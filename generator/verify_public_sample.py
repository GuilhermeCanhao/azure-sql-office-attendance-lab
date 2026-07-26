#!/usr/bin/env python3
"""Verify the committed public sample inventory, privacy boundary, and quality cases."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from create_public_sample import classify_card, classify_wifi
from verify_output import DEVICE_RE, MAC_RE, PERSON_RE, parse_utc


IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
PROHIBITED_TEXT = (
    "database.windows.net", "subscription id", "tenant id", "client secret",
    "connection string", "amazon s3", "internal system", "company-internal",
)


def parse_args() -> argparse.Namespace:
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=directory / "config.json")
    parser.add_argument("--sample", type=Path, default=directory / "sample")
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for value in row.values():
            text = value or ""
            lowered = text.lower()
            require(not MAC_RE.search(text), f"MAC-like identifier found in {path}.")
            require(not IPV4_RE.search(text), f"IPv4 address found in {path}.")
            require(not any(term in lowered for term in PROHIBITED_TEXT), f"Prohibited environment text found in {path}.")
    return rows


def main() -> None:
    args = parse_args()
    sample = args.sample.resolve()
    with args.config.resolve().open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    with (sample / "manifest.json").open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    actual_csvs = {
        path.relative_to(sample).as_posix() for path in sample.rglob("*.csv")
    }
    require(actual_csvs == set(manifest["files"]), "Sample manifest inventory mismatch.")
    for relative, record in manifest["files"].items():
        path = sample / relative
        rows = read_rows(path)
        require(len(rows) == int(record["rows"]), f"Row-count mismatch: {relative}")
        require(sha256_file(path) == record["sha256"], f"Checksum mismatch: {relative}")

    people = read_rows(sample / "reference" / "people.csv")
    devices = read_rows(sample / "reference" / "devices.csv")
    assignments = read_rows(sample / "reference" / "device_assignments.csv")
    access_points = read_rows(sample / "reference" / "access_points.csv")
    person_codes = {row["personnel_code"] for row in people}
    device_tokens = {row["device_token"] for row in devices}
    require(len(people) == 10 and len(person_codes) == 10, "Public sample must contain ten unique people.")
    require(all(PERSON_RE.fullmatch(code) for code in person_codes), "Invalid sample personnel code.")
    require(all(row["synthetic_email"].endswith("@attendance-lab.example") for row in people), "Non-reserved email found.")
    require(all(DEVICE_RE.fullmatch(token) for token in device_tokens), "Non-opaque sample device token.")
    require(all(row["personnel_code"] in person_codes and row["device_token"] in device_tokens for row in assignments), "Sample assignment reference mismatch.")
    assignment_counts = Counter(row["personnel_code"] for row in assignments)
    require(sum(1 for count in assignment_counts.values() if count == 2) == 1, "Sample must include one replacement history.")

    card_points = {row["access_point_code"] for row in access_points if row["access_point_type"] == "CARD_READER"}
    wifi_points = {row["access_point_code"] for row in access_points if row["access_point_type"] == "WIFI_AP"}
    card_rows = read_rows(sample / "source" / "card_events_sample.csv")
    wifi_rows = read_rows(sample / "source" / "wifi_observations_sample.csv")
    require([int(row["source_row_number"]) for row in card_rows] == list(range(1, 21)), "Card sample lineage is not sequential.")
    require([int(row["source_row_number"]) for row in wifi_rows] == list(range(1, 29)), "Wi-Fi sample lineage is not sequential.")

    card_codes = Counter(classify_card(row, person_codes, card_points) for row in card_rows)
    wifi_codes = Counter(
        classify_wifi(
            row, device_tokens, wifi_points,
            int(config["attendance"]["wifi_signal_strength_min"]),
            int(config["attendance"]["wifi_signal_strength_max"]),
        )
        for row in wifi_rows
    )
    require(card_codes.pop("") == 16, "Card sample must contain sixteen valid rows.")
    require(wifi_codes.pop("") == 24, "Wi-Fi sample must contain twenty-four valid rows.")
    require(card_codes == Counter({code: 1 for code in config["anomalies"]["card"]}), "Card anomaly coverage mismatch.")
    require(wifi_codes == Counter({code: 1 for code in config["anomalies"]["wifi"]}), "Wi-Fi anomaly coverage mismatch.")

    expected = read_rows(sample / "expected" / "validation_counts_sample.csv")
    expected_counts = {(row["source_type"], row["validation_code"]): int(row["expected_count"]) for row in expected}
    actual_counts = {("CARD", code): count for code, count in card_codes.items()}
    actual_counts.update({("WIFI", code): count for code, count in wifi_codes.items()})
    require(expected_counts == actual_counts, "Sample validation oracle mismatch.")

    # Verify all parseable sample observations retain explicit UTC timestamps.
    for row in card_rows + wifi_rows:
        if row["observed_at_raw"] != "not-a-timestamp":
            require(parse_utc(row["observed_at_raw"]).utcoffset().total_seconds() == 0, "Non-UTC sample observation.")

    print("SampleFiles People CardRows WifiRows QualityCases ChecksumVerification PrivacyVerification")
    print(len(manifest["files"]), len(people), len(card_rows), len(wifi_rows), 8, "PASS", "PASS")


if __name__ == "__main__":
    main()
