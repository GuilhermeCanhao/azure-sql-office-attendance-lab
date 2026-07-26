#!/usr/bin/env python3
"""Independently verify generated attendance files against the data contract."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


UTC = timezone.utc
PERSON_RE = re.compile(r"^PER-\d{4}$")
DEVICE_RE = re.compile(r"^DEV-[0-9A-F]{8}$")
MAC_RE = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")

REFERENCE_HEADERS = {
    "offices.csv": ["office_code", "display_name", "time_zone_name", "capacity", "is_active"],
    "departments.csv": ["department_code", "department_name", "is_active"],
    "people.csv": [
        "personnel_code", "display_name", "synthetic_email", "department_code",
        "valid_from", "valid_to",
    ],
    "devices.csv": ["device_token", "device_status"],
    "device_assignments.csv": [
        "personnel_code", "device_token", "valid_from_utc", "valid_to_utc",
    ],
    "access_points.csv": [
        "office_code", "access_point_code", "access_point_type", "display_label", "is_active",
    ],
}
CARD_HEADER = [
    "source_row_number", "observed_at_raw", "personnel_code_raw", "access_point_code_raw",
]
WIFI_HEADER = [
    "source_row_number", "observed_at_raw", "device_token_raw", "access_point_code_raw",
    "signal_strength_raw",
]
EXPECTED_DAILY_HEADER = [
    "attendance_date_local", "personnel_code", "detection_method", "first_observed_at_utc",
    "last_observed_at_utc", "card_signal_count", "wifi_signal_count",
]
EXPECTED_BATCH_HEADER = [
    "source_type", "source_file_name", "rows_received", "rows_accepted", "rows_rejected",
    "file_sha256",
]
EXPECTED_VALIDATION_HEADER = [
    "source_type", "source_file_name", "validation_code", "expected_count",
]


def parse_args() -> argparse.Namespace:
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=directory / "config.json")
    parser.add_argument("--output", type=Path, default=directory / "output")
    parser.add_argument(
        "--compare",
        type=Path,
        help="Optional second output directory that must be byte-for-byte deterministic.",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path, expected_header: Sequence[str]) -> List[dict]:
    require(path.is_file(), f"Missing required file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames == list(expected_header), f"Unexpected header in {path}")
        rows = list(reader)
    for row in rows:
        for value in row.values():
            require(not MAC_RE.search(value or ""), f"MAC-like value found in {path}")
            lowered = (value or "").lower()
            require("database.windows.net" not in lowered, f"Azure endpoint found in {path}")
    return rows


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, f"Timestamp has no timezone: {value}")
    return parsed.astimezone(UTC)


def month_keys(start_date: date, end_date: date) -> List[str]:
    current = date(start_date.year, start_date.month, 1)
    result: List[str] = []
    while current <= end_date:
        result.append(current.strftime("%Y_%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return result


def verify_manifest_files(output: Path, manifest: dict) -> None:
    actual_paths = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    recorded_paths = set(manifest["files"])
    require(actual_paths == recorded_paths, "Manifest file inventory does not match output files.")
    for relative in sorted(actual_paths):
        path = output / relative
        record = manifest["files"][relative]
        require(sha256_file(path) == record["sha256"], f"Checksum mismatch: {relative}")
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                row_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)
            require(row_count == int(record["rows"]), f"Row-count mismatch: {relative}")


def verify_reference(output: Path, config: dict, manifest: dict) -> dict:
    rows = {
        name: read_csv(output / "reference" / name, header)
        for name, header in REFERENCE_HEADERS.items()
    }
    expected_counts = {
        "offices": 1,
        "departments": len(config["departments"]),
        "people": int(config["population"]["people"]),
        "devices": int(config["population"]["people"]) + int(config["population"]["replacement_devices"]),
        "device_assignments": int(config["population"]["people"]) + int(config["population"]["replacement_devices"]),
        "access_points": len(config["access_points"]["card_readers"]) + len(config["access_points"]["wifi_access_points"]),
    }
    for key, expected in expected_counts.items():
        filename = "device_assignments.csv" if key == "device_assignments" else f"{key}.csv"
        require(len(rows[filename]) == expected, f"Unexpected reference count for {key}.")
        require(int(manifest["reference_counts"][key]) == expected, f"Manifest reference count mismatch for {key}.")

    offices = rows["offices.csv"]
    require(offices[0]["office_code"] == config["office"]["office_code"], "Office code mismatch.")
    require(int(offices[0]["capacity"]) == int(config["office"]["capacity"]), "Office capacity mismatch.")

    configured_departments = {item["code"]: int(item["people"]) for item in config["departments"]}
    people = rows["people.csv"]
    department_counts = Counter(row["department_code"] for row in people)
    require(department_counts == Counter(configured_departments), "Department headcounts do not match config.")
    person_codes = {row["personnel_code"] for row in people}
    require(len(person_codes) == len(people), "Duplicate personnel code.")
    email_domain = config["population"]["email_domain"]
    for row in people:
        require(PERSON_RE.fullmatch(row["personnel_code"]) is not None, "Invalid personnel code.")
        require(row["synthetic_email"].endswith(f"@{email_domain}"), "Email outside reserved synthetic domain.")

    devices = rows["devices.csv"]
    device_tokens = {row["device_token"] for row in devices}
    require(len(device_tokens) == len(devices), "Duplicate device token.")
    require(all(DEVICE_RE.fullmatch(token) for token in device_tokens), "Invalid opaque device token.")

    access_points = rows["access_points.csv"]
    access_point_types = {row["access_point_code"]: row["access_point_type"] for row in access_points}
    require(len(access_point_types) == len(access_points), "Duplicate access-point code.")

    assignments_by_device: Dict[str, List[Tuple[datetime, Optional[datetime], str]]] = defaultdict(list)
    assignments_by_person: Dict[str, List[Tuple[datetime, Optional[datetime], str]]] = defaultdict(list)
    for row in rows["device_assignments.csv"]:
        require(row["personnel_code"] in person_codes, "Assignment references unknown person.")
        require(row["device_token"] in device_tokens, "Assignment references unknown device.")
        start = parse_utc(row["valid_from_utc"])
        end = parse_utc(row["valid_to_utc"]) if row["valid_to_utc"] else None
        require(end is None or start < end, "Invalid assignment interval.")
        item = (start, end, row["personnel_code"])
        assignments_by_device[row["device_token"]].append(item)
        assignments_by_person[row["personnel_code"]].append((start, end, row["device_token"]))

    def no_overlaps(intervals: Iterable[Tuple[datetime, Optional[datetime], str]]) -> bool:
        ordered = sorted(intervals, key=lambda item: item[0])
        return all(previous[1] is not None and previous[1] <= current[0] for previous, current in zip(ordered, ordered[1:]))

    require(all(no_overlaps(items) for items in assignments_by_device.values()), "Overlapping device assignments.")
    require(all(no_overlaps(items) for items in assignments_by_person.values()), "Overlapping person assignments.")
    require(set(assignments_by_device) == device_tokens, "Every device must have one assignment history.")
    require(set(assignments_by_person) == person_codes, "Every person must have an assignment history.")
    replacements = sum(1 for items in assignments_by_person.values() if len(items) == 2)
    require(replacements == int(config["population"]["replacement_devices"]), "Replacement-device count mismatch.")

    return {
        "person_codes": person_codes,
        "device_tokens": device_tokens,
        "access_point_types": access_point_types,
        "assignments_by_device": assignments_by_device,
    }


def active_person_for_device(reference: dict, token: str, observed: datetime) -> Optional[str]:
    for start, end, person in reference["assignments_by_device"].get(token, []):
        if start <= observed and (end is None or observed < end):
            return person
    return None


def classify_sources(output: Path, config: dict, reference: dict) -> dict:
    start_date = date.fromisoformat(config["period"]["start_date"])
    end_date = date.fromisoformat(config["period"]["end_date"])
    zone = ZoneInfo(config["period"]["local_timezone"])
    batches: List[dict] = []
    validations: List[dict] = []
    daily_signals: Dict[Tuple[str, str], List[Tuple[str, datetime]]] = defaultdict(list)

    for month in month_keys(start_date, end_date):
        definitions = (
            ("CARD", output / "card" / f"card_events_{month}.csv", CARD_HEADER),
            ("WIFI", output / "wifi" / f"wifi_observations_{month}.csv", WIFI_HEADER),
        )
        for source_type, path, header in definitions:
            source_rows = read_csv(path, header)
            require(
                [int(row["source_row_number"]) for row in source_rows] == list(range(1, len(source_rows) + 1)),
                f"Source row numbers are not sequential in {path.name}.",
            )
            validation_counts: Counter[str] = Counter()
            accepted = 0
            for row in source_rows:
                observed: Optional[datetime]
                try:
                    observed = parse_utc(row["observed_at_raw"])
                except (ValueError, TypeError):
                    observed = None

                person: Optional[str] = None
                validation_code = ""
                if source_type == "CARD":
                    personnel = row["personnel_code_raw"]
                    access_point = row["access_point_code_raw"]
                    if observed is None:
                        validation_code = "INVALID_TIMESTAMP"
                    elif not personnel:
                        validation_code = "BLANK_PERSONNEL_CODE"
                    elif personnel not in reference["person_codes"]:
                        validation_code = "UNKNOWN_PERSONNEL"
                    elif reference["access_point_types"].get(access_point) != "CARD_READER":
                        validation_code = "UNKNOWN_ACCESS_POINT"
                    else:
                        person = personnel
                else:
                    token = row["device_token_raw"]
                    access_point = row["access_point_code_raw"]
                    if observed is None:
                        validation_code = "INVALID_TIMESTAMP"
                    elif token not in reference["device_tokens"]:
                        validation_code = "UNKNOWN_DEVICE"
                    elif reference["access_point_types"].get(access_point) != "WIFI_AP":
                        validation_code = "UNKNOWN_ACCESS_POINT"
                    else:
                        try:
                            strength = int(row["signal_strength_raw"])
                        except ValueError:
                            strength = 999
                        if not int(config["attendance"]["wifi_signal_strength_min"]) <= strength <= int(config["attendance"]["wifi_signal_strength_max"]):
                            validation_code = "INVALID_SIGNAL_STRENGTH"
                        else:
                            person = active_person_for_device(reference, token, observed)
                            if person is None:
                                validation_code = "UNKNOWN_DEVICE"

                if validation_code:
                    validation_counts[validation_code] += 1
                else:
                    require(observed is not None and person is not None, "Accepted signal failed resolution.")
                    local_date = observed.astimezone(zone).date()
                    require(start_date <= local_date <= end_date, "Accepted signal outside contract period.")
                    daily_signals[(local_date.isoformat(), person)].append((source_type, observed))
                    accepted += 1

            rejected = sum(validation_counts.values())
            batches.append({
                "source_type": source_type,
                "source_file_name": path.name,
                "rows_received": str(len(source_rows)),
                "rows_accepted": str(accepted),
                "rows_rejected": str(rejected),
                "file_sha256": sha256_file(path),
            })
            for code in sorted(validation_counts):
                validations.append({
                    "source_type": source_type,
                    "source_file_name": path.name,
                    "validation_code": code,
                    "expected_count": str(validation_counts[code]),
                })

    reconstructed_daily = []
    for (local_date, person), signals in sorted(daily_signals.items()):
        types = Counter(item[0] for item in signals)
        timestamps = [item[1] for item in signals]
        method = "BOTH" if types["CARD"] and types["WIFI"] else "WIFI" if types["WIFI"] else "CARD"
        reconstructed_daily.append({
            "attendance_date_local": local_date,
            "personnel_code": person,
            "detection_method": method,
            "first_observed_at_utc": min(timestamps).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "last_observed_at_utc": max(timestamps).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "card_signal_count": str(types["CARD"]),
            "wifi_signal_count": str(types["WIFI"]),
        })
    return {"batches": batches, "validations": validations, "daily": reconstructed_daily}


def verify_expected(output: Path, config: dict, manifest: dict, reconstructed: dict) -> None:
    expected_batches = read_csv(output / "expected" / "expected_batch_results.csv", EXPECTED_BATCH_HEADER)
    expected_validations = read_csv(output / "expected" / "expected_validation_counts.csv", EXPECTED_VALIDATION_HEADER)
    expected_daily = read_csv(output / "expected" / "expected_daily_attendance.csv", EXPECTED_DAILY_HEADER)
    require(expected_batches == reconstructed["batches"], "Expected batch results do not match independently classified sources.")
    require(expected_validations == reconstructed["validations"], "Expected validation counts do not match independently classified sources.")
    require(expected_daily == reconstructed["daily"], "Expected daily attendance does not match independently reconstructed signals.")

    configured_anomalies = {
        f"{source.upper()}:{code}": int(count)
        for source, codes in config["anomalies"].items()
        for code, count in codes.items()
    }
    actual_anomalies: Counter[str] = Counter()
    for row in expected_validations:
        actual_anomalies[f"{row['source_type']}:{row['validation_code']}"] += int(row["expected_count"])
    require(dict(sorted(actual_anomalies.items())) == configured_anomalies, "Anomaly totals do not match config.")
    require(manifest["validation_counts"] == configured_anomalies, "Manifest anomaly totals do not match config.")

    detection_counts = Counter(row["detection_method"] for row in expected_daily)
    if config["attendance"]["valid_card_only_days_allowed"]:
        require(detection_counts["CARD"] > 0, "No valid CARD-only person-day found.")
    else:
        require(detection_counts["CARD"] == 0, "Valid CARD-only person-day found.")
    for row in expected_daily:
        local_date = date.fromisoformat(row["attendance_date_local"])
        require(local_date.weekday() < 5, "Weekend person-day found.")
        card_count = int(row["card_signal_count"])
        wifi_count = int(row["wifi_signal_count"])
        method = row["detection_method"]
        if method == "CARD":
            require(card_count == 1 and wifi_count == 0, "CARD-only count mismatch.")
        else:
            require(int(config["attendance"]["wifi_observations_min"]) <= wifi_count <= int(config["attendance"]["wifi_observations_max"]), "Wi-Fi count outside contract.")
            require(
                (method == "BOTH" and card_count == 1)
                or (method == "WIFI" and card_count == 0),
                "Detection method/count mismatch.",
            )
        require(parse_utc(row["first_observed_at_utc"]) <= parse_utc(row["last_observed_at_utc"]), "Invalid observed range.")

    totals = manifest["totals"]
    received = sum(int(row["rows_received"]) for row in expected_batches)
    accepted = sum(int(row["rows_accepted"]) for row in expected_batches)
    rejected = sum(int(row["rows_rejected"]) for row in expected_batches)
    require(received == accepted + rejected, "Batch totals do not reconcile.")
    require(int(config["volume_guardrail"]["minimum_source_rows"]) <= received <= int(config["volume_guardrail"]["maximum_source_rows"]), "Source volume outside guardrail.")
    require(accepted == sum(int(row["card_signal_count"]) + int(row["wifi_signal_count"]) for row in expected_daily), "Accepted signal total does not match daily evidence.")
    expected_totals = {
        "source_rows": received,
        "accepted_rows": accepted,
        "rejected_rows": rejected,
        "attendance_signals": accepted,
        "person_days": len(expected_daily),
        "both_person_days": detection_counts["BOTH"],
        "wifi_person_days": detection_counts["WIFI"],
        "card_person_days": detection_counts["CARD"],
    }
    require(totals == expected_totals, "Manifest totals do not match verified outputs.")
    require(int(manifest["batch_count"]) == 24, "Expected 24 monthly source batches.")


def verify_output(config: dict, output: Path) -> dict:
    require(output.is_dir(), f"Output directory does not exist: {output}")
    manifest = load_json(output / "manifest.json")
    require(manifest["contract_version"] == config["contract_version"], "Contract version mismatch.")
    require(manifest["generator_version"] == config["generator_version"], "Generator version mismatch.")
    require(int(manifest["random_seed"]) == int(config["random_seed"]), "Random seed mismatch.")
    require(manifest["period"] == config["period"], "Period mismatch.")
    verify_manifest_files(output, manifest)
    reference = verify_reference(output, config, manifest)
    reconstructed = classify_sources(output, config, reference)
    verify_expected(output, config, manifest, reconstructed)
    return manifest


def main() -> None:
    args = parse_args()
    config = load_json(args.config.resolve())
    output = args.output.resolve()
    manifest = verify_output(config, output)
    totals = manifest["totals"]
    print("ContractVersion SourceRows AcceptedRows RejectedRows PersonDays Card Both Wifi ChecksumVerification ContractVerification")
    print(
        manifest["contract_version"], totals["source_rows"], totals["accepted_rows"],
        totals["rejected_rows"], totals["person_days"], totals["card_person_days"],
        totals["both_person_days"],
        totals["wifi_person_days"], "PASS", "PASS",
    )
    if args.compare:
        comparison = args.compare.resolve()
        other_manifest = verify_output(config, comparison)
        require(manifest == other_manifest, "Deterministic manifests differ between runs.")
        require(
            sha256_file(output / "manifest.json") == sha256_file(comparison / "manifest.json"),
            "Manifest bytes differ between runs.",
        )
        print(f"Comparison {output.name} {comparison.name} PASS")


if __name__ == "__main__":
    main()
