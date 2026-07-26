#!/usr/bin/env python3
"""Offline-default verifier for Phase 8 source and restored-database acceptance."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


RECOVERY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = RECOVERY_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (RECOVERY_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    DEFAULT_DATA_DIR,
    acquire_azure_sql_token,
    build_dataset_plan,
    connect_ready_target,
    safe_main_error,
)
from reporting_common import build_reporting_expectations, validate_reporting_expectations  # noqa: E402
from recovery_common import (  # noqa: E402
    confirm_database,
    private_database_confirmation,
    runtime_databases,
    validate_policy,
    verify_database_identity,
    verify_recovery_structure,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Canonical generated-data directory (not printed).")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute-source-preflight", action="store_true", help="Read and reconcile the confirmed source before marker creation.")
    modes.add_argument("--execute-restored-target", action="store_true", help="Independently reconcile the confirmed restored target and marked source.")
    parser.add_argument("--confirm-target-audit-safe", action="store_true", help="Confirm the target database-level audit was inspected and is absent or disabled.")
    return parser.parse_args()


def offline_expectations(data_dir: Path) -> dict:
    policy = validate_policy()
    plan = build_dataset_plan(data_dir)
    reporting = build_reporting_expectations(plan)
    report_result = validate_reporting_expectations(reporting, plan)
    totals = plan.manifest["totals"]
    return {
        "policy": policy,
        "plan": plan,
        "reporting": reporting,
        "batches": len(plan.batches),
        "accepted": int(totals["accepted_rows"]),
        "rejected": int(totals["rejected_rows"]),
        "person_days": int(totals["person_days"]),
        "daily_rows": report_result["daily_rows"],
        "department_rows": report_result["department_rows"],
    }


def verify_complete_database(connection, expected_database: str, expected: dict, marker: str) -> dict:
    from verify_loaded_data import verify_database
    from verify_reporting import verify_report_views

    verify_database_identity(connection, expected_database)
    canonical = verify_database(connection, expected["plan"])
    verify_report_views(connection, expected["reporting"])
    structure = verify_recovery_structure(connection, marker)
    return {"canonical": canonical, "structure": structure}


def main() -> int:
    source_connection = None
    target_connection = None
    try:
        args = parse_args()
        expected = offline_expectations(args.data_dir)
        if not args.execute_source_preflight and not args.execute_restored_target:
            print(
                "Phase 8 recovery expectations: PASS "
                f"Batches={expected['batches']} Accepted={expected['accepted']} "
                f"Rejected={expected['rejected']} PersonDays={expected['person_days']}"
            )
            print(
                f"ReportRows={expected['daily_rows']} DepartmentRows={expected['department_rows']} "
                "Target=Basic RestoreLimitMinutes=60 VerifyDeleteLimitMinutes=60"
            )
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        server, source, restored = runtime_databases(require_restore=args.execute_restored_target)
        confirm_database(source, private_database_confirmation("source"), "source")
        token = acquire_azure_sql_token()
        source_connection = connect_ready_target(server, source, token)

        if args.execute_source_preflight:
            verify_complete_database(source_connection, source, expected, "absent")
            print("Phase 8 source preflight: PASS Marker=0 UnexpectedFixtures=0")
            return 0

        if not args.confirm_target_audit_safe:
            from loader_common import SafeLoaderError
            raise SafeLoaderError("Target audit inspection confirmation is required.")
        confirm_database(str(restored), private_database_confirmation("restore"), "restore")
        target_connection = connect_ready_target(server, str(restored), token)
        verify_complete_database(target_connection, str(restored), expected, "absent")
        verify_complete_database(source_connection, source, expected, "present")
        print(
            "Phase 8 restored-database verification: PASS "
            f"Batches={expected['batches']} Accepted={expected['accepted']} "
            f"Rejected={expected['rejected']} PersonDays={expected['person_days']}"
        )
        print(
            f"ReportRows={expected['daily_rows']} DepartmentRows={expected['department_rows']} "
            "TargetMarker=0 SourceMarker=1 RoleMembers=0 UnexpectedFixtures=0"
        )
        return 0
    except Exception as exc:
        print(f"Phase 8 recovery verification: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        for connection in (target_connection, source_connection):
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
