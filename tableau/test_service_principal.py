#!/usr/bin/env python3
"""Offline tests for the guarded temporary Tableau identity lifecycle."""

from __future__ import annotations

import io
import sys
import types
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (TABLEAU_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import SafeLoaderError  # noqa: E402
import manage_service_principal as manager  # noqa: E402
from service_principal_common import (  # noqa: E402
    BIND_SQL,
    IDENTITY_NAME,
    ODBC_DRIVER,
    UNBIND_SQL,
    ServicePrincipalPolicy,
    validate_policy,
    connect_service_principal,
    verify_database_preflight,
    verify_service_principal_boundary,
)


class FakeCursor:
    def __init__(self, responses):
        self.responses = responses
        self.current = []

    def execute(self, sql):
        for marker, response in self.responses:
            if marker in sql:
                if isinstance(response, Exception):
                    raise response
                self.current = response
                return self
        raise AssertionError("Unexpected SQL in service-principal test.")

    def fetchall(self):
        return self.current


class FakeConnection:
    def __init__(self, responses):
        self.responses = responses

    def cursor(self):
        return FakeCursor(self.responses)


class ServicePrincipalOfflineTests(unittest.TestCase):
    def test_policy_is_exact_and_rejects_drift(self) -> None:
        self.assertEqual(validate_policy()["secret_minutes"], 60)
        with self.assertRaisesRegex(SafeLoaderError, "Secret lifetime"):
            validate_policy(ServicePrincipalPolicy(secret_lifetime_minutes=61))
        with self.assertRaisesRegex(SafeLoaderError, "Azure RBAC"):
            validate_policy(ServicePrincipalPolicy(azure_rbac_roles=1))
        with self.assertRaisesRegex(SafeLoaderError, "API permissions"):
            validate_policy(ServicePrincipalPolicy(required_api_permissions=1))

    def test_sql_contract_is_fixed_transactional_and_cleanup_safe(self) -> None:
        self.assertIn(f"CREATE USER [{IDENTITY_NAME}] FROM EXTERNAL PROVIDER", BIND_SQL)
        self.assertIn("ALTER ROLE [report_reader] ADD MEMBER", BIND_SQL)
        self.assertIn("BEGIN TRANSACTION", BIND_SQL)
        self.assertIn("ROLLBACK TRANSACTION", BIND_SQL)
        self.assertIn("ALTER ROLE [report_reader] DROP MEMBER", UNBIND_SQL)
        self.assertIn(f"DROP USER [{IDENTITY_NAME}]", UNBIND_SQL)
        self.assertNotIn("db_owner", BIND_SQL)

    def test_clean_database_preflight_passes_and_drift_fails(self) -> None:
        exact_views = [(name,) for name in sorted((
            "vw_DailyAttendanceTrend",
            "vw_DailyDepartmentAttendance",
            "vw_LoadQualitySummary",
            "vw_ValidationIssueSummary",
        ))]
        verify_database_preflight(
            FakeConnection(
                [
                    ("SELECT COUNT(*)", [(0, 1, 0)]),
                    ("SELECT name FROM sys.views", exact_views),
                ]
            )
        )
        with self.assertRaisesRegex(SafeLoaderError, "not clean"):
            verify_database_preflight(FakeConnection([("SELECT COUNT(*)", [(1, 1, 1)])]))

    def test_exact_permission_boundary_and_direct_denials(self) -> None:
        permission_row = [(IDENTITY_NAME, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0)]
        connection = FakeConnection(
            [
                ("SELECT USER_NAME()", permission_row),
                ("stage.ImportBatch", RuntimeError("denied")),
                ("core.DailyAttendanceSummary", RuntimeError("denied")),
            ]
        )
        with patch("service_principal_common.verify_report_views") as report_verify:
            result = verify_service_principal_boundary(connection, MagicMock())
        report_verify.assert_called_once()
        self.assertEqual(result, {"report_views": 4, "expected_denials": 2, "report_roles": 1})

    def test_unexpected_restricted_read_is_rejected(self) -> None:
        permission_row = [(IDENTITY_NAME, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0)]
        connection = FakeConnection(
            [
                ("SELECT USER_NAME()", permission_row),
                ("stage.ImportBatch", []),
                ("core.DailyAttendanceSummary", RuntimeError("denied")),
            ]
        )
        with patch("service_principal_common.verify_report_views"):
            with self.assertRaisesRegex(SafeLoaderError, "unexpectedly succeeded"):
                verify_service_principal_boundary(connection, MagicMock())

    def test_default_mode_is_offline_and_privacy_safe(self) -> None:
        with patch.object(sys, "argv", ["manage_service_principal.py"]):
            with patch.object(manager, "_run_az_json") as azure:
                with patch.object(manager, "_admin_connection") as database:
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(manager.main(), 0)
        azure.assert_not_called()
        database.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())
        self.assertNotIn(IDENTITY_NAME, output.getvalue())

    def test_cli_error_suppresses_provider_output(self) -> None:
        secret_error = "private tenant object and credential value"
        completed = MagicMock(returncode=1, stdout="", stderr=secret_error)
        with patch.object(manager, "_azure_cli", return_value="az"):
            with patch.object(manager.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(SafeLoaderError, "failed safely") as context:
                    manager._run_az_json(["ad", "app", "list"])
        self.assertNotIn(secret_error, str(context.exception))

    def test_created_identity_requires_empty_permissions_and_one_hour_secret(self) -> None:
        object_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        principal_id = str(uuid.uuid4())
        secret_value = "private-secret-value-123"
        application = {
            "id": object_id,
            "appId": client_id,
            "displayName": IDENTITY_NAME,
            "signInAudience": "AzureADMyOrg",
            "requiredResourceAccess": [],
        }
        principal = {"id": principal_id, "appId": client_id, "displayName": IDENTITY_NAME}
        credential = {"appId": client_id, "password": secret_value}
        expiry = (manager.datetime.now(manager.timezone.utc) + manager.timedelta(minutes=60)).isoformat()
        metadata = [{"displayName": manager.CREDENTIAL_NAME, "endDateTime": expiry}]
        with patch.object(
            manager,
            "_run_az_json",
            side_effect=[application, principal, [], {"value": []}, credential, metadata],
        ) as run:
            object_id, client_id, secret = manager.create_identity()
        self.assertEqual((object_id, client_id, secret), (application["id"], application["appId"], secret_value))
        credential_command = run.call_args_list[-2].args[0]
        self.assertIn("--append", credential_command)
        self.assertIn("--end-date", credential_command)
        self.assertNotIn(secret_value, " ".join(credential_command))

    def test_invalid_secret_response_is_rejected(self) -> None:
        object_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        principal_id = str(uuid.uuid4())
        responses = [
            {
                "id": object_id,
                "appId": client_id,
                "displayName": IDENTITY_NAME,
                "signInAudience": "AzureADMyOrg",
                "requiredResourceAccess": [],
            },
            {"id": principal_id, "appId": client_id, "displayName": IDENTITY_NAME},
            [],
            {"value": []},
            {"appId": client_id, "password": "short"},
        ]
        with patch.object(manager, "_run_az_json", side_effect=responses):
            with self.assertRaisesRegex(SafeLoaderError, "no usable secret"):
                manager.create_identity()

    def test_directory_cleanup_discovers_fixed_identity_and_proves_absence(self) -> None:
        object_id = str(uuid.uuid4())
        principal_id = str(uuid.uuid4())
        inventories = [
            ([{"id": object_id}], [{"id": principal_id}]),
            ([], []),
        ]
        with patch.object(manager, "_inventory", side_effect=inventories):
            with patch.object(manager, "delete_identity") as delete:
                with patch.object(manager.time, "sleep") as sleep:
                    manager.cleanup_directory_identity()
        delete.assert_called_once_with(object_id)
        sleep.assert_not_called()

    def test_handoff_always_clears_clipboard(self) -> None:
        with patch.object(manager, "input", side_effect=["", "PASTED", "", "PASTED"]):
            with patch.object(manager, "_clipboard") as copy:
                with patch.object(manager, "clear_clipboard") as clear:
                    with patch.object(manager.threading, "Timer") as timer:
                        manager.handoff_to_tableau("private-client", "private-secret")
        self.assertEqual(copy.call_count, 2)
        self.assertGreaterEqual(clear.call_count, 2)
        self.assertEqual(timer.call_count, 2)

    def test_handoff_can_copy_again_without_printing_private_value(self) -> None:
        output = io.StringIO()
        with patch.object(manager, "input", side_effect=["COPY AGAIN", "PASTED"]):
            with patch.object(manager, "_clipboard") as copy:
                with patch.object(manager, "clear_clipboard") as clear:
                    with patch.object(manager.threading, "Timer") as timer:
                        with redirect_stdout(output):
                            manager._handoff_value("private-secret", "client secret")
        self.assertEqual(copy.call_count, 2)
        self.assertEqual(clear.call_count, 2)
        self.assertEqual(timer.call_count, 2)
        self.assertNotIn("private-secret", output.getvalue())

    def test_handoff_rejects_more_than_three_copies(self) -> None:
        with patch.object(manager, "input", side_effect=["COPY AGAIN"] * 3):
            with patch.object(manager, "_clipboard") as copy:
                with patch.object(manager, "clear_clipboard") as clear:
                    with patch.object(manager.threading, "Timer"):
                        with self.assertRaisesRegex(SafeLoaderError, "copy limit"):
                            manager._handoff_value("private-secret", "client secret")
        self.assertEqual(copy.call_count, 3)
        self.assertEqual(clear.call_count, 3)

    def test_partial_failure_invokes_application_and_database_cleanup(self) -> None:
        admin = MagicMock()
        with patch.object(manager, "_confirmation"):
            with patch.object(manager, "build_dataset_plan"):
                with patch.object(manager, "build_reporting_expectations"):
                    with patch.object(manager, "verify_directory_preflight"):
                        with patch.object(manager, "_admin_connection", return_value=("server", "database", admin)):
                            with patch.object(manager, "verify_database_preflight"):
                                with patch.object(manager, "create_identity", return_value=("object", "client", "secret")):
                                    with patch.object(manager, "bind_database_user"):
                                        with patch.object(manager, "connect_service_principal", side_effect=SafeLoaderError("fail")):
                                            with patch.object(manager, "cleanup_directory_identity") as delete:
                                                with patch.object(manager, "unbind_database_user") as unbind:
                                                    with self.assertRaises(SafeLoaderError):
                                                        manager.execute_create_bind_verify(Path("unused"))
        delete.assert_called_once_with()
        unbind.assert_called_once_with(admin)
        admin.close.assert_called_once()

    def test_service_principal_connection_retries_only_propagation_safe_failures(self) -> None:
        connection = object()
        fake_pyodbc = types.SimpleNamespace(
            drivers=lambda: [ODBC_DRIVER],
            connect=MagicMock(
                side_effect=[RuntimeError("login failed"), RuntimeError("HYT00"), connection]
            ),
        )
        with patch.dict(sys.modules, {"pyodbc": fake_pyodbc}):
            with patch("service_principal_common.time.sleep") as sleep:
                observed = connect_service_principal(
                    "private.example", "private_database", "private-client", "private-secret-value"
                )
        self.assertIs(observed, connection)
        self.assertEqual(fake_pyodbc.connect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        connection_string = fake_pyodbc.connect.call_args.args[0]
        self.assertIn("Authentication=ActiveDirectoryServicePrincipal", connection_string)
        self.assertIn("Connection Timeout=30", connection_string)

        fake_pyodbc.connect.reset_mock(side_effect=True)
        fake_pyodbc.connect.side_effect = RuntimeError("firewall")
        with patch.dict(sys.modules, {"pyodbc": fake_pyodbc}):
            with patch("service_principal_common.time.sleep") as sleep:
                with self.assertRaisesRegex(SafeLoaderError, "FIREWALL"):
                    connect_service_principal(
                        "private.example", "private_database", "private-client", "private-secret-value"
                    )
        self.assertEqual(fake_pyodbc.connect.call_count, 1)
        sleep.assert_not_called()

    def test_source_has_no_secret_file_or_command_line_secret_path(self) -> None:
        source = Path(manager.__file__).read_text(encoding="utf-8")
        self.assertNotIn("write_text", source)
        self.assertNotIn("write_bytes", source)
        self.assertNotIn("--password", source)
        self.assertNotIn("--client-secret", source)
        self.assertNotIn("os.environ", source)


if __name__ == "__main__":
    unittest.main()
