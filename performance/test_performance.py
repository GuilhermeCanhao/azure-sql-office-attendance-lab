#!/usr/bin/env python3
"""Offline tests for the Phase 6 benchmark contract and safe evidence parsers."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


PERFORMANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERFORMANCE_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (PERFORMANCE_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, SafeLoaderError, build_dataset_plan  # noqa: E402
from performance_common import (  # noqa: E402
    build_benchmark_expectations,
    evaluate_candidate,
    validate_benchmark_expectations,
)
import benchmark_reporting  # noqa: E402


class PerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_dataset_plan(DEFAULT_DATA_DIR)
        cls.expected = build_benchmark_expectations(cls.plan)

    def test_frozen_windows_and_totals(self) -> None:
        self.assertEqual(
            validate_benchmark_expectations(self.expected),
            {
                "primary_rows": 65,
                "primary_person_days": 9195,
                "primary_card": 316,
                "primary_wifi": 2717,
                "primary_both": 6162,
                "regression_rows": 261,
                "regression_person_days": 37151,
                "regression_card": 1236,
                "regression_wifi": 11082,
                "regression_both": 24833,
            },
        )

    def test_business_query_is_parameterized_and_report_only(self) -> None:
        sql = benchmark_reporting.BENCHMARK_SQL
        self.assertIn("Phase6:DailyAttendanceTrend-v1", sql)
        self.assertIn("FROM report.vw_DailyAttendanceTrend", sql)
        self.assertEqual(sql.count("?"), 3)
        self.assertNotIn("FROM core.", sql)
        self.assertNotIn("FROM stage.", sql)
        create_sql = benchmark_reporting.CREATE_CANDIDATE_SQL.read_text(encoding="utf-8")
        remove_sql = benchmark_reporting.REMOVE_CANDIDATE_SQL.read_text(encoding="utf-8")
        self.assertIn("OfficeId,\n        AttendanceDateLocal,\n        DetectionMethod", create_sql)
        self.assertIn("DROP INDEX IX_core_DailyAttendanceSummary_OfficeDateMethod", remove_sql)
        self.assertNotIn("ALTER DATABASE", create_sql + remove_sql)
        self.assertNotIn("UPDATE ", create_sql + remove_sql)

    def test_statistics_parser_extracts_safe_metrics(self) -> None:
        messages = [
            (
                "01000",
                "Table 'DailyAttendanceSummary'. Scan count 1, logical reads 47, physical reads 0.\n"
                "Table 'Office'. Scan count 1, logical reads 2, physical reads 0.\n"
                " SQL Server Execution Times: CPU time = 4 ms, elapsed time = 7 ms.",
            )
        ]
        self.assertEqual(
            benchmark_reporting.parse_statistics_messages(messages),
            {
                "logical_reads": {"DailyAttendanceSummary": 47, "Office": 2},
                "cpu_ms": 4,
                "elapsed_ms": 7,
            },
        )
        self.assertEqual(
            benchmark_reporting._diagnostic_error_category(
                Exception("Invalid column name 'avg_cpu_time'. private details")
            ),
            "INCOMPATIBLE_COLUMN_AVG_CPU_TIME",
        )
        self.assertEqual(
            benchmark_reporting._suite_error_category(
                Exception("Synthetic suite failure 51804 with private details")
            ),
            "SQL_51804",
        )

    def test_plan_parser_returns_only_sanitized_evidence(self) -> None:
        plan = """<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">
          <BatchSequence><Batch><Statements><StmtSimple><QueryPlan>
            <RelOp PhysicalOp="Index Seek" LogicalOp="Index Seek">
              <IndexScan><Object Database="private-db" Schema="[core]" Table="[DailyAttendanceSummary]" Index="[PK_core_DailyAttendanceSummary]" /></IndexScan>
              <RunTimeInformation><RunTimeCountersPerThread ActualRows="65" ActualExecutions="1" /></RunTimeInformation>
            </RelOp>
          </QueryPlan></StmtSimple></Statements></Batch></BatchSequence>
        </ShowPlanXML>"""
        evidence = benchmark_reporting.parse_plan_evidence(plan)
        self.assertEqual(evidence["operators"], {"Index_Seek": 1})
        self.assertEqual(
            evidence["access_paths"],
            (("[core]", "[DailyAttendanceSummary]", "[PK_core_DailyAttendanceSummary]"),),
        )
        self.assertNotIn("private-db", str(evidence))
        self.assertEqual(evidence["runtime_rows_across_operators"], 65)

    def test_exact_result_drift_is_rejected(self) -> None:
        rows = [list(row) for row in self.expected.primary.expected_rows]
        rows[0][4] += 1
        with self.assertRaisesRegex(SafeLoaderError, "benchmark result drifted"):
            benchmark_reporting.verify_window_rows(rows, self.expected.primary)

    def test_candidate_requires_all_acceptance_conditions(self) -> None:
        self.assertEqual(
            evaluate_candidate(100, 60, 300, 280, True)["decision"], "KEEP"
        )
        self.assertEqual(
            evaluate_candidate(100, 71, 300, 280, True)["decision"], "NO_CHANGE"
        )
        self.assertEqual(
            evaluate_candidate(100, 60, 300, 301, True)["decision"], "NO_CHANGE"
        )
        self.assertEqual(
            evaluate_candidate(100, 60, 300, 280, False)["decision"], "NO_CHANGE"
        )
        self.assertTrue(
            benchmark_reporting._candidate_used(
                {
                    "access_paths": (
                        ("[core]", "[DailyAttendanceSummary]", f"[{benchmark_reporting.CANDIDATE_INDEX}]"),
                    )
                }
            )
        )
        self.assertFalse(
            benchmark_reporting._candidate_used(
                {"access_paths": (("[core]", "[DailyAttendanceSummary]", "[PK_summary]"),)}
            )
        )

    def test_default_mode_does_not_request_runtime_target(self) -> None:
        with patch.object(sys, "argv", ["benchmark_reporting.py"]):
            with patch.object(benchmark_reporting, "runtime_target") as runtime:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(benchmark_reporting.main(), 0)
        runtime.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_alert_workload_is_bounded_and_reuses_verified_window(self) -> None:
        with patch.object(benchmark_reporting, "execute_window") as execute:
            result = benchmark_reporting.execute_alert_workload(
                object(), self.expected, 3
            )
        self.assertEqual(execute.call_count, 3)
        execute.assert_called_with(
            unittest.mock.ANY, self.expected.primary, collect_statistics=False
        )
        self.assertEqual(
            result["verified_rows"], 3 * len(self.expected.primary.expected_rows)
        )
        with self.assertRaisesRegex(SafeLoaderError, "outside its safe bound"):
            benchmark_reporting.execute_alert_workload(object(), self.expected, 0)


if __name__ == "__main__":
    unittest.main()
