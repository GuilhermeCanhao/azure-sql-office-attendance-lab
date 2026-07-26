#!/usr/bin/env python3
"""Offline tests for deterministic Tableau exports and artifact privacy checks."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (TABLEAU_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, SafeLoaderError  # noqa: E402
import export_tableau_data  # noqa: E402
from tableau_common import (  # noqa: E402
    MANIFEST_NAME,
    build_export_bundle,
    validate_export_directory,
    write_export_bundle,
)
import verify_tableau_artifact  # noqa: E402


SAFE_TWB = """<?xml version='1.0' encoding='utf-8'?>
<workbook><datasources>
<datasource caption='Daily aggregate'><connection class='textscan' directory='Data' filename='daily_attendance_trend.csv'/></datasource>
<datasource caption='Department aggregate'><connection class='textscan' directory='Data' filename='daily_department_attendance.csv'/></datasource>
<datasource caption='Load aggregate'><connection class='textscan' directory='Data' filename='load_quality_summary.csv'/></datasource>
<datasource caption='Validation aggregate'><connection class='textscan' directory='Data' filename='validation_issue_summary.csv'/></datasource>
</datasources></workbook>
"""


def unsafe_metadata_fixture(*parts: str) -> str:
    return "".join(parts)


class TableauOfflineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle, cls.manifest = build_export_bundle(DEFAULT_DATA_DIR)

    def test_canonical_bundle_inventory_and_totals(self) -> None:
        self.assertEqual(
            set(self.bundle),
            {
                "daily_attendance_trend.csv",
                "daily_department_attendance.csv",
                "load_quality_summary.csv",
                "validation_issue_summary.csv",
                MANIFEST_NAME,
            },
        )
        self.assertEqual(
            self.manifest["totals"],
            {
                "daily_rows": 261,
                "department_rows": 2087,
                "load_rows": 2,
                "validation_rows": 8,
                "person_days": 37151,
                "received": 134372,
                "accepted": 133892,
                "rejected": 480,
            },
        )

    def test_bundle_is_byte_deterministic(self) -> None:
        second, second_manifest = build_export_bundle(DEFAULT_DATA_DIR)
        self.assertEqual(second, self.bundle)
        self.assertEqual(second_manifest, self.manifest)

    def test_write_and_exact_local_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "exports"
            write_export_bundle(self.bundle, output)
            self.assertEqual(
                validate_export_directory(output, DEFAULT_DATA_DIR),
                self.manifest["totals"],
            )

    def test_export_drift_and_unexpected_file_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "exports"
            write_export_bundle(self.bundle, output)
            (output / "daily_attendance_trend.csv").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(SafeLoaderError, "differs"):
                validate_export_directory(output, DEFAULT_DATA_DIR)
            write_export_bundle(self.bundle, output)
            (output / "unexpected.csv").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(SafeLoaderError, "inventory"):
                validate_export_directory(output, DEFAULT_DATA_DIR)

    def test_export_default_mode_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "exports"
            with patch.object(sys, "argv", ["export_tableau_data.py", "--output-dir", str(output)]):
                captured = io.StringIO()
                with redirect_stdout(captured):
                    self.assertEqual(export_tableau_data.main(), 0)
            self.assertFalse(output.exists())
            self.assertIn("DRY_RUN", captured.getvalue())

    def test_safe_plain_workbook_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "attendance.twb"
            artifact.write_text(SAFE_TWB, encoding="utf-8")
            self.assertEqual(
                verify_tableau_artifact.inspect_tableau_artifact(artifact),
                {"text_files": 1, "data_files": 0},
            )

    def test_packaged_textscan_empty_security_attributes_and_uuid_pass(self) -> None:
        workbook = SAFE_TWB.replace(
            "filename='daily_attendance_trend.csv'/>",
            "filename='daily_attendance_trend.csv' password='' server=''/>\n"
            "<simple-id uuid='{B71BADE2-4A8D-4892-9D5E-131ADBCBCD00}'/>\n"
            "<object-id>[daily_attendance_trend.csv_17AAC5930935476D8D39E82953DA874F]</object-id>\n"
            "<column name='[__tableau_internal_object_id__].[daily_attendance_trend.csv_17AAC5930935476D8D39E82953DA874F]'/>\n"
            "<column name='[__tableau_internal_object_id__].[cnt:daily_attendance_trend.csv_17AAC5930935476D8D39E82953DA874F:qk]'/>",
        )
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "attendance.twb"
            artifact.write_text(workbook, encoding="utf-8")
            self.assertEqual(
                verify_tableau_artifact.inspect_tableau_artifact(artifact),
                {"text_files": 1, "data_files": 0},
            )

    def test_connection_strings_and_nonempty_credentials_are_rejected(self) -> None:
        unsafe_values = (
            unsafe_metadata_fixture(
                "<metadata>Server=tcp:",
                "private.example",
                ";UID=",
                "reporter",
                ";PWD=",
                "unsafe-placeholder",
                ";</metadata>",
            ),
            unsafe_metadata_fixture("<connection pass", "word='unsafe-placeholder'/>"),
            unsafe_metadata_fixture(
                "<object-id>",
                "00000000",
                "-0000-0000-0000-",
                "000000000000",
                "</object-id>",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "attendance.twb"
            for value in unsafe_values:
                artifact.write_text(f"<workbook>{value}</workbook>", encoding="utf-8")
                with self.assertRaises(SafeLoaderError):
                    verify_tableau_artifact.inspect_tableau_artifact(artifact)

    def test_private_connection_and_restricted_fields_are_rejected(self) -> None:
        unsafe_values = (
            unsafe_metadata_fixture(
                "<connection server='",
                "private",
                ".database",
                ".windows",
                ".net'/>",
            ),
            "<column name='PersonnelCode'/>",
            "<relation table='core.DailyAttendanceSummary'/>",
            "<connection username='private-user'/>",
            "<repository-location path='/Users/private/workbook'/>",
        )
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "attendance.twb"
            for value in unsafe_values:
                artifact.write_text(f"<workbook>{value}</workbook>", encoding="utf-8")
                with self.assertRaises(SafeLoaderError):
                    verify_tableau_artifact.inspect_tableau_artifact(artifact)

    def test_package_requires_safe_structure_and_known_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            safe = Path(temporary) / "safe.twbx"
            with zipfile.ZipFile(safe, "w") as archive:
                archive.writestr("attendance.twb", SAFE_TWB)
                for filename in self.manifest["files"]:
                    archive.writestr(f"Data/{filename}", self.bundle[filename])
            self.assertEqual(
                verify_tableau_artifact.inspect_tableau_artifact(
                    safe,
                    {filename: self.bundle[filename] for filename in self.manifest["files"]},
                ),
                {"text_files": 5, "data_files": 4},
            )
            unsafe = Path(temporary) / "unsafe.twbx"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../attendance.twb", SAFE_TWB)
            with self.assertRaisesRegex(SafeLoaderError, "unsafe path"):
                verify_tableau_artifact.inspect_tableau_artifact(unsafe)

            drifted = Path(temporary) / "drifted.twbx"
            with zipfile.ZipFile(drifted, "w") as archive:
                archive.writestr("attendance.twb", SAFE_TWB)
                for filename in self.manifest["files"]:
                    content = b"drift\n" if filename == "load_quality_summary.csv" else self.bundle[filename]
                    archive.writestr(f"Data/{filename}", content)
            with self.assertRaisesRegex(SafeLoaderError, "differs"):
                verify_tableau_artifact.inspect_tableau_artifact(
                    drifted,
                    {filename: self.bundle[filename] for filename in self.manifest["files"]},
                )

    def test_uninspectable_hyper_extract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "attendance.twbx"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("attendance.twb", SAFE_TWB)
                archive.writestr("Data/Extracts/attendance.hyper", b"not-inspectable")
            with self.assertRaisesRegex(SafeLoaderError, "cannot be independently inspected"):
                verify_tableau_artifact.inspect_tableau_artifact(artifact)

    def test_export_and_artifact_tools_have_no_live_client_path(self) -> None:
        for path in (
            TABLEAU_DIR / "tableau_common.py",
            TABLEAU_DIR / "export_tableau_data.py",
            TABLEAU_DIR / "verify_tableau_artifact.py",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("acquire_azure_sql_token", source)
            self.assertNotIn("pyodbc", source)
            self.assertNotIn("az account", source)


if __name__ == "__main__":
    unittest.main()
