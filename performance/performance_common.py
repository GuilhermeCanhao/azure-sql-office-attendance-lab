#!/usr/bin/env python3
"""Frozen workload oracle and acceptance rules for Phase 6 performance work."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


PERFORMANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERFORMANCE_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DatasetPlan, SafeLoaderError  # noqa: E402
from reporting_common import (  # noqa: E402
    ReportingExpectations,
    build_reporting_expectations,
)


CANDIDATE_INDEX = "IX_core_DailyAttendanceSummary_OfficeDateMethod"


@dataclass(frozen=True)
class BenchmarkWindow:
    name: str
    office_code: str
    from_date: str
    through_date: str
    expected_rows: Tuple[tuple, ...]
    expected_person_days: int
    expected_card: int
    expected_wifi: int
    expected_both: int


@dataclass(frozen=True)
class BenchmarkExpectations:
    primary: BenchmarkWindow
    regression: BenchmarkWindow


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _window(
    reporting: ReportingExpectations,
    name: str,
    office_code: str,
    from_date: str,
    through_date: str,
) -> BenchmarkWindow:
    rows = tuple(
        row
        for row in reporting.daily_trend
        if row[1] == office_code and from_date <= row[0] <= through_date
    )
    return BenchmarkWindow(
        name=name,
        office_code=office_code,
        from_date=from_date,
        through_date=through_date,
        expected_rows=rows,
        expected_person_days=sum(row[4] for row in rows),
        expected_card=sum(row[5] for row in rows),
        expected_wifi=sum(row[6] for row in rows),
        expected_both=sum(row[7] for row in rows),
    )


def build_benchmark_expectations(plan: DatasetPlan) -> BenchmarkExpectations:
    reporting = build_reporting_expectations(plan)
    expectations = BenchmarkExpectations(
        primary=_window(
            reporting,
            "PRIMARY_90_DAY",
            "PT-LAB-01",
            "2026-04-01",
            "2026-06-30",
        ),
        regression=_window(
            reporting,
            "FULL_HISTORY",
            "PT-LAB-01",
            "2025-07-01",
            "2026-06-30",
        ),
    )
    validate_benchmark_expectations(expectations)
    return expectations


def validate_benchmark_expectations(expectations: BenchmarkExpectations) -> dict:
    primary = expectations.primary
    regression = expectations.regression
    require(len(primary.expected_rows) == 65, "Primary benchmark row count changed.")
    require(primary.expected_person_days == 9195, "Primary person-day total changed.")
    require(primary.expected_card == 316, "Primary CARD total changed.")
    require(primary.expected_wifi == 2717, "Primary WIFI total changed.")
    require(primary.expected_both == 6162, "Primary BOTH total changed.")
    require(len(regression.expected_rows) == 261, "Regression row count changed.")
    require(regression.expected_person_days == 37151, "Regression person-day total changed.")
    require(regression.expected_card == 1236, "Regression CARD total changed.")
    require(regression.expected_wifi == 11082, "Regression WIFI total changed.")
    require(regression.expected_both == 24833, "Regression BOTH total changed.")
    return {
        "primary_rows": len(primary.expected_rows),
        "primary_person_days": primary.expected_person_days,
        "primary_card": primary.expected_card,
        "primary_wifi": primary.expected_wifi,
        "primary_both": primary.expected_both,
        "regression_rows": len(regression.expected_rows),
        "regression_person_days": regression.expected_person_days,
        "regression_card": regression.expected_card,
        "regression_wifi": regression.expected_wifi,
        "regression_both": regression.expected_both,
    }


def evaluate_candidate(
    baseline_primary_reads: int,
    candidate_primary_reads: int,
    baseline_regression_reads: int,
    candidate_regression_reads: int,
    candidate_used: bool,
) -> dict:
    require(baseline_primary_reads > 0, "Baseline primary logical reads must be positive.")
    require(baseline_regression_reads > 0, "Baseline regression logical reads must be positive.")
    require(candidate_primary_reads >= 0, "Candidate primary logical reads cannot be negative.")
    require(candidate_regression_reads >= 0, "Candidate regression logical reads cannot be negative.")
    reduction = (
        (baseline_primary_reads - candidate_primary_reads) / baseline_primary_reads
    ) * 100.0
    keep = (
        candidate_used
        and reduction >= 30.0
        and candidate_regression_reads <= baseline_regression_reads
    )
    return {
        "decision": "KEEP" if keep else "NO_CHANGE",
        "primary_read_reduction_percent": round(reduction, 2),
        "full_history_read_regression": candidate_regression_reads > baseline_regression_reads,
        "candidate_used": candidate_used,
    }
