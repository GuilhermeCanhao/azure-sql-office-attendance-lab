#!/usr/bin/env python3
"""Regenerate deterministic local data and run the offline test suite."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
RUN_A = PROJECT_ROOT / "generator" / "output" / "run-a"
RUN_B = PROJECT_ROOT / "generator" / "output" / "run-b"

TEST_MODULES = [
    "loader.test_loader",
    "reporting.test_reporting",
    "performance.test_performance",
    "monitoring.test_monitoring",
    "recovery.test_recovery",
    "tableau.test_tableau",
    "tableau.test_service_principal",
]


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=PROJECT_ROOT, check=True)


def remove_generated_outputs() -> None:
    for path in (RUN_A, RUN_B):
        if path.exists():
            shutil.rmtree(path)


def main() -> int:
    remove_generated_outputs()
    run([PYTHON, "generator/generate_data.py", "--output", str(RUN_A), "--clean"])
    run([PYTHON, "generator/verify_output.py", "--output", str(RUN_A)])
    run([PYTHON, "generator/generate_data.py", "--output", str(RUN_B), "--clean"])
    run(
        [
            PYTHON,
            "generator/verify_output.py",
            "--output",
            str(RUN_A),
            "--compare",
            str(RUN_B),
        ]
    )
    run([PYTHON, "-m", "unittest", *TEST_MODULES])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
