#!/usr/bin/env python3
"""Offline tests for aggregate reporting expectations and verification."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPORTING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REPORTING_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (REPORTING_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, SafeLoaderError, build_dataset_plan  # noqa: E402
from reporting_common import build_reporting_expectations, validate_reporting_expectations  # noqa: E402
import verify_reporting  # noqa: E402


class FakeCursor:
    def __init__(self, rows_by_view):
        self.rows_by_view = rows_by_view
        self.current = []

    def execute(self, sql):
        for view_name, rows in self.rows_by_view.items():
            if view_name in sql:
                self.current = rows
                return self
        raise AssertionError("Verifier queried an unapproved object.")

    def fetchall(self):
        return self.current


class FakeConnection:
    def __init__(self, rows_by_view):
        self.rows_by_view = rows_by_view

    def cursor(self):
        return FakeCursor(self.rows_by_view)


class ReportingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_dataset_plan(DEFAULT_DATA_DIR)
        cls.expected = build_reporting_expectations(cls.plan)

    def test_canonical_reporting_inventory_and_totals(self) -> None:
        self.assertEqual(
            validate_reporting_expectations(self.expected, self.plan),
            {
                "daily_rows": 261,
                "department_rows": 2087,
                "load_rows": 2,
                "validation_rows": 8,
                "person_days": 37151,
                "received": 134372,
                "accepted": 133892,
                "rejected": 480,
            },
        )

    def test_source_quality_keeps_in_progress_and_failed_visible(self) -> None:
        for row in self.expected.load_quality:
            self.assertEqual(row[1], 12)
            self.assertEqual(row[2], 0)
            self.assertEqual(row[3], 0)
            self.assertEqual(row[4], 12)
            self.assertEqual(row[5], 0)

    def test_database_verifier_queries_only_report_schema(self) -> None:
        source = Path(verify_reporting.__file__).read_text(encoding="utf-8")
        self.assertIn("FROM report.vw_DailyAttendanceTrend", source)
        self.assertNotIn("FROM core.", source)
        self.assertNotIn("FROM stage.", source)

    def test_mocked_exact_report_views_pass(self) -> None:
        connection = FakeConnection(
            {
                "vw_DailyAttendanceTrend": self.expected.daily_trend,
                "vw_DailyDepartmentAttendance": self.expected.daily_department,
                "vw_LoadQualitySummary": self.expected.load_quality,
                "vw_ValidationIssueSummary": self.expected.validation_issues,
            }
        )
        verify_reporting.verify_report_views(connection, self.expected)

    def test_mocked_report_drift_is_rejected(self) -> None:
        drifted = list(self.expected.daily_trend)
        row = list(drifted[0])
        row[4] += 1
        drifted[0] = tuple(row)
        connection = FakeConnection(
            {
                "vw_DailyAttendanceTrend": tuple(drifted),
                "vw_DailyDepartmentAttendance": self.expected.daily_department,
                "vw_LoadQualitySummary": self.expected.load_quality,
                "vw_ValidationIssueSummary": self.expected.validation_issues,
            }
        )
        with self.assertRaisesRegex(SafeLoaderError, "Daily attendance report differs"):
            verify_reporting.verify_report_views(connection, self.expected)

    def test_default_mode_is_offline(self) -> None:
        with patch.object(sys, "argv", ["verify_reporting.py"]):
            with patch.object(verify_reporting, "runtime_target") as runtime:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(verify_reporting.main(), 0)
        runtime.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())


if __name__ == "__main__":
    unittest.main()
