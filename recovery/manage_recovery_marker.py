#!/usr/bin/env python3
"""Offline-default, explicitly guarded Phase 8 recovery-marker client."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


RECOVERY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = RECOVERY_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (RECOVERY_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    DEFAULT_DATA_DIR,
    SafeLoaderError,
    acquire_azure_sql_token,
    build_dataset_plan,
    connect_ready_target,
    result_row,
    safe_main_error,
)
from reporting_common import build_reporting_expectations  # noqa: E402
from recovery_common import (  # noqa: E402
    MARKER_KEY,
    confirm_database,
    marker_state,
    private_database_confirmation,
    runtime_databases,
    validate_policy,
    verify_database_identity,
    verify_marker_schema,
    verify_recovery_structure,
)


CREATE_MARKER_SQL = f"""
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.Phase8RecoveryMarker', N'U') IS NOT NULL
    THROW 52200, 'Recovery marker already exists.', 1;

CREATE TABLE dbo.Phase8RecoveryMarker
(
    MarkerKey varchar(32) NOT NULL
        CONSTRAINT PK_Phase8RecoveryMarker PRIMARY KEY,
    CreatedAtUtc datetime2(3) NOT NULL
        CONSTRAINT DF_Phase8RecoveryMarker_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
    CONSTRAINT CK_Phase8RecoveryMarker_Key CHECK (MarkerKey = '{MARKER_KEY}')
);

INSERT dbo.Phase8RecoveryMarker (MarkerKey) VALUES ('{MARKER_KEY}');
SELECT COUNT_BIG(*) AS MarkerCount, MIN(CreatedAtUtc) AS MarkerCreatedAtUtc
FROM dbo.Phase8RecoveryMarker;
""".strip()

DROP_MARKER_SQL = """
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.Phase8RecoveryMarker', N'U') IS NULL
    THROW 52201, 'Recovery marker does not exist.', 1;
IF (SELECT COUNT_BIG(*) FROM dbo.Phase8RecoveryMarker) <> 1
    THROW 52202, 'Recovery marker row count is not exact.', 1;
DROP TABLE dbo.Phase8RecoveryMarker;
SELECT CONVERT(int, 0) AS MarkerCount;
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Canonical generated-data directory (not printed).")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute-create-marker", action="store_true", help="Run verified source preflight, capture a UTC restore point, and commit one marker.")
    modes.add_argument("--execute-remove-marker", action="store_true", help="Remove the exact marker from the confirmed source after restore deletion.")
    parser.add_argument(
        "--confirm-restore-deleted",
        action="store_true",
        help="Required for cleanup only after control-plane inventory proves the temporary target is absent.",
    )
    return parser.parse_args()


def validate_marker_contract() -> dict:
    required_create = (
        "SET XACT_ABORT ON", "OBJECT_ID", "THROW 52200", "CREATE TABLE",
        "PRIMARY KEY", "CHECK", "SYSUTCDATETIME", "INSERT", "MarkerCount",
    )
    required_drop = ("SET XACT_ABORT ON", "OBJECT_ID", "THROW 52201", "THROW 52202", "DROP TABLE")
    for marker in required_create:
        if marker not in CREATE_MARKER_SQL:
            raise SafeLoaderError("Recovery-marker create contract is incomplete.")
    for marker in required_drop:
        if marker not in DROP_MARKER_SQL:
            raise SafeLoaderError("Recovery-marker cleanup contract is incomplete.")
    if "COMMIT" in CREATE_MARKER_SQL.upper() or "COMMIT" in DROP_MARKER_SQL.upper():
        raise SafeLoaderError("Recovery-marker SQL must leave transaction ownership to the client.")
    return {"create_guards": len(required_create), "cleanup_guards": len(required_drop)}


