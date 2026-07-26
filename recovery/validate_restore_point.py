#!/usr/bin/env python3
"""Privately compare the captured checkpoint with the portal review timestamp."""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path


RECOVERY_DIR = Path(__file__).resolve().parent
LOADER_DIR = RECOVERY_DIR.parent / "loader"
for directory in (RECOVERY_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import SafeLoaderError, safe_main_error  # noqa: E402
from recovery_common import parse_private_utc, validate_portal_restore_point  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute-compare",
        action="store_true",
        help="Privately prompt for and compare the captured and portal-reviewed UTC values.",
    )
    return parser.parse_args()


def private_timestamp(label: str) -> datetime:
    try:
        value = getpass.getpass(f"Privately enter the {label} UTC timestamp: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise SafeLoaderError("Private restore-point comparison was not completed.") from exc
    return parse_private_utc(value)


def main() -> int:
    try:
        args = parse_args()
        if not args.execute_compare:
            validate_portal_restore_point(
                parse_private_utc("2026-01-01T12:34:56.789000Z"),
                parse_private_utc("2026-01-01 12:34:56 UTC"),
            )
            print("Phase 8 restore-point comparator: PASS")
            print("Mode: DRY_RUN — no private timestamp was requested.")
            return 0
        captured = private_timestamp("captured checkpoint")
        reviewed = private_timestamp("portal Review + create")
        validate_portal_restore_point(captured, reviewed)
        print("Phase 8 restore-point comparator: PASS ExactSecondMatch=1")
        return 0
    except Exception as exc:
        print(f"Phase 8 restore-point comparator: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
