#!/usr/bin/env python3
"""Shared unittest helpers for the public verification contract."""

from __future__ import annotations

import unittest
from pathlib import Path

CANONICAL_DATA_SKIP_REASON = (
    "canonical generated output is intentionally excluded from the public package; "
    "run the synthetic-data generator and independent verifier locally before running "
    "this full-contract test"
)


def canonical_data_available(data_dir: Path) -> bool:
    path = Path(data_dir)
    return path.is_dir() and (path / "manifest.json").is_file()


def requires_canonical_data(data_dir: Path):
    return unittest.skipUnless(
        canonical_data_available(data_dir),
        CANONICAL_DATA_SKIP_REASON,
    )
