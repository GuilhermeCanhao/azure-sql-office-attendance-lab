#!/usr/bin/env python3
"""Offline tests for loader contract, privacy, and safety helpers."""

from __future__ import annotations

import json
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


LOADER_DIR = Path(__file__).resolve().parent
if str(LOADER_DIR) not in sys.path:
    sys.path.insert(0, str(LOADER_DIR))

from loader_common import (  # noqa: E402
    DEFAULT_DATA_DIR,
    SafeLoaderError,
    build_dataset_plan,
    build_reference_payload,
    classify_database_error,
    runtime_target,
    safe_main_error,
    validate_dry_run,
)
import load_data  # noqa: E402


class LoaderContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_dataset_plan(DEFAULT_DATA_DIR)

    def test_canonical_plan_and_chunk_guardrail(self) -> None:
        result = validate_dry_run(self.plan)
        self.assertEqual(result, {"batches": 24, "rows": 134372, "chunks": 149})

    def test_reference_payload_contains_six_complete_arrays(self) -> None:
        payload = json.loads(build_reference_payload(self.plan))
        self.assertEqual(
            {key: len(value) for key, value in payload.items()},
            {
                "offices": 1,
                "departments": 8,
                "people": 300,
                "devices": 315,
                "device_assignments": 315,
                "access_points": 5,
            },
        )
        self.assertIsNone(payload["people"][0]["valid_to"])

    def test_runtime_target_is_required_and_validated_without_display(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SafeLoaderError, "Runtime target is missing"):
                runtime_target()
        with patch.dict(
            os.environ,
            {
                "ATTENDANCE_SQL_SERVER": "invalid-runtime-target;Encrypt=no",
                "ATTENDANCE_SQL_DATABASE": "lab",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(SafeLoaderError, "format is invalid") as raised:
                runtime_target()
            self.assertNotIn("invalid-runtime-target", str(raised.exception))

    def test_database_errors_are_reduced_to_safe_categories(self) -> None:
        self.assertEqual(classify_database_error(Exception("40615 firewall address suppressed")), "FIREWALL")
        self.assertEqual(classify_database_error(Exception("40613 temporarily unavailable")), "DATABASE_NOT_READY")
        self.assertEqual(classify_database_error(Exception("18456 login failed for user")), "AUTHENTICATION")
        self.assertEqual(classify_database_error(Exception("certificate verify failed")), "TLS_VALIDATION")

    def test_unexpected_exception_text_is_suppressed(self) -> None:
        message = safe_main_error(Exception("token endpoint account tenant subscription"))
        self.assertNotIn("token", message.lower())
        self.assertNotIn("tenant", message.lower())
        self.assertIn("safely suppressed", message)

    def test_loader_uses_controlled_batch_result_interface(self) -> None:
        source = Path(load_data.__file__).read_text(encoding="utf-8")
        self.assertIn("stage.usp_GetImportBatchResult", source)
        self.assertNotIn("FROM stage.ImportBatch", source)

    def test_existing_batch_verification_uses_id_and_checksum(self) -> None:
        batch = self.plan.batches[0]
        cursor = Mock()
        cursor.description = [
            ("SourceType",),
            ("SourceFileName",),
            ("FileChecksumHex",),
            ("Status",),
            ("RowsReceived",),
            ("RowsAccepted",),
            ("RowsRejected",),
        ]
        cursor.fetchone.return_value = (
            batch.source_type,
            batch.source_file_name,
            batch.file_sha256.upper(),
            batch.expected_status,
            batch.rows_received,
            batch.rows_accepted,
            batch.rows_rejected,
        )
        connection = Mock()
        connection.cursor.return_value = cursor

        load_data._verify_existing_batch(connection, batch, 123)

        sql, batch_id, checksum = cursor.execute.call_args.args
        self.assertIn("stage.usp_GetImportBatchResult", sql)
        self.assertEqual(batch_id, 123)
        self.assertEqual(checksum, bytes.fromhex(batch.file_sha256))

    def test_source_load_skips_bootstrap_and_independent_verifier(self) -> None:
        connection = object()
        with patch.object(load_data, "process_batch", return_value="ALREADY_PROCESSED") as process:
            with patch.object(load_data, "refresh_daily_summary") as refresh:
                with patch.object(load_data, "bootstrap_references") as bootstrap:
                    with redirect_stdout(io.StringIO()):
                        result = load_data.execute_source_load(connection, self.plan)

        self.assertEqual(process.call_count, 24)
        refresh.assert_called_once_with(connection, 37151)
        bootstrap.assert_not_called()
        self.assertEqual(
            result,
            {"outcomes": {"ALREADY_PROCESSED": 24, "COMPLETED": 0, "PARTIAL": 0}},
        )

    def test_execution_modes_are_mutually_exclusive(self) -> None:
        with patch.object(sys, "argv", ["load_data.py", "--execute-load", "--execute-source-load"]):
            with patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    load_data.parse_args()


if __name__ == "__main__":
    unittest.main()
