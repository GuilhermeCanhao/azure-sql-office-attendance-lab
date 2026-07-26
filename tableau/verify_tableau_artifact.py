#!/usr/bin/env python3
"""Offline structural and privacy verifier for a Tableau workbook or package."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional, Set, Tuple


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (TABLEAU_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, SafeLoaderError, safe_main_error  # noqa: E402
from tableau_common import DEFAULT_OUTPUT_DIR, EXPORT_COLUMNS, validate_export_directory  # noqa: E402


MAX_ARCHIVE_FILES = 200
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
TEXT_SUFFIXES = {".twb", ".tds", ".xml", ".txt", ".json", ".csv"}
PACKAGE_SUFFIXES = {".twbx", ".tdsx"}
PLAIN_SUFFIXES = {".twb", ".tds"}

FORBIDDEN_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("Azure SQL endpoint", re.compile(r"database\.windows\.net", re.IGNORECASE)),
    ("IP address", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("private local path", re.compile(r"(?:/Users/|/home/|[A-Z]:\\Users\\)", re.IGNORECASE)),
    (
        "connection string",
        re.compile(
            r"(?:(?<![\w-])(?:Data Source|Initial Catalog|UID|PWD)\s*=|"
            r"(?<![\w-])Server\s*=\s*(?!['\"]))",
            re.IGNORECASE,
        ),
    ),
    (
        "credential metadata",
        re.compile(
            r"(?:client.?secret|access.?token|refresh.?token|"
            r"password\s*=\s*(?:[^'\"\s]|['\"][^'\"]+['\"]))",
            re.IGNORECASE,
        ),
    ),
    ("account metadata", re.compile(r"(?:tenant|subscription|account|object)[_ -]?id", re.IGNORECASE)),
    ("live database connector", re.compile(r"class\s*=\s*['\"](?:sqlserver|azure_sql)['\"]", re.IGNORECASE)),
    ("server attribute", re.compile(r"server\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE)),
    ("username attribute", re.compile(r"username\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE)),
    ("restricted schema", re.compile(r"\b(?:stage|core)\.\w+", re.IGNORECASE)),
    (
        "restricted field",
        re.compile(
            r"\b(?:PersonId|PersonnelCode|SyntheticEmail|DeviceToken|AccessPointId|"
            r"ImportBatchId|SourceFileName|SourceRowNumber|FileSha256|ObservedAtUtc)\b",
            re.IGNORECASE,
        ),
    ),
)

APPROVED_CSV_NAME_PATTERN = "|".join(re.escape(name) for name in EXPORT_COLUMNS)
SAFE_TABLEAU_OBJECT_ID_PATTERN = re.compile(
    rf"(?:<object-id>\[(?:{APPROVED_CSV_NAME_PATTERN})_[A-F0-9]{{32}}\]</object-id>|"
    rf"\[__tableau_internal_object_id__\]\.\[(?:cnt:)?(?:{APPROVED_CSV_NAME_PATTERN})_"
    rf"[A-F0-9]{{32}}(?::qk)?\])",
    re.IGNORECASE,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _inspect_text(label: str, content: str) -> Set[str]:
    inspected_content = SAFE_TABLEAU_OBJECT_ID_PATTERN.sub("", content)
    for description, pattern in FORBIDDEN_PATTERNS:
        require(
            not pattern.search(inspected_content),
            f"The Tableau artifact contains {description} metadata.",
        )
    mentioned = set(re.findall(r"[A-Za-z0-9_-]+\.csv", content, re.IGNORECASE))
    require(
        mentioned <= set(EXPORT_COLUMNS),
        f"The Tableau artifact contains an unexpected CSV reference in {label}.",
    )
    return mentioned


def _safe_archive_entries(archive: zipfile.ZipFile) -> Iterable[zipfile.ZipInfo]:
    entries = archive.infolist()
    require(len(entries) <= MAX_ARCHIVE_FILES, "The Tableau package contains too many files.")
    total_size = sum(entry.file_size for entry in entries)
    require(total_size <= MAX_ARCHIVE_BYTES, "The Tableau package is unexpectedly large.")
    for entry in entries:
        path = PurePosixPath(entry.filename)
        require(not path.is_absolute() and ".." not in path.parts, "The Tableau package has an unsafe path.")
        require(not (entry.flag_bits & 0x1), "The Tableau package contains an encrypted entry.")
        unix_mode = entry.external_attr >> 16
        require((unix_mode & 0o170000) != 0o120000, "The Tableau package contains a symlink.")
        yield entry


def inspect_tableau_artifact(
    artifact: Path, expected_csvs: Optional[Mapping[str, bytes]] = None
) -> dict:
    require(artifact.is_file() and not artifact.is_symlink(), "The Tableau artifact is not a regular file.")
    suffix = artifact.suffix.lower()
    require(suffix in PLAIN_SUFFIXES | PACKAGE_SUFFIXES, "The Tableau artifact type is unsupported.")
    text_files = 0
    data_files = 0
    mentioned_sources: Set[str] = set()
    packaged_sources: Set[str] = set()
    if suffix in PLAIN_SUFFIXES:
        mentioned_sources |= _inspect_text(
            artifact.name, artifact.read_text(encoding="utf-8")
        )
        text_files = 1
    else:
        require(zipfile.is_zipfile(artifact), "The Tableau package is not a valid ZIP archive.")
        workbook_suffix = ".twb" if suffix == ".twbx" else ".tds"
        workbook_files = 0
        with zipfile.ZipFile(artifact, "r") as archive:
            for entry in _safe_archive_entries(archive):
                if entry.is_dir():
                    continue
                entry_suffix = Path(entry.filename).suffix.lower()
                if entry_suffix == ".hyper":
                    raise SafeLoaderError(
                        "A Hyper extract is present but cannot be independently inspected by the project-local verifier."
                    )
                if entry_suffix in TEXT_SUFFIXES:
                    try:
                        content = archive.read(entry).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise SafeLoaderError("A Tableau package text entry is not valid UTF-8.") from exc
                    mentioned_sources |= _inspect_text(entry.filename, content)
                    text_files += 1
                    if entry_suffix == ".csv":
                        filename = Path(entry.filename).name
                        require(
                            filename in EXPORT_COLUMNS,
                            "The Tableau package contains an unexpected CSV source.",
                        )
                        require(filename not in packaged_sources, "A Tableau CSV source is duplicated.")
                        require(expected_csvs is not None, "Packaged CSV bytes have no independent oracle.")
                        require(
                            archive.read(entry) == expected_csvs.get(filename),
                            "A packaged Tableau CSV differs from the independent aggregate export.",
                        )
                        packaged_sources.add(filename)
                        data_files += 1
                if entry_suffix == workbook_suffix:
                    workbook_files += 1
        require(workbook_files == 1, "The Tableau package must contain exactly one workbook definition.")
        require(
            packaged_sources == set(EXPORT_COLUMNS),
            "The Tableau package must contain exactly the four approved aggregate CSV sources.",
        )
    require(
        mentioned_sources == set(EXPORT_COLUMNS),
        "The Tableau workbook must reference exactly the four approved aggregate CSV sources.",
    )
    return {"text_files": text_files, "data_files": data_files}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, help="Local .twb, .twbx, .tds, or .tdsx artifact.")
    parser.add_argument(
        "--export-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Deterministic aggregate-export directory (not printed).",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Canonical generated-data directory (not printed).",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.artifact is None:
            print("Tableau artifact privacy policy: PASS")
            print("Mode: DRY_RUN — no artifact was opened and no external service was accessed.")
            return 0
        totals = validate_export_directory(args.export_dir, args.data_dir)
        expected_csvs = {
            filename: (args.export_dir / filename).read_bytes()
            for filename in EXPORT_COLUMNS
        }
        result = inspect_tableau_artifact(args.artifact, expected_csvs)
        print(
            "Tableau artifact privacy verification: PASS "
            f"TextFiles={result['text_files']} DataFiles={result['data_files']} "
            f"DailyRows={totals['daily_rows']} DepartmentRows={totals['department_rows']}"
        )
        print("Mode: VERIFY_LOCAL — Azure SQL and Tableau Public were not accessed.")
        return 0
    except Exception as exc:
        print(f"Tableau artifact privacy verification: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
