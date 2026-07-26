#!/usr/bin/env python3
"""Independent expected-output builder for the aggregate reporting contract."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Tuple


REPORTING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REPORTING_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
if str(LOADER_DIR) not in sys.path:
    sys.path.insert(0, str(LOADER_DIR))

from loader_common import (  # noqa: E402
    EXPECTED_DAILY_HEADER,
    DatasetPlan,
    SafeLoaderError,
    read_csv_rows,
)


RATE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class ReportingExpectations:
    daily_trend: Tuple[tuple, ...]
    daily_department: Tuple[tuple, ...]
    load_quality: Tuple[tuple, ...]
    validation_issues: Tuple[tuple, ...]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def decimal_rate(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.000000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATE_QUANTUM, rounding=ROUND_HALF_UP
    )


def build_reporting_expectations(plan: DatasetPlan) -> ReportingExpectations:
    offices = plan.reference_rows["offices.csv"]
    require(len(offices) == 1, "Version 1 reporting expectations require one fictional office.")
    office = offices[0]
    office_code = office["office_code"]
    office_name = office["display_name"]
    office_capacity = int(office["capacity"])

    people_to_department = {
        row["personnel_code"]: row["department_code"]
        for row in plan.reference_rows["people.csv"]
    }
    departments = {
        row["department_code"]: row["department_name"]
        for row in plan.reference_rows["departments.csv"]
    }

    by_day: Dict[str, Counter[str]] = defaultdict(Counter)
    by_department_day: Dict[Tuple[str, str], Counter[str]] = defaultdict(Counter)
    expected_daily = read_csv_rows(plan.expected_daily_path, EXPECTED_DAILY_HEADER)
    for row in expected_daily:
        personnel_code = row["personnel_code"]
        department_code = people_to_department.get(personnel_code)
        require(department_code is not None, "Expected daily row references an unknown person.")
        method = row["detection_method"]
        require(method in {"CARD", "WIFI", "BOTH"}, "Expected daily method is unsupported.")
        attendance_date = row["attendance_date_local"]
        by_day[attendance_date][method] += 1
        by_department_day[(attendance_date, department_code)][method] += 1

    daily_trend: List[tuple] = []
    for attendance_date, counts in sorted(by_day.items()):
        person_days = sum(counts.values())
        daily_trend.append(
            (
                attendance_date,
                office_code,
                office_name,
                office_capacity,
                person_days,
                counts["CARD"],
                counts["WIFI"],
                counts["BOTH"],
                counts["CARD"] + counts["BOTH"],
                counts["WIFI"] + counts["BOTH"],
                decimal_rate(person_days, office_capacity),
            )
        )

    daily_department: List[tuple] = []
    for (attendance_date, department_code), counts in sorted(by_department_day.items()):
        require(department_code in departments, "Expected department is missing from references.")
        daily_department.append(
            (
                attendance_date,
                office_code,
                office_name,
                department_code,
                departments[department_code],
                sum(counts.values()),
                counts["CARD"],
                counts["WIFI"],
                counts["BOTH"],
            )
        )

    by_source: Dict[str, dict] = defaultdict(
        lambda: {
            "terminal": 0,
            "in_progress": 0,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "received": 0,
            "accepted": 0,
            "rejected": 0,
        }
    )
    for batch in plan.batches:
        totals = by_source[batch.source_type]
        totals["terminal"] += 1
        totals["partial" if batch.rows_rejected else "completed"] += 1
        totals["received"] += batch.rows_received
        totals["accepted"] += batch.rows_accepted
        totals["rejected"] += batch.rows_rejected

    load_quality: List[tuple] = []
    for source_type, totals in sorted(by_source.items()):
        load_quality.append(
            (
                source_type,
                totals["terminal"],
                totals["in_progress"],
                totals["completed"],
                totals["partial"],
                totals["failed"],
                totals["received"],
                totals["accepted"],
                totals["rejected"],
                decimal_rate(totals["accepted"], totals["received"]),
            )
        )

    validation_counts: Counter[Tuple[str, str]] = Counter()
    for row in plan.expected_validations:
        validation_counts[(row["source_type"], row["validation_code"])] += int(
            row["expected_count"]
        )
    validation_issues = tuple(
        (source_type, validation_code, count)
        for (source_type, validation_code), count in sorted(validation_counts.items())
    )

    expectations = ReportingExpectations(
        daily_trend=tuple(daily_trend),
        daily_department=tuple(daily_department),
        load_quality=tuple(load_quality),
        validation_issues=validation_issues,
    )
    validate_reporting_expectations(expectations, plan)
    return expectations


def validate_reporting_expectations(
    expectations: ReportingExpectations, plan: DatasetPlan
) -> dict:
    totals = plan.manifest["totals"]
    trend_person_days = sum(row[4] for row in expectations.daily_trend)
    department_person_days = sum(row[5] for row in expectations.daily_department)
    received = sum(row[6] for row in expectations.load_quality)
    accepted = sum(row[7] for row in expectations.load_quality)
    rejected = sum(row[8] for row in expectations.load_quality)
    validation_rejections = sum(row[2] for row in expectations.validation_issues)

    require(trend_person_days == int(totals["person_days"]), "Daily report total is incorrect.")
    require(department_person_days == trend_person_days, "Department report total is incorrect.")
    require(received == int(totals["source_rows"]), "Load-quality received total is incorrect.")
    require(accepted == int(totals["accepted_rows"]), "Load-quality accepted total is incorrect.")
    require(rejected == int(totals["rejected_rows"]), "Load-quality rejected total is incorrect.")
    require(validation_rejections == rejected, "Validation summary does not reconcile to rejected rows.")
    require(
        all(row[2] == 0 and row[5] == 0 for row in expectations.load_quality),
        "Canonical reporting expectations contain an in-progress or failed batch.",
    )
    require(
        all(row[5] == row[6] + row[7] + row[8] for row in expectations.daily_department),
        "A department report row does not reconcile its detection counts.",
    )
    require(
        all(row[4] == row[5] + row[6] + row[7] for row in expectations.daily_trend),
        "A daily report row does not reconcile its detection counts.",
    )

    return {
        "daily_rows": len(expectations.daily_trend),
        "department_rows": len(expectations.daily_department),
        "load_rows": len(expectations.load_quality),
        "validation_rows": len(expectations.validation_issues),
        "person_days": trend_person_days,
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
    }
