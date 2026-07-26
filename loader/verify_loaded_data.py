#!/usr/bin/env python3
"""Independently reconcile loaded Azure SQL data with generated expectations."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from loader_common import (
    DEFAULT_DATA_DIR,
    EXPECTED_DAILY_HEADER,
    DatasetPlan,
    SafeLoaderError,
    acquire_azure_sql_token,
    build_dataset_plan,
    connect_ready_target,
    read_csv_rows,
    runtime_target,
    safe_main_error,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Canonical generated-data directory (not printed).",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _iso_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _iso_utc(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, datetime):
        raise SafeLoaderError("Azure SQL returned an unexpected timestamp type.")
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def _rows(connection, sql: str) -> List[tuple]:
    try:
        return [tuple(row) for row in connection.cursor().execute(sql).fetchall()]
    except Exception as exc:
        raise SafeLoaderError("A verification query failed; database details were suppressed.") from exc


def _expected_reference(plan: DatasetPlan, filename: str) -> List[Tuple[str, ...]]:
    return [tuple(row.values()) for row in plan.reference_rows[filename]]


def verify_references(connection, plan: DatasetPlan) -> None:
    actual: Mapping[str, List[Tuple[str, ...]]] = {
        "offices.csv": [
            (str(a), str(b), str(c), str(d), str(int(e)))
            for a, b, c, d, e in _rows(
                connection,
                "SELECT OfficeCode, DisplayName, TimeZoneName, Capacity, IsActive "
                "FROM core.Office ORDER BY OfficeCode;",
            )
        ],
        "departments.csv": [
            (str(a), str(b), str(int(c)))
            for a, b, c in _rows(
                connection,
                "SELECT DepartmentCode, DepartmentName, IsActive "
                "FROM core.Department ORDER BY DepartmentCode;",
            )
        ],
        "people.csv": [
            (str(a), str(b), str(c), str(d), _iso_date(e), _iso_date(f))
            for a, b, c, d, e, f in _rows(
                connection,
                "SELECT p.PersonnelCode, p.DisplayName, p.SyntheticEmail, d.DepartmentCode, "
                "p.ValidFrom, p.ValidTo FROM core.Person AS p "
                "JOIN core.Department AS d ON d.DepartmentId = p.DepartmentId "
                "ORDER BY p.PersonnelCode;",
            )
        ],
        "devices.csv": [
            (str(a), str(b))
            for a, b in _rows(
                connection,
                "SELECT DeviceToken, DeviceStatus FROM core.Device ORDER BY DeviceToken;",
            )
        ],
        "device_assignments.csv": [
            (str(a), str(b), _iso_utc(c), _iso_utc(d))
            for a, b, c, d in _rows(
                connection,
                "SELECT p.PersonnelCode, d.DeviceToken, a.ValidFrom, a.ValidTo "
                "FROM core.PersonDeviceAssignment AS a "
                "JOIN core.Person AS p ON p.PersonId = a.PersonId "
                "JOIN core.Device AS d ON d.DeviceId = a.DeviceId "
                "ORDER BY p.PersonnelCode, d.DeviceToken, a.ValidFrom;",
            )
        ],
        "access_points.csv": [
            (str(a), str(b), str(c), str(d), str(int(e)))
            for a, b, c, d, e in _rows(
                connection,
                "SELECT o.OfficeCode, a.AccessPointCode, a.AccessPointType, a.DisplayLabel, a.IsActive "
                "FROM core.AccessPoint AS a JOIN core.Office AS o ON o.OfficeId = a.OfficeId "
                "ORDER BY a.AccessPointCode;",
            )
        ],
    }
    for filename, expected_rows in plan.reference_rows.items():
        expected = sorted(tuple(str(value) for value in row.values()) for row in expected_rows)
        require(sorted(actual[filename]) == expected, f"Reference reconciliation failed: {filename}.")


def verify_batches(connection, plan: DatasetPlan) -> None:
    actual_rows = _rows(
        connection,
        "SELECT SourceType, SourceFileName, CONVERT(varchar(64), FileChecksum, 2), Status, "
        "RowsReceived, RowsAccepted, RowsRejected "
        "FROM stage.ImportBatch ORDER BY SourceType, SourceFileName;",
    )
    actual: Dict[Tuple[str, str], tuple] = {}
    for source_type, filename, checksum, status, received, accepted, rejected in actual_rows:
        actual[(str(source_type), str(filename))] = (
            str(checksum).lower(), str(status), int(received), int(accepted), int(rejected)
        )
    require(len(actual_rows) == len(actual) == len(plan.batches), "Import-batch inventory is not exactly canonical.")
    for batch in plan.batches:
        key = (batch.source_type, batch.source_file_name)
        require(key in actual, f"Canonical batch is missing: {batch.source_file_name}.")
        require(
            actual[key]
            == (
                batch.file_sha256,
                batch.expected_status,
                batch.rows_received,
                batch.rows_accepted,
                batch.rows_rejected,
            ),
            f"Batch reconciliation failed: {batch.source_file_name}.",
        )

    totals = _rows(
        connection,
        "SELECT COALESCE(SUM(RowsReceived),0), COALESCE(SUM(RowsAccepted),0), "
        "COALESCE(SUM(RowsRejected),0), "
        "COALESCE(SUM(CASE WHEN Status IN ('STARTED','FAILED') THEN 1 ELSE 0 END),0) "
        "FROM stage.ImportBatch;",
    )[0]
    manifest_totals = plan.manifest["totals"]
    require(int(totals[0]) == int(manifest_totals["source_rows"]), "Batch source-row total does not reconcile.")
    require(int(totals[1]) == int(manifest_totals["accepted_rows"]), "Batch accepted-row total does not reconcile.")
    require(int(totals[2]) == int(manifest_totals["rejected_rows"]), "Batch rejected-row total does not reconcile.")
    require(int(totals[3]) == 0, "Unreconciled or abandoned import batches exist.")

    lifecycle = _rows(
        connection,
        "SELECT "
        "(SELECT COUNT(*) FROM stage.CardAccessEvent WHERE ProcessingStatus = 'PENDING') + "
        "(SELECT COUNT(*) FROM stage.WifiObservation WHERE ProcessingStatus = 'PENDING'), "
        "(SELECT COUNT(*) FROM stage.CardAccessEvent WHERE ProcessingStatus = 'ACCEPTED') + "
        "(SELECT COUNT(*) FROM stage.WifiObservation WHERE ProcessingStatus = 'ACCEPTED'), "
        "(SELECT COUNT(*) FROM stage.CardAccessEvent WHERE ProcessingStatus = 'REJECTED') + "
        "(SELECT COUNT(*) FROM stage.WifiObservation WHERE ProcessingStatus = 'REJECTED'), "
        "(SELECT COUNT(*) FROM core.AttendanceSignal), "
        "(SELECT COUNT(*) FROM stage.ImportError);",
    )[0]
    require(int(lifecycle[0]) == 0, "Pending staging rows remain.")
    require(int(lifecycle[1]) == int(manifest_totals["accepted_rows"]), "Accepted staging rows do not reconcile.")
    require(int(lifecycle[2]) == int(manifest_totals["rejected_rows"]), "Rejected staging rows do not reconcile.")
    require(int(lifecycle[3]) == int(manifest_totals["attendance_signals"]), "Attendance-signal total does not reconcile.")
    require(int(lifecycle[4]) == int(manifest_totals["rejected_rows"]), "Import-error total does not reconcile.")


def verify_validations(connection, plan: DatasetPlan) -> None:
    expected = {
        (row["source_type"], row["source_file_name"], row["validation_code"]): int(row["expected_count"])
        for row in plan.expected_validations
    }
    actual = {
        (str(a), str(b), str(c)): int(d)
        for a, b, c, d in _rows(
            connection,
            "SELECT b.SourceType, b.SourceFileName, e.ValidationCode, COUNT(*) "
            "FROM stage.ImportError AS e "
            "JOIN stage.ImportBatch AS b ON b.ImportBatchId = e.ImportBatchId "
            "GROUP BY b.SourceType, b.SourceFileName, e.ValidationCode;",
        )
    }
    require(actual == expected, "Validation-code counts do not match the independent expectations.")


def verify_signals(connection, plan: DatasetPlan) -> None:
    expected = Counter()
    for batch in plan.batches:
        expected[batch.source_type] += batch.rows_accepted
    actual = Counter(
        {str(signal_type): int(count) for signal_type, count in _rows(
            connection,
            "SELECT SignalType, COUNT_BIG(*) FROM core.AttendanceSignal GROUP BY SignalType;",
        )}
    )
    require(actual == expected, "Attendance-signal totals do not match expected batches.")


def verify_daily(connection, plan: DatasetPlan) -> None:
    expected_rows = read_csv_rows(plan.expected_daily_path, EXPECTED_DAILY_HEADER)
    actual_rows = _rows(
        connection,
        "SELECT s.AttendanceDateLocal, p.PersonnelCode, s.DetectionMethod, "
        "s.FirstObservedAtUtc, s.LastObservedAtUtc, s.CardSignalCount, s.WifiSignalCount "
        "FROM core.DailyAttendanceSummary AS s "
        "JOIN core.Person AS p ON p.PersonId = s.PersonId "
        "ORDER BY s.AttendanceDateLocal, p.PersonnelCode;",
    )
    require(len(actual_rows) == len(expected_rows), "Daily person-day total does not reconcile.")
    detection_counts = Counter()
    signal_count = 0
    for expected, actual in zip(expected_rows, actual_rows):
        normalized = {
            "attendance_date_local": _iso_date(actual[0]),
            "personnel_code": str(actual[1]),
            "detection_method": str(actual[2]),
            "first_observed_at_utc": _iso_utc(actual[3]),
            "last_observed_at_utc": _iso_utc(actual[4]),
            "card_signal_count": str(int(actual[5])),
            "wifi_signal_count": str(int(actual[6])),
        }
        require(normalized == expected, "A daily attendance row differs from the independent expectation.")
        detection_counts[normalized["detection_method"]] += 1
        signal_count += int(normalized["card_signal_count"]) + int(normalized["wifi_signal_count"])
    totals = plan.manifest["totals"]
    require(detection_counts["CARD"] == int(totals["card_person_days"]), "CARD person-day total differs.")
    require(detection_counts["WIFI"] == int(totals["wifi_person_days"]), "WIFI person-day total differs.")
    require(detection_counts["BOTH"] == int(totals["both_person_days"]), "BOTH person-day total differs.")
    require(signal_count == int(totals["attendance_signals"]), "Daily signal counts do not reconcile with facts.")


def verify_no_test_fixtures(connection) -> None:
    count = _rows(
        connection,
        "SELECT "
        "(SELECT COUNT(*) FROM core.Office WHERE OfficeCode LIKE 'TST-%') + "
        "(SELECT COUNT(*) FROM core.Department WHERE DepartmentCode LIKE 'TST-%') + "
        "(SELECT COUNT(*) FROM core.Person WHERE PersonnelCode LIKE 'TST-%') + "
        "(SELECT COUNT(*) FROM core.AccessPoint WHERE AccessPointCode LIKE 'TST-%') + "
        "(SELECT COUNT(*) FROM stage.ImportBatch WHERE SourceFileName LIKE 'tst-%' OR SourceFileName LIKE 'test-%');",
    )[0][0]
    require(int(count) == 0, "Unexpected verification fixtures remain.")


def verify_database(connection, plan: DatasetPlan) -> dict:
    verify_references(connection, plan)
    verify_batches(connection, plan)
    verify_validations(connection, plan)
    verify_signals(connection, plan)
    verify_daily(connection, plan)
    verify_no_test_fixtures(connection)
    totals = plan.manifest["totals"]
    return {
        "reference_counts": {
            key: int(value) for key, value in plan.manifest["reference_counts"].items()
        },
        "batches": len(plan.batches),
        "accepted": int(totals["accepted_rows"]),
        "rejected": int(totals["rejected_rows"]),
        "person_days": int(totals["person_days"]),
        "card": int(totals["card_person_days"]),
        "wifi": int(totals["wifi_person_days"]),
        "both": int(totals["both_person_days"]),
    }


def main() -> int:
    connection = None
    try:
        args = parse_args()
        plan = build_dataset_plan(args.data_dir)
        server, database = runtime_target()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)
        result = verify_database(connection, plan)
        references = result["reference_counts"]
        print("Loaded-data verification: PASS")
        print(
            "Offices={offices} Departments={departments} People={people} Devices={devices} "
            "Assignments={device_assignments} AccessPoints={access_points}".format(**references)
        )
        print(
            "Batches={batches} Accepted={accepted} Rejected={rejected} PersonDays={person_days} "
            "CARD={card} WIFI={wifi} BOTH={both}".format(**result)
        )
        print("UnreconciledBatches=0 PendingRows=0 UnexpectedFixtures=0")
        return 0
    except Exception as exc:
        print(f"Loaded-data verification: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
