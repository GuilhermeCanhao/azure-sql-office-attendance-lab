#!/usr/bin/env python3
"""Deterministic, offline Tableau aggregate-export contract."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, SafeLoaderError, build_dataset_plan  # noqa: E402
from reporting_common import (  # noqa: E402
    ReportingExpectations,
    build_reporting_expectations,
    validate_reporting_expectations,
)


CONTRACT_VERSION = "1.0"
MANIFEST_NAME = "tableau_export_manifest.json"
DEFAULT_OUTPUT_DIR = TABLEAU_DIR / "output"


@dataclass(frozen=True)
class ExportSpec:
    filename: str
    columns: Tuple[str, ...]
    rows: Tuple[tuple, ...]


EXPORT_COLUMNS: Mapping[str, Tuple[str, ...]] = {
    "daily_attendance_trend.csv": (
        "AttendanceDateLocal", "OfficeCode", "OfficeName", "OfficeCapacity",
        "PersonDayCount", "CardOnlyPersonDays", "WifiOnlyPersonDays",
        "BothPersonDays", "BadgeObservedPersonDays", "WifiObservedPersonDays",
        "OccupancyRate",
    ),
    "daily_department_attendance.csv": (
        "AttendanceDateLocal", "OfficeCode", "OfficeName", "DepartmentCode",
        "DepartmentName", "PersonDayCount", "CardOnlyPersonDays",
        "WifiOnlyPersonDays", "BothPersonDays",
    ),
    "load_quality_summary.csv": (
        "SourceType", "TerminalBatchCount", "InProgressBatchCount",
        "CompletedWithoutRejectsBatchCount", "CompletedWithRejectsBatchCount",
        "FailedBatchCount", "RowsReceived", "RowsAccepted", "RowsRejected",
        "AcceptanceRate",
    ),
    "validation_issue_summary.csv": (
        "SourceType", "ValidationCode", "RejectedRowCount",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _cell_text(value) -> str:
    if isinstance(value, Decimal):
        return format(value, ".6f")
    return str(value)


def _csv_bytes(columns: Sequence[str], rows: Sequence[tuple]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        require(len(row) == len(columns), "An aggregate export row has an unexpected width.")
        writer.writerow([_cell_text(value) for value in row])
    return handle.getvalue().encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_export_specs(expectations: ReportingExpectations) -> Tuple[ExportSpec, ...]:
    row_sets = (
        expectations.daily_trend,
        expectations.daily_department,
        expectations.load_quality,
        expectations.validation_issues,
    )
    return tuple(
        ExportSpec(filename, columns, tuple(rows))
        for (filename, columns), rows in zip(EXPORT_COLUMNS.items(), row_sets)
    )


def build_export_bundle(data_dir: Path = DEFAULT_DATA_DIR) -> Tuple[Dict[str, bytes], dict]:
    plan = build_dataset_plan(data_dir)
    expectations = build_reporting_expectations(plan)
    totals = validate_reporting_expectations(expectations, plan)
    bundle: Dict[str, bytes] = {}
    file_records = {}
    for spec in build_export_specs(expectations):
        content = _csv_bytes(spec.columns, spec.rows)
        bundle[spec.filename] = content
        file_records[spec.filename] = {
            "columns": list(spec.columns),
            "rows": len(spec.rows),
            "sha256": _sha256(content),
        }
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "files": file_records,
        "totals": totals,
    }
    bundle[MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    return bundle, manifest


def _validate_destination(output_dir: Path) -> Path:
    require(not output_dir.is_symlink(), "The Tableau export directory must not be a symlink.")
    resolved = output_dir.expanduser().resolve()
    require(resolved != PROJECT_ROOT, "The project root cannot be used as the export directory.")
    require(resolved != Path(resolved.anchor), "A filesystem root cannot be used as the export directory.")
    return resolved


def write_export_bundle(bundle: Mapping[str, bytes], output_dir: Path) -> None:
    destination = _validate_destination(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    require(destination.is_dir(), "The Tableau export destination is not a directory.")
    allowed = set(bundle)
    unexpected = {item.name for item in destination.iterdir()} - allowed
    require(not unexpected, "The Tableau export directory contains unexpected content.")
    for filename, content in bundle.items():
        target = destination / filename
        require(not target.is_symlink(), "A Tableau export target must not be a symlink.")
        temporary = destination / f".{filename}.tmp"
        temporary.write_bytes(content)
        os.replace(temporary, target)


def validate_export_directory(
    output_dir: Path, data_dir: Path = DEFAULT_DATA_DIR
) -> dict:
    destination = _validate_destination(output_dir)
    require(destination.is_dir(), "The Tableau export directory does not exist.")
    expected_bundle, manifest = build_export_bundle(data_dir)
    actual_names = {item.name for item in destination.iterdir() if item.is_file()}
    require(actual_names == set(expected_bundle), "The Tableau export inventory is not exact.")
    require(
        not any(item.is_dir() or item.is_symlink() for item in destination.iterdir()),
        "The Tableau export directory contains an unsupported entry.",
    )
    for filename, expected in expected_bundle.items():
        require(
            (destination / filename).read_bytes() == expected,
            "A Tableau export differs from the independent aggregate expectation.",
        )
    return manifest["totals"]
