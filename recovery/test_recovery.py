#!/usr/bin/env python3
"""Offline tests for the Phase 8 recovery and privacy contracts."""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


RECOVERY_DIR = Path(__file__).resolve().parent
LOADER_DIR = RECOVERY_DIR.parent / "loader"
for directory in (RECOVERY_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import SafeLoaderError  # noqa: E402
from recovery_common import (  # noqa: E402
    APPROVED_POLICY,
    confirm_database,
    runtime_databases,
    validate_live_control_state,
    validate_policy,
)
import manage_recovery_marker  # noqa: E402
import validate_restore_point  # noqa: E402
import verify_recovery  # noqa: E402
from recovery_common import parse_private_utc, validate_portal_restore_point  # noqa: E402


FICTIONAL_SERVER = ".".join(("fictional", "database", "windows", "net"))


class RecoveryContractTests(unittest.TestCase):
    def test_frozen_policy_and_oracles_pass(self) -> None:
        self.assertEqual(validate_policy()["target_edition"], "Basic")
        expected = verify_recovery.offline_expectations(
            verify_recovery.DEFAULT_DATA_DIR
        )
        self.assertEqual(
            (expected["batches"], expected["accepted"], expected["rejected"], expected["person_days"]),
            (24, 133892, 480, 37151),
        )
        self.assertEqual((expected["daily_rows"], expected["department_rows"]), (261, 2087))

    def test_policy_rejects_tier_size_time_and_cost_drift(self) -> None:
        for drifted in (
            replace(APPROVED_POLICY, target_edition="GeneralPurpose"),
            replace(APPROVED_POLICY, observed_source_bytes=APPROVED_POLICY.maximum_target_bytes),
            replace(APPROVED_POLICY, restore_limit_minutes=120),
            replace(APPROVED_POLICY, cost_ceiling_eur=6),
        ):
            with self.assertRaises(SafeLoaderError):
                validate_policy(drifted)

    def test_live_control_state_requires_clean_inventory_and_cost(self) -> None:
        state = {
            "user_database_count": 1, "temporary_database_count": 0,
            "source_backup_redundancy": "Local", "short_term_retention_days": 7,
            "differential_backup_hours": 12, "long_term_retention_policies": 0,
            "restore_window_available": True, "source_bytes": 114_819_072,
            "actual_cost_eur": 0.0, "forecast_cost_eur": None,
        }
        validate_live_control_state(state)
        with self.assertRaisesRegex(SafeLoaderError, "temporary restore"):
            validate_live_control_state(dict(state, temporary_database_count=1))
        with self.assertRaisesRegex(SafeLoaderError, "Forecast"):
            validate_live_control_state(dict(state, forecast_cost_eur=5.01))

    def test_marker_sql_has_guards_and_client_owned_transaction(self) -> None:
        self.assertEqual(
            manage_recovery_marker.validate_marker_contract(),
            {"create_guards": 9, "cleanup_guards": 5},
        )
        self.assertNotIn("COMMIT", manage_recovery_marker.CREATE_MARKER_SQL.upper())
        self.assertNotIn("COMMIT", manage_recovery_marker.DROP_MARKER_SQL.upper())

    def test_runtime_requires_explicit_distinct_database_names(self) -> None:
        values = {
            "ATTENDANCE_SQL_SERVER": FICTIONAL_SERVER,
            "ATTENDANCE_SQL_SOURCE_DATABASE": "source_lab",
            "ATTENDANCE_SQL_RESTORE_DATABASE": "restore_lab",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                runtime_databases(require_restore=True),
                (values["ATTENDANCE_SQL_SERVER"], "source_lab", "restore_lab"),
            )
        with patch.dict(os.environ, dict(values, ATTENDANCE_SQL_RESTORE_DATABASE="source_lab"), clear=True):
            with self.assertRaisesRegex(SafeLoaderError, "different"):
                runtime_databases(require_restore=True)

    def test_confirmation_must_exactly_match(self) -> None:
        confirm_database("source_lab", "source_lab", "source")
        with self.assertRaisesRegex(SafeLoaderError, "does not match"):
            confirm_database("source_lab", "other_lab", "source")

    def test_restore_point_requires_exact_reviewed_second(self) -> None:
        captured = parse_private_utc("2026-01-01T12:34:56.789000Z")
        validate_portal_restore_point(
            captured, parse_private_utc("2026-01-01 12:34:56 UTC")
        )
        with self.assertRaisesRegex(SafeLoaderError, "does not exactly match"):
            validate_portal_restore_point(
                captured, parse_private_utc("2026-01-01 12:35:00 UTC")
            )

    def test_private_timestamp_parser_rejects_ambiguous_values(self) -> None:
        with self.assertRaisesRegex(SafeLoaderError, "unsupported format"):
            parse_private_utc("01/01/2026 12:34")

    def test_restore_point_comparator_default_is_offline(self) -> None:
        with patch.object(sys, "argv", ["validate_restore_point.py"]):
            with patch.object(validate_restore_point.getpass, "getpass") as prompt:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(validate_restore_point.main(), 0)
        prompt.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_cleanup_requires_restore_deletion_confirmation(self) -> None:
        args = manage_recovery_marker.parse_args
        argv = [
            "manage_recovery_marker.py", "--execute-remove-marker",
        ]
        with patch.object(sys, "argv", argv):
            parsed = args()
        self.assertTrue(parsed.execute_remove_marker)
        self.assertFalse(parsed.confirm_restore_deleted)

    def test_marker_default_mode_is_offline(self) -> None:
        with patch.object(sys, "argv", ["manage_recovery_marker.py"]):
            with patch.object(manage_recovery_marker, "runtime_databases") as runtime:
                with patch.object(manage_recovery_marker, "acquire_azure_sql_token") as token:
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(manage_recovery_marker.main(), 0)
        runtime.assert_not_called()
        token.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_verifier_default_mode_is_offline(self) -> None:
        with patch.object(sys, "argv", ["verify_recovery.py"]):
            with patch.object(verify_recovery, "runtime_databases") as runtime:
                with patch.object(verify_recovery, "acquire_azure_sql_token") as token:
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(verify_recovery.main(), 0)
        runtime.assert_not_called()
        token.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())
        self.assertNotIn("database.windows.net", output.getvalue())

    def test_restored_mode_requires_target_audit_confirmation(self) -> None:
        values = {
            "ATTENDANCE_SQL_SERVER": FICTIONAL_SERVER,
            "ATTENDANCE_SQL_SOURCE_DATABASE": "source_lab",
            "ATTENDANCE_SQL_RESTORE_DATABASE": "restore_lab",
        }
        argv = [
            "verify_recovery.py", "--execute-restored-target",
        ]
        fake_connection = object()
        with patch.dict(os.environ, values, clear=True), patch.object(sys, "argv", argv):
            with patch.object(verify_recovery, "acquire_azure_sql_token", return_value=b"token"):
                with patch.object(verify_recovery, "connect_ready_target", return_value=fake_connection):
                    with patch.object(verify_recovery, "offline_expectations", return_value={}):
                        with patch.object(verify_recovery, "private_database_confirmation", return_value="source_lab"):
                            errors = io.StringIO()
                            with redirect_stderr(errors):
                                self.assertEqual(verify_recovery.main(), 1)
        self.assertIn("audit inspection confirmation", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
