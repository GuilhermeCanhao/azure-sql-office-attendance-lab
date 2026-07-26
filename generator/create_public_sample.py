#!/usr/bin/env python3
"""Create a small deterministic public sample from a verified full output."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from verify_output import DEVICE_RE, parse_utc, verify_output


CARD_HEADER = [
    "source_row_number", "observed_at_raw", "personnel_code_raw", "access_point_code_raw",
]
WIFI_HEADER = [
    "source_row_number", "observed_at_raw", "device_token_raw", "access_point_code_raw",
    "signal_strength_raw",
]
VALIDATION_HEADER = ["source_type", "validation_code", "expected_count"]
REFERENCE_FILES = (
    "offices.csv", "departments.csv", "people.csv", "devices.csv",
    "device_assignments.csv", "access_points.csv",
)


def parse_args() -> argparse.Namespace:
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=directory / "config.json")
    parser.add_argument("--input", type=Path, default=directory / "output" / "run-a")
    parser.add_argument("--output", type=Path, default=directory / "sample")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[List[str], List[dict]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Mapping[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_card(row: Mapping[str, str], people: set[str], card_points: set[str]) -> str:
    try:
        parse_utc(row["observed_at_raw"])
    except ValueError:
        return "INVALID_TIMESTAMP"
    if not row["personnel_code_raw"]:
        return "BLANK_PERSONNEL_CODE"
    if row["personnel_code_raw"] not in people:
        return "UNKNOWN_PERSONNEL"
    if row["access_point_code_raw"] not in card_points:
        return "UNKNOWN_ACCESS_POINT"
    return ""


def classify_wifi(
    row: Mapping[str, str], devices: set[str], wifi_points: set[str], minimum: int, maximum: int
) -> str:
    try:
        parse_utc(row["observed_at_raw"])
    except ValueError:
        return "INVALID_TIMESTAMP"
    if row["device_token_raw"] not in devices:
        return "UNKNOWN_DEVICE"
    if row["access_point_code_raw"] not in wifi_points:
        return "UNKNOWN_ACCESS_POINT"
    try:
        strength = int(row["signal_strength_raw"])
    except ValueError:
        return "INVALID_SIGNAL_STRENGTH"
    if not minimum <= strength <= maximum:
        return "INVALID_SIGNAL_STRENGTH"
    return ""


def choose_rows(
    input_dir: Path,
    selected_people: set[str],
    selected_devices: set[str],
    all_people: set[str],
    all_devices: set[str],
    card_points: set[str],
    wifi_points: set[str],
    config: dict,
) -> tuple[List[dict], List[dict], List[dict]]:
    card_valid: List[dict] = []
    wifi_valid: List[dict] = []
    card_invalid: Dict[str, dict] = {}
    wifi_invalid: Dict[str, dict] = {}

    for path in sorted((input_dir / "card").glob("*.csv")):
        _, rows = read_csv(path)
        for row in rows:
            code = classify_card(row, all_people, card_points)
            if not code and row["personnel_code_raw"] in selected_people and len(card_valid) < 16:
                card_valid.append(row)
            elif code and code not in card_invalid:
                card_invalid[code] = row

    minimum = int(config["attendance"]["wifi_signal_strength_min"])
    maximum = int(config["attendance"]["wifi_signal_strength_max"])
    for path in sorted((input_dir / "wifi").glob("*.csv")):
        _, rows = read_csv(path)
        for row in rows:
            code = classify_wifi(row, all_devices, wifi_points, minimum, maximum)
            if not code and row["device_token_raw"] in selected_devices and len(wifi_valid) < 24:
                wifi_valid.append(row)
            elif code and code not in wifi_invalid:
                wifi_invalid[code] = row

    expected_card_codes = set(config["anomalies"]["card"])
    expected_wifi_codes = set(config["anomalies"]["wifi"])
    if len(card_valid) != 16 or set(card_invalid) != expected_card_codes:
        raise ValueError("Could not construct the required card sample.")
    if len(wifi_valid) != 24 or set(wifi_invalid) != expected_wifi_codes:
        raise ValueError("Could not construct the required Wi-Fi sample.")

    card_sample = card_valid + [card_invalid[code] for code in sorted(card_invalid)]
    wifi_sample = wifi_valid + [wifi_invalid[code] for code in sorted(wifi_invalid)]
    for index, row in enumerate(card_sample, start=1):
        row["source_row_number"] = str(index)
    for index, row in enumerate(wifi_sample, start=1):
        row["source_row_number"] = str(index)

    validations = [
        {"source_type": "CARD", "validation_code": code, "expected_count": 1}
        for code in sorted(card_invalid)
    ] + [
        {"source_type": "WIFI", "validation_code": code, "expected_count": 1}
        for code in sorted(wifi_invalid)
    ]
    return card_sample, wifi_sample, validations


def create_sample(config_path: Path, input_dir: Path, output_dir: Path, clean: bool) -> dict:
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    full_manifest = verify_output(config, input_dir)

    if output_dir.exists():
        if not clean:
            raise FileExistsError(f"Sample already exists: {output_dir}. Use --clean to replace it.")
        for generated_name in ("reference", "source", "expected"):
            generated_path = output_dir / generated_name
            if generated_path.exists():
                shutil.rmtree(generated_path)
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            manifest_path.unlink()
    (output_dir / "reference").mkdir(parents=True)
    (output_dir / "source").mkdir(parents=True)
    (output_dir / "expected").mkdir(parents=True)

    headers: Dict[str, List[str]] = {}
    references: Dict[str, List[dict]] = {}
    for name in REFERENCE_FILES:
        headers[name], references[name] = read_csv(input_dir / "reference" / name)

    assignment_counts = Counter(row["personnel_code"] for row in references["device_assignments.csv"])
    replacement_person = sorted(code for code, count in assignment_counts.items() if count == 2)[0]
    selected_codes = [row["personnel_code"] for row in references["people.csv"][:10]]
    if replacement_person not in selected_codes:
        selected_codes[-1] = replacement_person
    selected_people = set(selected_codes)

    selected_assignments = [
        row for row in references["device_assignments.csv"]
        if row["personnel_code"] in selected_people
    ]
    selected_devices = {row["device_token"] for row in selected_assignments}
    require_tokens = all(DEVICE_RE.fullmatch(token) for token in selected_devices)
    if not require_tokens:
        raise ValueError("Selected sample contains a non-opaque device token.")

    sample_references = {
        "offices.csv": references["offices.csv"],
        "departments.csv": references["departments.csv"],
        "people.csv": [row for row in references["people.csv"] if row["personnel_code"] in selected_people],
        "devices.csv": [row for row in references["devices.csv"] if row["device_token"] in selected_devices],
        "device_assignments.csv": selected_assignments,
        "access_points.csv": references["access_points.csv"],
    }
    for name, rows in sample_references.items():
        write_csv(output_dir / "reference" / name, headers[name], rows)

    all_people = {row["personnel_code"] for row in references["people.csv"]}
    all_devices = {row["device_token"] for row in references["devices.csv"]}
    card_points = {
        row["access_point_code"] for row in references["access_points.csv"]
        if row["access_point_type"] == "CARD_READER"
    }
    wifi_points = {
        row["access_point_code"] for row in references["access_points.csv"]
        if row["access_point_type"] == "WIFI_AP"
    }
    card_rows, wifi_rows, validations = choose_rows(
        input_dir, selected_people, selected_devices, all_people, all_devices,
        card_points, wifi_points, config,
    )
    write_csv(output_dir / "source" / "card_events_sample.csv", CARD_HEADER, card_rows)
    write_csv(output_dir / "source" / "wifi_observations_sample.csv", WIFI_HEADER, wifi_rows)
    write_csv(output_dir / "expected" / "validation_counts_sample.csv", VALIDATION_HEADER, validations)

    files = {}
    for path in sorted(output_dir.rglob("*.csv")):
        relative = path.relative_to(output_dir).as_posix()
        _, rows = read_csv(path)
        files[relative] = {"rows": len(rows), "sha256": sha256_file(path)}
    manifest = {
        "contract_version": config["contract_version"],
        "source_manifest_sha256": sha256_file(input_dir / "manifest.json"),
        "selection": {
            "people": len(sample_references["people.csv"]),
            "includes_replacement_history": True,
            "valid_card_rows": 16,
            "invalid_card_rows": 4,
            "valid_wifi_rows": 24,
            "invalid_wifi_rows": 4,
        },
        "source_totals": full_manifest["totals"],
        "files": files,
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main() -> None:
    args = parse_args()
    manifest = create_sample(
        args.config.resolve(), args.input.resolve(), args.output.resolve(), args.clean
    )
    print(f"Sample: {args.output.resolve()}")
    print(f"People: {manifest['selection']['people']}")
    print("Card rows: 20 (16 valid, 4 controlled invalid)")
    print("Wi-Fi rows: 28 (24 valid, 4 controlled invalid)")


if __name__ == "__main__":
    main()
