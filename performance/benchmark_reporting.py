#!/usr/bin/env python3
"""Offline-default benchmark for the Phase 6 aggregate reporting workload."""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PERFORMANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERFORMANCE_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (PERFORMANCE_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    DEFAULT_DATA_DIR,
    SafeLoaderError,
    acquire_azure_sql_token,
    build_dataset_plan,
    connect_ready_target,
    runtime_target,
    safe_main_error,
)
from verify_loaded_data import verify_database  # noqa: E402
from performance_common import (  # noqa: E402
    CANDIDATE_INDEX,
    BenchmarkExpectations,
    BenchmarkWindow,
    build_benchmark_expectations,
    evaluate_candidate,
    validate_benchmark_expectations,
)
from reporting_common import RATE_QUANTUM, build_reporting_expectations  # noqa: E402
from verify_reporting import verify_report_views  # noqa: E402


BENCHMARK_TAG = "Phase6:DailyAttendanceTrend-v1"
BENCHMARK_SQL = """/* Phase6:DailyAttendanceTrend-v1 */
SELECT
    AttendanceDateLocal,
    OfficeCode,
    OfficeName,
    OfficeCapacity,
    PersonDayCount,
    CardOnlyPersonDays,
    WifiOnlyPersonDays,
    BothPersonDays,
    BadgeObservedPersonDays,
    WifiObservedPersonDays,
    OccupancyRate
FROM report.vw_DailyAttendanceTrend
WHERE OfficeCode = ?
  AND AttendanceDateLocal >= ?
  AND AttendanceDateLocal <= ?
ORDER BY AttendanceDateLocal;"""

WARMUP_RUNS = 2
MEASURED_RUNS = 10
BASELINE_PRIMARY_READS = 169
BASELINE_REGRESSION_READS = 672
CREATE_CANDIDATE_SQL = PROJECT_ROOT / "sql" / "014_create_performance_candidate.sql"
REMOVE_CANDIDATE_SQL = PROJECT_ROOT / "sql" / "015_remove_performance_candidate.sql"
SUMMARY_REFRESH_SUITE = PROJECT_ROOT / "tests" / "010_verify_daily_summary_refresh.sql"
REPORTING_SUITE = PROJECT_ROOT / "tests" / "013_verify_reporting_views.sql"

