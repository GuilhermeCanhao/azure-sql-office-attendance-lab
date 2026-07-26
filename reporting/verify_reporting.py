#!/usr/bin/env python3
"""Verify aggregate report views against independently generated expectations."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import List


REPORTING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REPORTING_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (REPORTING_DIR, LOADER_DIR):
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
from reporting_common import (  # noqa: E402
    RATE_QUANTUM,
    ReportingExpectations,
    build_reporting_expectations,
    validate_reporting_expectations,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Canonical generated-data directory (not printed).",
    )
    parser.add_argument(
        "--execute-verify",
        action="store_true",
        help="Acquire a short-lived Entra token and compare the deployed report views.",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _rows(connection, sql: str) -> List[tuple]:
    try:
        return [tuple(row) for row in connection.cursor().execute(sql).fetchall()]
    except Exception as exc:
        raise SafeLoaderError(
            "A reporting verification query failed; database details were suppressed."
        ) from exc


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
        raise SafeLoaderError("A reporting rate has an unexpected type.") from exc


def verify_report_views(connection, expected: ReportingExpectations) -> None:
    daily_actual = tuple(
        (
            _date_text(row[0]), str(row[1]), str(row[2]), int(row[3]),
            int(row[4]), int(row[5]), int(row[6]), int(row[7]), int(row[8]),
            int(row[9]), _decimal(row[10]),
        )
        for row in _rows(
            connection,
            "SELECT AttendanceDateLocal, OfficeCode, OfficeName, OfficeCapacity, "
            "PersonDayCount, CardOnlyPersonDays, WifiOnlyPersonDays, BothPersonDays, "
            "BadgeObservedPersonDays, WifiObservedPersonDays, OccupancyRate "
            "FROM report.vw_DailyAttendanceTrend "
            "ORDER BY AttendanceDateLocal, OfficeCode;",
        )
    )
    require(daily_actual == expected.daily_trend, "Daily attendance report differs from expectation.")

    department_actual = tuple(
        (
            _date_text(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
            int(row[5]), int(row[6]), int(row[7]), int(row[8]),
        )
        for row in _rows(
            connection,
            "SELECT AttendanceDateLocal, OfficeCode, OfficeName, DepartmentCode, "
            "DepartmentName, PersonDayCount, CardOnlyPersonDays, WifiOnlyPersonDays, "
            "BothPersonDays FROM report.vw_DailyDepartmentAttendance "
            "ORDER BY AttendanceDateLocal, OfficeCode, DepartmentCode;",
        )
    )
    require(
        department_actual == expected.daily_department,
        "Department attendance report differs from expectation.",
    )

    load_actual = tuple(
        (
            str(row[0]), int(row[1]), int(row[2]), int(row[3]), int(row[4]),
            int(row[5]), int(row[6]), int(row[7]), int(row[8]), _decimal(row[9]),
        )
        for row in _rows(
            connection,
            "SELECT SourceType, TerminalBatchCount, InProgressBatchCount, "
            "CompletedWithoutRejectsBatchCount, CompletedWithRejectsBatchCount, "
            "FailedBatchCount, RowsReceived, RowsAccepted, RowsRejected, AcceptanceRate "
            "FROM report.vw_LoadQualitySummary ORDER BY SourceType;",
        )
    )
    require(load_actual == expected.load_quality, "Load-quality report differs from expectation.")

    validation_actual = tuple(
        (str(row[0]), str(row[1]), int(row[2]))
        for row in _rows(
            connection,
            "SELECT SourceType, ValidationCode, RejectedRowCount "
            "FROM report.vw_ValidationIssueSummary ORDER BY SourceType, ValidationCode;",
        )
    )
    require(
        validation_actual == expected.validation_issues,
        "Validation-issue report differs from expectation.",
    )


def main() -> int:
    connection = None
    try:
        args = parse_args()
        plan = build_dataset_plan(args.data_dir)
        expectations = build_reporting_expectations(plan)
        result = validate_reporting_expectations(expectations, plan)
        if not args.execute_verify:
            print(
                "Reporting expectation verification: PASS "
                "DailyRows={daily_rows} DepartmentRows={department_rows} "
                "LoadRows={load_rows} ValidationRows={validation_rows}".format(**result)
            )
            print(
                "PersonDays={person_days} Received={received} Accepted={accepted} "
                "Rejected={rejected}".format(**result)
            )
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        server, database = runtime_target()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)
        verify_report_views(connection, expectations)
        print(
            "Reporting view verification: PASS "
            "DailyRows={daily_rows} DepartmentRows={department_rows} "
            "LoadRows={load_rows} ValidationRows={validation_rows}".format(**result)
        )
        print(
            "PersonDays={person_days} Received={received} Accepted={accepted} "
            "Rejected={rejected} UnexpectedFixtures=0".format(**result)
        )
        return 0
    except Exception as exc:
        print(f"Reporting verification: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