def _transaction(
    connection,
    sql: str,
    purpose: str,
    validator: Optional[Callable[[dict], None]] = None,
) -> dict:
    connection.autocommit = False
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        result = result_row(cursor)
        if result is None:
            raise SafeLoaderError(f"{purpose} returned no controlled result.")
        if validator is not None:
            validator(result)
        connection.commit()
        return result
    except SafeLoaderError:
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        raise SafeLoaderError(f"{purpose} failed; database details were suppressed.") from exc
    finally:
        connection.autocommit = True


def create_marker(connection, sleep_seconds: float = 5.0) -> datetime:
    exists, count = marker_state(connection)
    if exists or count:
        raise SafeLoaderError("Recovery marker already exists; creation refused.")
    try:
        row = connection.cursor().execute("SELECT SYSUTCDATETIME();").fetchone()
    except Exception as exc:
        raise SafeLoaderError("Restore-point capture failed; database details were suppressed.") from exc
    if row is None or not isinstance(row[0], datetime):
        raise SafeLoaderError("Restore-point capture returned an unexpected type.")
    restore_point = row[0]
    time.sleep(max(5.0, sleep_seconds))

    def validate_created(result: dict) -> None:
        created = result.get("MarkerCreatedAtUtc")
        if int(result.get("MarkerCount", -1)) != 1 or not isinstance(created, datetime) or created <= restore_point:
            raise SafeLoaderError("Recovery marker does not follow the restore point; transaction rolled back.")

    _transaction(
        connection,
        CREATE_MARKER_SQL,
        "Recovery-marker creation",
        validator=validate_created,
    )
    verify_marker_schema(connection)
    return restore_point


def remove_marker(connection) -> None:
    exists, count = marker_state(connection)
    if not exists or count != 1:
        raise SafeLoaderError("Exact recovery marker is not available for cleanup.")
    verify_marker_schema(connection)
    def validate_removed(result: dict) -> None:
        if int(result.get("MarkerCount", -1)) != 0:
            raise SafeLoaderError("Recovery-marker cleanup returned an unexpected result; transaction rolled back.")

    _transaction(
        connection,
        DROP_MARKER_SQL,
        "Recovery-marker cleanup",
        validator=validate_removed,
    )
    if marker_state(connection) != (False, 0):
        raise SafeLoaderError("Recovery marker remains after cleanup.")


def _source_preflight(connection, data_dir: Path) -> None:
    from verify_loaded_data import verify_database  # local import keeps dry mode simple
    from verify_reporting import verify_report_views

    plan = build_dataset_plan(data_dir)
    reporting = build_reporting_expectations(plan)
    verify_database(connection, plan)
    verify_report_views(connection, reporting)
    verify_recovery_structure(connection, "absent")


def main() -> int:
    connection = None
    try:
        args = parse_args()
        contract = validate_marker_contract()
        validate_policy()
        if not args.execute_create_marker and not args.execute_remove_marker:
            print(
                "Phase 8 marker contract: PASS "
                f"CreateGuards={contract['create_guards']} CleanupGuards={contract['cleanup_guards']}"
            )
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        server, source, _ = runtime_databases(require_restore=False)
        confirm_database(source, private_database_confirmation("source"), "source")
        token = acquire_azure_sql_token()
        connection = connect_ready_target(server, source, token)
        verify_database_identity(connection, source)

        if args.execute_create_marker:
            _source_preflight(connection, args.data_dir)
            restore_point = create_marker(connection)
            print("Phase 8 recovery marker: PASS Created=1")
            print("Restore point captured privately for the approved portal restore.")
            print(restore_point.strftime("RestorePointUtc=%Y-%m-%dT%H:%M:%S.%fZ"))
        else:
            if not args.confirm_restore_deleted:
                raise SafeLoaderError("Verified restore-target deletion confirmation is required.")
            remove_marker(connection)
            print("Phase 8 recovery marker cleanup: PASS Remaining=0")
        return 0
    except Exception as exc:
        print(f"Phase 8 recovery marker: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
