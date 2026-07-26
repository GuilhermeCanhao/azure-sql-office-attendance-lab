#!/usr/bin/env python3
"""Frozen Phase 8 recovery policy and database safety checks."""

from __future__ import annotations

import os
import sys
import getpass
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional, Tuple


RECOVERY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = RECOVERY_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (RECOVERY_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DATABASE_RE, SERVER_RE, SafeLoaderError  # noqa: E402


MARKER_TABLE = "dbo.Phase8RecoveryMarker"
MARKER_KEY = "POST_CHECKPOINT"
CANDIDATE_INDEX = "IX_core_DailyAttendanceSummary_OfficeDateMethod"
REPORT_VIEWS = (
    "vw_DailyAttendanceTrend",
    "vw_DailyDepartmentAttendance",
    "vw_LoadQualitySummary",
    "vw_ValidationIssueSummary",
)
APPLICATION_ROLES = ("app_loader", "report_reader")


@dataclass(frozen=True)
class RecoveryPolicy:
    target_edition: str = "Basic"
    target_backup_redundancy: str = "Local"
    target_zone_redundant: bool = False
    short_term_retention_days: int = 7
    differential_backup_hours: int = 12
    long_term_retention_policies: int = 0
    maximum_target_bytes: int = 2 * 1024 * 1024 * 1024
    observed_source_bytes: int = 114_819_072
    restore_limit_minutes: int = 60
    verify_delete_limit_minutes: int = 60
    cost_ceiling_eur: int = 5
    target_audit_must_be_inspected: bool = True


APPROVED_POLICY = RecoveryPolicy()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def validate_policy(policy: RecoveryPolicy = APPROVED_POLICY) -> dict:
    require(policy.target_edition == "Basic", "Temporary restore target must be Basic.")
    require(
        policy.target_backup_redundancy == "Local",
        "Temporary restore backup redundancy must remain local.",
    )
    require(not policy.target_zone_redundant, "Temporary restore cannot be zone redundant.")
    require(policy.short_term_retention_days == 7, "Short-term retention changed.")
    require(policy.differential_backup_hours == 12, "Differential frequency changed.")
    require(policy.long_term_retention_policies == 0, "Long-term retention must remain disabled.")
    require(
        0 < policy.observed_source_bytes < policy.maximum_target_bytes,
        "Observed source size no longer fits the approved target boundary.",
    )
    require(policy.restore_limit_minutes == 60, "Restore time limit changed.")
    require(
        policy.verify_delete_limit_minutes == 60,
        "Verification and deletion time limit changed.",
    )
    require(policy.cost_ceiling_eur == 5, "Project cost ceiling changed.")
    require(policy.target_audit_must_be_inspected, "Target audit inspection cannot be skipped.")
    return {
        "target_edition": policy.target_edition,
        "restore_limit_minutes": policy.restore_limit_minutes,
        "verify_delete_limit_minutes": policy.verify_delete_limit_minutes,
        "cost_ceiling_eur": policy.cost_ceiling_eur,
    }


def runtime_databases(require_restore: bool = False) -> Tuple[str, str, Optional[str]]:
    server = os.environ.get("ATTENDANCE_SQL_SERVER", "").strip()
    source = os.environ.get("ATTENDANCE_SQL_SOURCE_DATABASE", "").strip()
    restored = os.environ.get("ATTENDANCE_SQL_RESTORE_DATABASE", "").strip()
    if not server or not source or (require_restore and not restored):
        raise SafeLoaderError("Explicit Phase 8 runtime target is missing.")
    if not SERVER_RE.fullmatch(server) or not DATABASE_RE.fullmatch(source):
        raise SafeLoaderError("Phase 8 source target format is invalid.")
    if restored and not DATABASE_RE.fullmatch(restored):
        raise SafeLoaderError("Phase 8 restore target format is invalid.")
    if restored and restored == source:
        raise SafeLoaderError("Source and restored database must be different.")
    return server, source, restored or None


def confirm_database(expected: str, confirmation: Optional[str], label: str) -> None:
    require(bool(confirmation), f"Explicit {label} database confirmation is required.")
    require(confirmation == expected, f"Explicit {label} database confirmation does not match.")


def private_database_confirmation(label: str) -> str:
    """Read a target name without placing it in command history or normal output."""
    try:
        return getpass.getpass(f"Privately re-enter the {label} database name: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SafeLoaderError(f"Private {label} database confirmation was not completed.") from exc


def parse_private_utc(value: str) -> datetime:
    normalized = value.strip()
    formats = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S UTC")
    for format_text in formats:
        try:
            return datetime.strptime(normalized, format_text)
        except ValueError:
            pass
    raise SafeLoaderError("A private recovery timestamp has an unsupported format.")


def validate_portal_restore_point(captured: datetime, reviewed: datetime) -> dict:
    expected = captured.replace(microsecond=0)
    require(
        reviewed == expected,
        "Portal review timestamp does not exactly match the captured pre-marker second.",
    )
    return {"exact_second_match": True}


def _rows(connection, sql: str, *params) -> list:
    try:
        return [tuple(row) for row in connection.cursor().execute(sql, *params).fetchall()]
    except Exception as exc:
        raise SafeLoaderError("A recovery safety query failed; database details were suppressed.") from exc


def verify_database_identity(connection, expected_database: str) -> None:
    rows = _rows(connection, "SELECT DB_NAME();")
    require(len(rows) == 1 and str(rows[0][0]) == expected_database, "Connected database identity is not the confirmed target.")


def marker_state(connection) -> Tuple[bool, int]:
    rows = _rows(
        connection,
        "IF OBJECT_ID(N'dbo.Phase8RecoveryMarker', N'U') IS NULL "
        "SELECT CONVERT(bit,0), CONVERT(bigint,0); "
        "ELSE SELECT CONVERT(bit,1), COUNT_BIG(*) FROM dbo.Phase8RecoveryMarker;",
    )
    require(len(rows) == 1, "Recovery marker state is ambiguous.")
    return bool(rows[0][0]), int(rows[0][1])


def verify_marker_schema(connection) -> None:
    columns = _rows(
        connection,
        "SELECT columns.name, types.name, columns.is_nullable, columns.scale "
        "FROM sys.columns AS columns INNER JOIN sys.types AS types "
        "ON types.user_type_id = columns.user_type_id "
        "WHERE columns.object_id = OBJECT_ID(N'dbo.Phase8RecoveryMarker') "
        "ORDER BY columns.column_id;",
    )
    normalized = [(str(a), str(b), bool(c), int(d)) for a, b, c, d in columns]
    require(
        normalized == [("MarkerKey", "varchar", False, 0), ("CreatedAtUtc", "datetime2", False, 3)],
        "Recovery marker schema is not exact.",
    )
    keys = _rows(
        connection,
        "SELECT columns.name FROM sys.indexes AS indexes "
        "INNER JOIN sys.index_columns AS index_columns "
        "ON index_columns.object_id = indexes.object_id AND index_columns.index_id = indexes.index_id "
        "INNER JOIN sys.columns AS columns "
        "ON columns.object_id = index_columns.object_id AND columns.column_id = index_columns.column_id "
        "WHERE indexes.object_id = OBJECT_ID(N'dbo.Phase8RecoveryMarker') "
        "AND indexes.is_primary_key = 1 ORDER BY index_columns.key_ordinal;",
    )
    require(keys == [("MarkerKey",)], "Recovery marker primary key is not exact.")


def verify_recovery_structure(connection, expect_marker: str) -> dict:
    require(expect_marker in {"absent", "present"}, "Marker expectation is invalid.")
    candidate = _rows(
        connection,
        "SELECT columns.name, index_columns.key_ordinal, index_columns.is_included_column, "
        "indexes.is_unique, indexes.is_disabled FROM sys.indexes AS indexes "
        "INNER JOIN sys.index_columns AS index_columns "
        "ON index_columns.object_id = indexes.object_id AND index_columns.index_id = indexes.index_id "
        "INNER JOIN sys.columns AS columns ON columns.object_id = index_columns.object_id "
        "AND columns.column_id = index_columns.column_id "
        "WHERE indexes.object_id = OBJECT_ID(N'core.DailyAttendanceSummary') "
        "AND indexes.name = ? ORDER BY index_columns.key_ordinal, index_columns.index_column_id;",
        CANDIDATE_INDEX,
    )
    expected_candidate = [
        ("OfficeId", 1, False, False, False),
        ("AttendanceDateLocal", 2, False, False, False),
        ("DetectionMethod", 3, False, False, False),
    ]
    normalized_candidate = [
        (str(a), int(b), bool(c), bool(d), bool(e)) for a, b, c, d, e in candidate
    ]
    require(normalized_candidate == expected_candidate, "Retained candidate index is not exact.")

    views = tuple(row[0] for row in _rows(
        connection,
        "SELECT name FROM sys.views WHERE schema_id = SCHEMA_ID(N'report') ORDER BY name;",
    ))
    require(views == tuple(sorted(REPORT_VIEWS)), "Reporting-view inventory is not exact.")

    roles = _rows(
        connection,
        "SELECT roles.name, COUNT(members.member_principal_id) FROM sys.database_principals AS roles "
        "LEFT JOIN sys.database_role_members AS members ON members.role_principal_id = roles.principal_id "
        "WHERE roles.name IN (N'app_loader', N'report_reader') "
        "GROUP BY roles.name ORDER BY roles.name;",
    )
    require(
        [(str(name), int(count)) for name, count in roles] == [("app_loader", 0), ("report_reader", 0)],
        "Application-role inventory or membership is not exact.",
    )

    fixture_rows = _rows(
        connection,
        "SELECT "
        "(SELECT COUNT(*) FROM sys.database_principals WHERE name LIKE N'tst[_]%') + "
        "(SELECT COUNT(*) FROM sys.views WHERE name LIKE N'tst[_]%') + "
        "(SELECT COUNT(*) FROM stage.ImportBatch WHERE SourceFileName LIKE N'tst-%' OR SourceFileName LIKE N'test-%');",
    )
    require(len(fixture_rows) == 1 and int(fixture_rows[0][0]) == 0, "Unexpected recovery fixtures exist.")

    encryption = _rows(
        connection,
        "SELECT encryption_state FROM sys.dm_database_encryption_keys WHERE database_id = DB_ID();",
    )
    require(len(encryption) == 1 and int(encryption[0][0]) == 3, "Database encryption is not enabled.")

    exists, count = marker_state(connection)
    if expect_marker == "present":
        require(exists and count == 1, "Source recovery marker is not exactly present.")
        verify_marker_schema(connection)
    else:
        require(not exists and count == 0, "Recovery marker unexpectedly exists.")
    return {"report_views": len(views), "role_members": 0, "fixtures": 0, "marker": expect_marker}


def validate_live_control_state(state: Mapping[str, object]) -> None:
    """Validate privacy-safe values collected before a future live restore."""
    require(int(state.get("user_database_count", -1)) == 1, "User-database inventory is not exactly one.")
    require(int(state.get("temporary_database_count", -1)) == 0, "A temporary restore database already exists.")
    require(state.get("source_backup_redundancy") == "Local", "Source backup redundancy changed.")
    require(int(state.get("short_term_retention_days", -1)) == 7, "Short-term retention changed.")
    require(int(state.get("differential_backup_hours", -1)) == 12, "Differential frequency changed.")
    require(int(state.get("long_term_retention_policies", -1)) == 0, "Long-term retention changed.")
    require(bool(state.get("restore_window_available")), "Point-in-time restore window is unavailable.")
    require(int(state.get("source_bytes", -1)) < APPROVED_POLICY.maximum_target_bytes, "Source no longer fits Basic.")
    require(float(state.get("actual_cost_eur", 6)) <= APPROVED_POLICY.cost_ceiling_eur, "Actual cost exceeds ceiling.")
    forecast = state.get("forecast_cost_eur")
    require(
        forecast is None or float(forecast) <= APPROVED_POLICY.cost_ceiling_eur,
        "Forecast cost exceeds ceiling.",
    )
