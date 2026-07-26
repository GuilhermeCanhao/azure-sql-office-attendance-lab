#!/usr/bin/env python3
"""Build or validate deterministic aggregate files for the public Tableau workbook."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (TABLEAU_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import DEFAULT_DATA_DIR, safe_main_error  # noqa: E402
from tableau_common import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    build_export_bundle,
    validate_export_directory,
    write_export_bundle,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Canonical generated-data directory (not printed).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Local aggregate-export directory (not printed).",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--write-exports", action="store_true",
        help="Write the deterministic aggregate bundle locally.",
    )
    action.add_argument(
        "--verify-exports", action="store_true",
        help="Verify an existing aggregate bundle byte for byte.",
    )
    return parser.parse_args()


def _result_line(totals: dict) -> str:
    return (
        "DailyRows={daily_rows} DepartmentRows={department_rows} "
        "LoadRows={load_rows} ValidationRows={validation_rows} "
        "PersonDays={person_days} Received={received} Accepted={accepted} "
        "Rejected={rejected}"
    ).format(**totals)


def main() -> int:
    try:
        args = parse_args()
        if args.verify_exports:
            totals = validate_export_directory(args.output_dir, args.data_dir)
            print(f"Tableau aggregate export verification: PASS {_result_line(totals)}")
            print("Mode: VERIFY_LOCAL — Azure CLI, Azure SQL, and Tableau were not accessed.")
            return 0

        bundle, manifest = build_export_bundle(args.data_dir)
        if args.write_exports:
            write_export_bundle(bundle, args.output_dir)
            validate_export_directory(args.output_dir, args.data_dir)
            print(f"Tableau aggregate export: PASS {_result_line(manifest['totals'])}")
            print("Mode: WRITE_LOCAL — only deterministic local aggregate files changed.")
            return 0

        print(f"Tableau aggregate export plan: PASS {_result_line(manifest['totals'])}")
        print("Mode: DRY_RUN — no files, Azure service, or Tableau artifact changed.")
        return 0
    except Exception as exc:
        print(f"Tableau aggregate export: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