TABLE_IO_RE = re.compile(
    r"Table\s+'([^']+)'\..*?logical reads\s+(\d+)", re.IGNORECASE
)
EXECUTION_TIME_RE = re.compile(
    r"SQL Server Execution Times:\s*CPU time =\s*(\d+)\s*ms,\s*elapsed time =\s*(\d+)\s*ms",
    re.IGNORECASE,
)
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.$#@\[\]-]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute-baseline",
        action="store_true",
        help="Run the approved read-only Azure SQL baseline workload.",
    )
    mode.add_argument(
        "--execute-candidate",
        action="store_true",
        help="Run the approved reversible candidate-index experiment.",
    )
    mode.add_argument(
        "--execute-alert-workload",
        action="store_true",
        help=(
            "Run a bounded read-only copy of the verified reporting query for the "
            "Phase 7 metric-alert proof."
        ),
    )
    parser.add_argument(
        "--alert-workload-iterations",
        type=int,
        default=500,
        help="Verified query executions for alert-workload mode (1 through 5000).",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _message_text(messages: Iterable[object]) -> str:
    parts: List[str] = []
    for item in messages:
        if isinstance(item, tuple):
            parts.extend(str(part) for part in item)
        else:
            parts.append(str(item))
    return "\n".join(parts)


def parse_statistics_messages(messages: Iterable[object]) -> dict:
    text = _message_text(messages)
    logical_reads: Counter[str] = Counter()
    for table_name, reads in TABLE_IO_RE.findall(text):
        safe_name = table_name if SAFE_IDENTIFIER_RE.fullmatch(table_name) else "SUPPRESSED"
        logical_reads[safe_name] += int(reads)
    execution_matches = EXECUTION_TIME_RE.findall(text)
    cpu_ms: Optional[int] = None
    elapsed_ms: Optional[int] = None
    if execution_matches:
        cpu_ms, elapsed_ms = (int(value) for value in execution_matches[-1])
    return {
        "logical_reads": dict(sorted(logical_reads.items())),
        "cpu_ms": cpu_ms,
        "elapsed_ms": elapsed_ms,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_identifier(value: Optional[str]) -> str:
    if not value:
        return ""
    return value if SAFE_IDENTIFIER_RE.fullmatch(value) else "SUPPRESSED"


def parse_plan_evidence(plan_xml: str) -> dict:
    try:
        root = ET.fromstring(plan_xml)
    except ET.ParseError as exc:
        raise SafeLoaderError("Actual-plan XML could not be parsed safely.") from exc

    operators: Counter[str] = Counter()
    access_paths = set()
    actual_rows = 0
    actual_executions = 0
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "RelOp":
            operator = element.attrib.get("PhysicalOp", "UNKNOWN")
            operators[_safe_identifier(operator.replace(" ", "_"))] += 1
        elif name == "Object":
            schema = _safe_identifier(element.attrib.get("Schema"))
            table = _safe_identifier(element.attrib.get("Table"))
            index = _safe_identifier(element.attrib.get("Index"))
            access_paths.add((schema, table, index))
        elif name == "RunTimeCountersPerThread":
            actual_rows += int(element.attrib.get("ActualRows", "0"))
            actual_executions += int(element.attrib.get("ActualExecutions", "0"))

    return {
        "operators": dict(sorted(operators.items())),
        "access_paths": tuple(sorted(access_paths)),
        "runtime_rows_across_operators": actual_rows,
        "runtime_executions_across_operators": actual_executions,
    }


def _date_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _decimal(value) -> Decimal:
    try:
        return Decimal(value).quantize(RATE_QUANTUM)
    except Exception as exc:
        raise SafeLoaderError("A benchmark rate has an unexpected type.") from exc


def normalize_rows(rows: Sequence[Sequence[object]]) -> Tuple[tuple, ...]:
    return tuple(
        (
            _date_text(row[0]), str(row[1]), str(row[2]), int(row[3]),
            int(row[4]), int(row[5]), int(row[6]), int(row[7]), int(row[8]),
            int(row[9]), _decimal(row[10]),
        )
        for row in rows
    )


def verify_window_rows(rows: Sequence[Sequence[object]], window: BenchmarkWindow) -> None:
    normalized = normalize_rows(rows)
    require(normalized == window.expected_rows, f"{window.name} benchmark result drifted.")


def _cursor_messages(cursor) -> List[object]:
    return list(getattr(cursor, "messages", ()) or ())


def execute_window(connection, window: BenchmarkWindow, collect_statistics: bool) -> dict:
    cursor = connection.cursor()
    sql = BENCHMARK_SQL
    if collect_statistics:
        sql = (
            "SET STATISTICS IO ON; SET STATISTICS TIME ON;\n"
            + sql
            + "\nSET STATISTICS IO OFF; SET STATISTICS TIME OFF;"
        )
    started = time.perf_counter()
    try:
        cursor.execute(sql, window.office_code, window.from_date, window.through_date)
        rows = cursor.fetchall()
        messages = _cursor_messages(cursor)
        while cursor.nextset():
            messages.extend(_cursor_messages(cursor))
    except Exception as exc:
        raise SafeLoaderError("The benchmark query failed; database details were suppressed.") from exc
    client_elapsed_ms = (time.perf_counter() - started) * 1000.0
    verify_window_rows(rows, window)
    parsed = parse_statistics_messages(messages) if collect_statistics else {
        "logical_reads": {}, "cpu_ms": None, "elapsed_ms": None
    }
    parsed["client_elapsed_ms"] = round(client_elapsed_ms, 3)
    return parsed


def capture_actual_plan(connection, window: BenchmarkWindow) -> dict:
    cursor = connection.cursor()
    sql = "SET STATISTICS XML ON;\n" + BENCHMARK_SQL + "\nSET STATISTICS XML OFF;"
    plan_xml: Optional[str] = None
    try:
        cursor.execute(sql, window.office_code, window.from_date, window.through_date)
        rows = cursor.fetchall()
        verify_window_rows(rows, window)
        while cursor.nextset():
            if cursor.description:
                candidate = cursor.fetchone()
                if candidate and isinstance(candidate[0], str) and "ShowPlanXML" in candidate[0]:
                    plan_xml = candidate[0]
    except Exception as exc:
        raise SafeLoaderError("Actual-plan capture failed; database details were suppressed.") from exc
    require(plan_xml is not None, "Actual-plan output was not returned.")
    return parse_plan_evidence(plan_xml)


def _diagnostic_error_category(exc: BaseException) -> str:
    text = " ".join(str(part) for part in getattr(exc, "args", ())).lower()
    compatible_columns = (
        "count_executions",
        "avg_duration",
        "avg_cpu_time",
        "avg_logical_io_reads",
        "last_execution_time",
        "plan_id",
    )
    if "invalid column name" in text:
        for column in compatible_columns:
            if column in text:
                return f"INCOMPATIBLE_COLUMN_{column.upper()}"
        return "INCOMPATIBLE_COLUMN"
    if "arithmetic overflow" in text:
        return "ARITHMETIC_OVERFLOW"
    if "operand type clash" in text or "conversion failed" in text:
        return "TYPE_CONVERSION"
    if "permission" in text or "denied" in text:
        return "PERMISSION"
    if "syntax" in text:
        return "SYNTAX"
    return "UNCLASSIFIED"


def _suite_error_category(exc: BaseException) -> str:
    text = " ".join(str(part) for part in getattr(exc, "args", ()))
    known_error = re.search(r"\b(5180[0-6]|5200[0-9]|5201[0-2])\b", text)
    if known_error:
        return f"SQL_{known_error.group(1)}"
    lowered = text.lower()
    if "transaction" in lowered:
        return "TRANSACTION_STATE"
    if "timeout" in lowered:
        return "TIMEOUT"
    return "UNCLASSIFIED"


def _rows(
    connection, sql: str, *params, diagnostic: str = "Diagnostic catalog"
) -> List[tuple]:
    try:
        return [tuple(row) for row in connection.cursor().execute(sql, *params).fetchall()]
    except Exception as exc:
        category = _diagnostic_error_category(exc)
        raise SafeLoaderError(
            f"{diagnostic} query failed ({category}); database details were suppressed."
        ) from exc


def inspect_baseline_state(connection) -> dict:
    index_rows = _rows(
        connection,
        "SELECT indexes.name, indexes.type_desc, indexes.is_unique, indexes.is_disabled "
        "FROM sys.indexes AS indexes "
        "WHERE indexes.object_id = OBJECT_ID(N'core.DailyAttendanceSummary') "
        "AND indexes.index_id > 0 ORDER BY indexes.index_id;",
        diagnostic="Index inventory",
    )
    require(
        not any(str(row[0]) == CANDIDATE_INDEX for row in index_rows),
        "The candidate index already exists; baseline state is not clean.",
    )
    query_store_rows = _rows(
        connection,
        "SELECT actual_state_desc, desired_state_desc, query_capture_mode_desc, "
        "current_storage_size_mb, max_storage_size_mb, readonly_reason "
        "FROM sys.database_query_store_options;",
        diagnostic="Query Store options",
    )
    require(len(query_store_rows) == 1, "Query Store options were not available.")
    query_store = query_store_rows[0]
    return {
        "summary_index_count": len(index_rows),
        "query_store_actual_state": str(query_store[0]),
        "query_store_desired_state": str(query_store[1]),
        "query_store_capture_mode": str(query_store[2]),
        "query_store_current_mb": float(query_store[3]),
        "query_store_max_mb": int(query_store[4]),
        "query_store_readonly_reason": int(query_store[5]),
    }


def inspect_candidate_state(connection) -> dict:
    rows = _rows(
        connection,
        "SELECT columns.name, index_columns.key_ordinal, "
        "index_columns.is_included_column, indexes.is_unique, indexes.is_disabled "
        "FROM sys.indexes AS indexes "
        "INNER JOIN sys.index_columns AS index_columns "
        "ON index_columns.object_id = indexes.object_id "
        "AND index_columns.index_id = indexes.index_id "
        "INNER JOIN sys.columns AS columns "
        "ON columns.object_id = index_columns.object_id "
        "AND columns.column_id = index_columns.column_id "
        "WHERE indexes.object_id = OBJECT_ID(N'core.DailyAttendanceSummary') "
        "AND indexes.name = ? "
        "ORDER BY index_columns.key_ordinal, index_columns.index_column_id;",
        CANDIDATE_INDEX,
        diagnostic="Candidate definition",
    )
    expected = [
        ("OfficeId", 1, False, False, False),
        ("AttendanceDateLocal", 2, False, False, False),
        ("DetectionMethod", 3, False, False, False),
    ]
    normalized = [
        (str(row[0]), int(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
        for row in rows
    ]
    require(normalized == expected, "The candidate index definition is not exact.")
    page_rows = _rows(
        connection,
        "SELECT COALESCE(SUM(partitions.reserved_page_count), 0) "
        "FROM sys.dm_db_partition_stats AS partitions "
        "INNER JOIN sys.indexes AS indexes "
        "ON indexes.object_id = partitions.object_id "
        "AND indexes.index_id = partitions.index_id "
        "WHERE indexes.object_id = OBJECT_ID(N'core.DailyAttendanceSummary') "
        "AND indexes.name = ?;",
        CANDIDATE_INDEX,
        diagnostic="Candidate storage",
    )
    return {"candidate_pages": int(page_rows[0][0])}


def _execute_sql_file(connection, path: Path, purpose: str) -> None:
    try:
        sql = path.read_text(encoding="utf-8")
        connection.cursor().execute(sql)
    except Exception as exc:
        category = _suite_error_category(exc)
        raise SafeLoaderError(
            f"{purpose} failed ({category}); database details were suppressed."
        ) from exc


def _run_behavior_suite(
    connection,
    path: Path,
    marker_column: str,
    expected: Mapping[str, object],
    purpose: str,
) -> None:
    cursor = connection.cursor()
    observed: Optional[dict] = None
    try:
        cursor.execute(path.read_text(encoding="utf-8"))
        while True:
            if cursor.description:
                columns = [str(item[0]) for item in cursor.description]
                rows = cursor.fetchall()
                if marker_column in columns:
                    require(len(rows) == 1, "A behavior suite returned unexpected rows.")
                    observed = dict(zip(columns, rows[0]))
            if not cursor.nextset():
                break
    except SafeLoaderError:
        raise
    except Exception as exc:
        category = _suite_error_category(exc)
        raise SafeLoaderError(
            f"{purpose} failed ({category}); database details were suppressed."
        ) from exc
    require(observed is not None, "A live regression result was not returned.")
    for name, value in expected.items():
        require(observed.get(name) == value, f"Live regression check {name} failed.")


def _candidate_used(plan: Mapping[str, object]) -> bool:
    access_paths = plan.get("access_paths", ())
    return any(
        len(path) >= 3 and str(path[2]).strip("[]") == CANDIDATE_INDEX
        for path in access_paths  # type: ignore[union-attr]
    )


def inspect_query_store_measurement(connection, started_at: datetime) -> dict:
    rows = _rows(
        connection,
        "SELECT COALESCE(SUM(rs.count_executions), 0), "
        "CASE WHEN SUM(rs.count_executions) > 0 "
        "THEN SUM(rs.avg_duration * rs.count_executions) / SUM(rs.count_executions) END, "
        "CASE WHEN SUM(rs.count_executions) > 0 "
        "THEN SUM(rs.avg_cpu_time * rs.count_executions) / SUM(rs.count_executions) END, "
        "CASE WHEN SUM(rs.count_executions) > 0 "
        "THEN SUM(rs.avg_logical_io_reads * rs.count_executions) / SUM(rs.count_executions) END, "
        "COUNT(DISTINCT qsp.plan_id) "
        "FROM sys.query_store_query_text AS qst "
        "INNER JOIN sys.query_store_query AS qsq ON qsq.query_text_id = qst.query_text_id "
        "INNER JOIN sys.query_store_plan AS qsp ON qsp.query_id = qsq.query_id "
        "INNER JOIN sys.query_store_runtime_stats AS rs ON rs.plan_id = qsp.plan_id "
        "WHERE qst.query_sql_text LIKE ? AND rs.last_execution_time >= ?;",
        f"%{BENCHMARK_TAG}%",
        started_at,
        diagnostic="Query Store measurement",
    )
    row = rows[0]
    return {
        "capture_observed": int(row[0]) > 0,
        "execution_count": int(row[0]),
        "average_duration_microseconds": None if row[1] is None else float(row[1]),
        "average_cpu_microseconds": None if row[2] is None else float(row[2]),
        "average_logical_reads": None if row[3] is None else float(row[3]),
        "plan_count": int(row[4]),
    }


def summarize_runs(runs: Sequence[Mapping[str, object]]) -> dict:
    require(len(runs) == MEASURED_RUNS, "Measured-run count is incorrect.")
    summary_reads = [
        int(run["logical_reads"].get("DailyAttendanceSummary", 0))  # type: ignore[union-attr]
        for run in runs
    ]
    require(all(value > 0 for value in summary_reads), "Summary-table logical reads were not captured.")
    client_times = [float(run["client_elapsed_ms"]) for run in runs]
    sql_cpu = [int(run["cpu_ms"]) for run in runs if run["cpu_ms"] is not None]
    sql_elapsed = [int(run["elapsed_ms"]) for run in runs if run["elapsed_ms"] is not None]
    return {
        "runs": len(runs),
        "summary_logical_reads_median": int(statistics.median(summary_reads)),
        "summary_logical_reads_min": min(summary_reads),
        "summary_logical_reads_max": max(summary_reads),
        "client_elapsed_ms_median": round(statistics.median(client_times), 3),
        "sql_cpu_ms_median": None if not sql_cpu else statistics.median(sql_cpu),
        "sql_elapsed_ms_median": None if not sql_elapsed else statistics.median(sql_elapsed),
    }


def execute_measurement(connection, expectations: BenchmarkExpectations) -> dict:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    for _ in range(WARMUP_RUNS):
        execute_window(connection, expectations.primary, collect_statistics=False)
    runs = [
        execute_window(connection, expectations.primary, collect_statistics=True)
        for _ in range(MEASURED_RUNS)
    ]
    regression = execute_window(connection, expectations.regression, collect_statistics=True)
    plan = capture_actual_plan(connection, expectations.primary)
    query_store = inspect_query_store_measurement(connection, started_at)
    return {
        "primary": summarize_runs(runs),
        "regression": regression,
        "plan": plan,
        "query_store": query_store,
    }


def execute_baseline(connection, expectations: BenchmarkExpectations) -> dict:
    state = inspect_baseline_state(connection)
    result = execute_measurement(connection, expectations)
    result["state"] = state
    return result


def execute_alert_workload(
    connection, expectations: BenchmarkExpectations, iterations: int
) -> dict:
    """Exercise only the frozen aggregate query; tolerate the retained Phase 6 index."""
    require(1 <= iterations <= 5000, "Alert-workload iteration count is outside its safe bound.")
    started = time.perf_counter()
    for _ in range(iterations):
        execute_window(connection, expectations.primary, collect_statistics=False)
    return {
        "iterations": iterations,
        "verified_rows": iterations * len(expectations.primary.expected_rows),
        "client_elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def execute_candidate_experiment(connection, plan, expectations: BenchmarkExpectations) -> dict:
    initial_state = inspect_baseline_state(connection)
    candidate_present = False
    try:
        _execute_sql_file(connection, CREATE_CANDIDATE_SQL, "Candidate creation")
        candidate_present = True
        candidate_state = inspect_candidate_state(connection)
        result = execute_measurement(connection, expectations)
        primary_reads = int(result["primary"]["summary_logical_reads_median"])
        regression_reads = int(
            result["regression"]["logical_reads"].get("DailyAttendanceSummary", 0)
        )
        decision = evaluate_candidate(
            BASELINE_PRIMARY_READS,
            primary_reads,
            BASELINE_REGRESSION_READS,
            regression_reads,
            _candidate_used(result["plan"]),
        )
        result["decision"] = decision
        result["candidate_state"] = candidate_state
        result["initial_state"] = initial_state

        if decision["decision"] != "KEEP":
            _execute_sql_file(connection, REMOVE_CANDIDATE_SQL, "Candidate cleanup")
            candidate_present = False
            inspect_baseline_state(connection)
            return result

        _run_behavior_suite(
            connection,
            SUMMARY_REFRESH_SUITE,
            "FullReconciliation",
            {
                "FullReconciliation": "PASS",
                "RangeReplacement": "PASS",
                "InvalidRangeRejection": "PASS",
                "TransactionRollback": "PASS",
                "FixtureCleanup": "PASS",
            },
            "Daily-summary live regression",
        )
        _run_behavior_suite(
            connection,
            REPORTING_SUITE,
            "ExactMetadata",
            {
                "ExactMetadata": "PASS",
                "AggregateFormulas": "PASS",
                "ReporterPositiveViews": 4,
                "ReporterExpectedDenials": 3,
                "TransactionRollback": "PASS",
                "FixtureCleanup": "PASS",
            },
            "Reporting live regression",
        )
        verify_report_views(connection, build_reporting_expectations(plan))
        verify_database(connection, plan)
        inspect_candidate_state(connection)
        candidate_present = False
        result["regressions"] = "PASS"
        return result
    except Exception as exc:
        if candidate_present:
            try:
                _execute_sql_file(connection, REMOVE_CANDIDATE_SQL, "Candidate cleanup")
                inspect_baseline_state(connection)
            except Exception as cleanup_exc:
                raise SafeLoaderError(
                    "The candidate experiment failed and automatic cleanup could not be verified."
                ) from cleanup_exc
        raise exc


def main() -> int:
    connection = None
    try:
        args = parse_args()
        plan = build_dataset_plan(args.data_dir)
        expectations = build_benchmark_expectations(plan)
        contract = validate_benchmark_expectations(expectations)
        if (
            not args.execute_baseline
            and not args.execute_candidate
            and not args.execute_alert_workload
        ):
            print(
                "Performance benchmark contract: PASS "
                "PrimaryRows={primary_rows} PrimaryPersonDays={primary_person_days} "
                "RegressionRows={regression_rows} RegressionPersonDays={regression_person_days}".format(
                    **contract
                )
            )
            print(
                "PrimaryWIFI={primary_wifi} PrimaryBOTH={primary_both} "
                "Warmups=2 MeasuredRuns=10 CandidateDecision=DEFERRED".format(**contract)
            )
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        server, database = runtime_target()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)

        if args.execute_alert_workload:
            result = execute_alert_workload(
                connection, expectations, args.alert_workload_iterations
            )
            print(
                "Performance alert workload: PASS "
                "Iterations={iterations} VerifiedRows={verified_rows} "
                "ClientElapsedSeconds={client_elapsed_seconds} ReadOnly=YES".format(
                    **result
                )
            )
            return 0

        if args.execute_candidate:
            result = execute_candidate_experiment(connection, plan, expectations)
            primary = result["primary"]
            regression_reads = result["regression"]["logical_reads"].get(
                "DailyAttendanceSummary", 0
            )
            decision = result["decision"]
            print(
                "Performance candidate: PASS Runs={runs} "
                "SummaryReadsMedian={summary_logical_reads_median} "
                "SummaryReadsRange={summary_logical_reads_min}-{summary_logical_reads_max} "
                "ClientElapsedMsMedian={client_elapsed_ms_median} "
                "SqlCpuMsMedian={sql_cpu_ms_median} "
                "SqlElapsedMsMedian={sql_elapsed_ms_median}".format(**primary)
            )
            print(
                f"FullHistorySummaryReads={regression_reads} "
                f"PrimaryReadReductionPercent={decision['primary_read_reduction_percent']} "
                f"CandidateUsed={'YES' if decision['candidate_used'] else 'NO'} "
                f"CandidatePages={result['candidate_state']['candidate_pages']} "
                f"Decision={decision['decision']}"
            )
            operators = ",".join(
                f"{name}:{count}" for name, count in result["plan"]["operators"].items()
            )
            access_paths = ",".join(
                ".".join(part for part in path if part)
                for path in result["plan"]["access_paths"]
            )
            print(
                f"PlanOperators={operators or 'NONE'} "
                f"AccessPaths={access_paths or 'NONE'}"
            )
            if decision["decision"] == "KEEP":
                print("Regressions=PASS CandidateState=RETAINED")
            else:
                print("Regressions=NOT_REQUIRED CandidateState=REMOVED")
            return 0

        result = execute_baseline(connection, expectations)
        primary = result["primary"]
        regression_reads = result["regression"]["logical_reads"].get(
            "DailyAttendanceSummary", 0
        )
        print(
            "Performance baseline: PASS Runs={runs} SummaryReadsMedian={summary_logical_reads_median} "
            "SummaryReadsRange={summary_logical_reads_min}-{summary_logical_reads_max} "
            "ClientElapsedMsMedian={client_elapsed_ms_median} "
            "SqlCpuMsMedian={sql_cpu_ms_median} "
            "SqlElapsedMsMedian={sql_elapsed_ms_median}".format(**primary)
        )
        print(
            f"FullHistorySummaryReads={regression_reads} "
            f"QueryStoreCapture={'OBSERVED' if result['query_store']['capture_observed'] else 'NOT_OBSERVED'} "
            f"CandidateDecision=DEFERRED"
        )
        operators = ",".join(
            f"{name}:{count}" for name, count in result["plan"]["operators"].items()
        )
        access_paths = ",".join(
            ".".join(part for part in path if part)
            for path in result["plan"]["access_paths"]
        )
        print(
            f"PlanOperators={operators or 'NONE'} "
            f"AccessPaths={access_paths or 'NONE'}"
        )
        print(
            f"QueryStoreState={result['state']['query_store_actual_state']} "
            f"QueryStoreCaptureMode={result['state']['query_store_capture_mode']} "
            f"ExistingSummaryIndexes={result['state']['summary_index_count']}"
        )
        return 0
    except Exception as exc:
        print(f"Performance benchmark: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
