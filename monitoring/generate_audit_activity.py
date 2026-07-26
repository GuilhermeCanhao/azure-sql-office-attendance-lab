#!/usr/bin/env python3
"""Generate controlled rollback-only audit activity and prove all regressions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping, Optional


MONITORING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MONITORING_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (MONITORING_DIR, LOADER_DIR, REPORTING_DIR):
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
from verify_loaded_data import verify_database  # noqa: E402
from reporting_common import build_reporting_expectations  # noqa: E402
from verify_reporting import verify_report_views  # noqa: E402


SECURITY_SUITE = PROJECT_ROOT / "tests" / "012_verify_security_roles.sql"
AUDIT_PROBE = PROJECT_ROOT / "tests" / "014_verify_controlled_audit_activity.sql"
EXPECTED_RESULT: Mapping[str, object] = {
    "ComponentName": "security",
    "AdministratorControl": "PASS",
    "LoaderPositive": "PASS",
    "LoaderExpectedDenials": 6,
    "ReporterPositive": "PASS",
    "ReporterExpectedDenials": 6,
    "TransactionRollback": "PASS",
    "FixtureCleanup": "PASS",
}
EXPECTED_AUDIT_PROBE: Mapping[str, object] = {
    "ComponentName": "audit_probe",
    "PrincipalChange": "PASS",
    "RoleMembershipChange": "PASS",
    "ObjectChange": "PASS",
    "PermissionChange": "PASS",
    "TransactionRollback": "PASS",
    "FixtureCleanup": "PASS",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Connect with the existing Azure CLI session and run the approved activity.",
    )
    return parser.parse_args()


def validate_suite_source() -> dict:
    sql = SECURITY_SUITE.read_text(encoding="utf-8")
    required = (
        "BEGIN TRANSACTION", "ROLLBACK TRANSACTION", "CREATE USER",
        "ALTER ROLE", "CREATE VIEW", "FixtureCleanup",
    )
    probe = AUDIT_PROBE.read_text(encoding="utf-8")
    probe_required = (
        "BEGIN TRANSACTION", "ROLLBACK TRANSACTION", "CREATE USER",
        "ALTER ROLE", "CREATE VIEW", "GRANT", "DENY", "FixtureCleanup",
    )
    if any(marker not in sql for marker in required) or any(
        marker not in probe for marker in probe_required
    ):
        raise SafeLoaderError("The controlled security suite contract changed.")
    return {
        "required_markers": len(required) + len(probe_required),
        "expected_result_fields": len(EXPECTED_RESULT) + len(EXPECTED_AUDIT_PROBE),
    }


def run_security_suite(connection) -> None:
    _run_suite(connection, SECURITY_SUITE, "ComponentName", EXPECTED_RESULT)


def run_audit_probe(connection) -> None:
    _run_suite(connection, AUDIT_PROBE, "ComponentName", EXPECTED_AUDIT_PROBE)


def _run_suite(connection, path: Path, marker_column: str, expected: Mapping[str, object]) -> None:
    observed: Optional[dict] = None
    try:
        cursor = connection.cursor()
        cursor.execute(path.read_text(encoding="utf-8"))
        while True:
            if cursor.description:
                columns = [str(item[0]) for item in cursor.description]
                rows = cursor.fetchall()
                if marker_column in columns:
                    if len(rows) != 1:
                        raise SafeLoaderError("Controlled security activity returned unexpected rows.")
                    observed = dict(zip(columns, rows[0]))
            if not cursor.nextset():
                break
    except SafeLoaderError:
        raise
    except Exception as exc:
        raise SafeLoaderError(
            "Controlled security activity failed; database details were suppressed."
        ) from exc
    if observed is None:
        raise SafeLoaderError("Controlled security activity returned no verification result.")
    for name, value in expected.items():
        if observed.get(name) != value:
            raise SafeLoaderError(f"Controlled security result failed: {name}.")


def main() -> int:
    connection = None
    try:
        args = parse_args()
        contract = validate_suite_source()
        if not args.execute:
            print(
                "Controlled audit-activity contract: PASS "
                f"Markers={contract['required_markers']} "
                f"ResultFields={contract['expected_result_fields']}"
            )
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        plan = build_dataset_plan(DEFAULT_DATA_DIR)
        reporting = build_reporting_expectations(plan)
        server, database = runtime_target()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)
        print("Data-plane readiness probe: PASS")
        run_security_suite(connection)
        print(
            "Controlled security activity: PASS "
            "LoaderExpectedDenials=6 ReporterExpectedDenials=6 "
            "TransactionRollback=PASS FixtureCleanup=PASS"
        )
        run_audit_probe(connection)
        print(
            "Controlled audit probe: PASS PrincipalChange=PASS "
            "RoleMembershipChange=PASS ObjectChange=PASS PermissionChange=PASS "
            "TransactionRollback=PASS FixtureCleanup=PASS"
        )
        verify_database(connection, plan)
        print("Fresh canonical reconciliation: PASS UnexpectedFixtures=0")
        verify_report_views(connection, reporting)
        print("Fresh reporting reconciliation: PASS")
        return 0
    except Exception as exc:
        print(f"Controlled audit activity: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
