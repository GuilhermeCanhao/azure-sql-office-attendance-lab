#!/usr/bin/env python3
"""Generate the deterministic synthetic attendance lab dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


UTC = timezone.utc
CSV_DIALECT = {
    "delimiter": ",",
    "quotechar": '"',
    "quoting": csv.QUOTE_MINIMAL,
    "lineterminator": "\n",
}

REFERENCE_HEADERS = {
    "offices.csv": ["office_code", "display_name", "time_zone_name", "capacity", "is_active"],
    "departments.csv": ["department_code", "department_name", "is_active"],
    "people.csv": [
        "personnel_code",
        "display_name",
        "synthetic_email",
        "department_code",
        "valid_from",
        "valid_to",
    ],
    "devices.csv": ["device_token", "device_status"],
    "device_assignments.csv": [
        "personnel_code",
        "device_token",
        "valid_from_utc",
        "valid_to_utc",
    ],
    "access_points.csv": [
        "office_code",
        "access_point_code",
        "access_point_type",
        "display_label",
        "is_active",
    ],
}

CARD_HEADER = [
    "source_row_number",
    "observed_at_raw",
    "personnel_code_raw",
    "access_point_code_raw",
]
WIFI_HEADER = [
    "source_row_number",
    "observed_at_raw",
    "device_token_raw",
    "access_point_code_raw",
    "signal_strength_raw",
]
EXPECTED_DAILY_HEADER = [
    "attendance_date_local",
    "personnel_code",
    "detection_method",
    "first_observed_at_utc",
    "last_observed_at_utc",
    "card_signal_count",
    "wifi_signal_count",
]
EXPECTED_BATCH_HEADER = [
    "source_type",
    "source_file_name",
    "rows_received",
    "rows_accepted",
    "rows_rejected",
    "file_sha256",
]
EXPECTED_VALIDATION_HEADER = [
    "source_type",
    "source_file_name",
    "validation_code",
    "expected_count",
]


def parse_args() -> argparse.Namespace:
    generator_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=generator_dir / "config.json",
        help="Path to the generator configuration.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=generator_dir / "output",
        help="Output directory.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove an existing output directory before generation.",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def validate_config(config: Mapping[str, object]) -> None:
    departments = config["departments"]
    population = config["population"]
    if sum(int(item["people"]) for item in departments) != int(population["people"]):
        raise ValueError("Department headcounts must equal the configured population.")

    attendance = config["attendance"]
    if int(attendance["wifi_observations_min"]) < 1:
        raise ValueError("Wi-Fi observations must be positive when Wi-Fi evidence exists.")
    if int(attendance["wifi_observations_max"]) < int(attendance["wifi_observations_min"]):
        raise ValueError("The maximum Wi-Fi observation count is below the minimum.")
    card_probability = float(attendance["card_event_probability"])
    card_only_probability = float(attendance.get("card_only_day_probability", 0))
    if not 0 <= card_probability <= 1:
        raise ValueError("Card event probability must be between zero and one.")
    if not 0 <= card_only_probability <= 1:
        raise ValueError("Card-only day probability must be between zero and one.")
    if card_only_probability and not attendance["valid_card_only_days_allowed"]:
        raise ValueError("Card-only days must be explicitly allowed before assigning a probability.")

    start_date = date.fromisoformat(config["period"]["start_date"])
    end_date = date.fromisoformat(config["period"]["end_date"])
    if end_date < start_date:
        raise ValueError("The configured end date precedes the start date.")

    ZoneInfo(config["period"]["local_timezone"])


def prepare_output(path: Path, clean: bool) -> None:
    if path.exists():
        if not clean:
            raise FileExistsError(
                f"Output directory already exists: {path}. Use --clean to replace it."
            )
        shutil.rmtree(path)
    for child in ("reference", "card", "wifi", "expected"):
        (path / child).mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Mapping[str, object]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="raise", **CSV_DIALECT)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in header})
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def opaque_device_token(seed: int, label: str) -> str:
    value = hashlib.sha256(f"{seed}:{label}".encode("utf-8")).hexdigest()[:8].upper()
    return f"DEV-{value}"


def iso_utc(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_clock(value: str) -> time:
    return time.fromisoformat(value)


def random_local_datetime(
    rng: random.Random,
    local_date: date,
    start_clock: time,
    end_clock: time,
    zone: ZoneInfo,
) -> datetime:
    start_value = datetime.combine(local_date, start_clock, tzinfo=zone)
    end_value = datetime.combine(local_date, end_clock, tzinfo=zone)
    seconds = int((end_value - start_value).total_seconds())
    return start_value + timedelta(seconds=rng.randint(0, seconds))


def month_keys(start_date: date, end_date: date) -> List[str]:
    current = date(start_date.year, start_date.month, 1)
    keys: List[str] = []
    while current <= end_date:
        keys.append(current.strftime("%Y_%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return keys


def month_sample_date(month_key: str, index: int) -> date:
    year, month = (int(part) for part in month_key.split("_"))
    candidate = date(year, month, 1) + timedelta(days=index % 20)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    if candidate.month != month:
        candidate = date(year, month, 1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate


def build_reference_data(config: dict, rng: random.Random) -> dict:
    period = config["period"]
    population = config["population"]
    office = config["office"]
    seed = int(config["random_seed"])
    start_date = date.fromisoformat(period["start_date"])
    end_date = date.fromisoformat(period["end_date"])

    offices = [
        {
            "office_code": office["office_code"],
            "display_name": office["display_name"],
            "time_zone_name": period["sql_server_timezone"],
            "capacity": office["capacity"],
            "is_active": 1,
        }
    ]

    departments = [
        {
            "department_code": item["code"],
            "department_name": item["name"],
            "is_active": 1,
        }
        for item in config["departments"]
    ]
    department_codes = [
        item["code"]
        for item in config["departments"]
        for _ in range(int(item["people"]))
    ]
    rng.shuffle(department_codes)

    people = []
    for number in range(1, int(population["people"]) + 1):
        people.append(
            {
                "personnel_code": f"PER-{number:04d}",
                "display_name": f"Synthetic Person {number:04d}",
                "synthetic_email": f"person{number:04d}@{population['email_domain']}",
                "department_code": department_codes[number - 1],
                "valid_from": start_date.isoformat(),
                "valid_to": "",
            }
        )

    replacement_people = set(
        rng.sample(
            [person["personnel_code"] for person in people],
            int(population["replacement_devices"]),
        )
    )
    replacement_window_start = date(start_date.year, 10, 1)
    replacement_window_end = date(end_date.year, 3, 31)
    replacement_span = (replacement_window_end - replacement_window_start).days

    devices = []
    assignments = []
    assignment_lookup: Dict[str, List[Tuple[date, Optional[date], str]]] = {}

    for person in people:
        code = person["personnel_code"]
        primary_token = opaque_device_token(seed, f"{code}:primary")
        periods: List[Tuple[date, Optional[date], str]] = []
        if code in replacement_people:
            offset = rng.randint(0, replacement_span)
            replacement_date = replacement_window_start + timedelta(days=offset)
            while replacement_date.weekday() >= 5:
                replacement_date += timedelta(days=1)
            if replacement_date > replacement_window_end:
                replacement_date = replacement_window_end
                while replacement_date.weekday() >= 5:
                    replacement_date -= timedelta(days=1)

            replacement_token = opaque_device_token(seed, f"{code}:replacement")
            devices.append({"device_token": primary_token, "device_status": "RETIRED"})
            devices.append({"device_token": replacement_token, "device_status": "ACTIVE"})
            periods.extend(
                [
                    (start_date, replacement_date, primary_token),
                    (replacement_date, None, replacement_token),
                ]
            )
        else:
            devices.append({"device_token": primary_token, "device_status": "ACTIVE"})
            periods.append((start_date, None, primary_token))

        assignment_lookup[code] = periods
        for valid_from, valid_to, token in periods:
            assignments.append(
                {
                    "personnel_code": code,
                    "device_token": token,
                    "valid_from_utc": f"{valid_from.isoformat()}T00:00:00.000Z",
                    "valid_to_utc": (
                        f"{valid_to.isoformat()}T00:00:00.000Z" if valid_to else ""
                    ),
                }
            )

    access_points = []
    for item in config["access_points"]["card_readers"]:
        access_points.append(
            {
                "office_code": office["office_code"],
                "access_point_code": item["code"],
                "access_point_type": "CARD_READER",
                "display_label": item["label"],
                "is_active": 1,
            }
        )
    for item in config["access_points"]["wifi_access_points"]:
        access_points.append(
            {
                "office_code": office["office_code"],
                "access_point_code": item["code"],
                "access_point_type": "WIFI_AP",
                "display_label": item["label"],
                "is_active": 1,
            }
        )

    return {
        "offices": offices,
        "departments": departments,
        "people": people,
        "devices": sorted(devices, key=lambda row: row["device_token"]),
        "device_assignments": sorted(
            assignments, key=lambda row: (row["personnel_code"], row["valid_from_utc"])
        ),
        "access_points": sorted(access_points, key=lambda row: row["access_point_code"]),
        "assignment_lookup": assignment_lookup,
        "replacement_people": replacement_people,
    }


def device_for_date(reference: dict, personnel_code: str, local_date: date) -> str:
    for valid_from, valid_to, token in reference["assignment_lookup"][personnel_code]:
        if valid_from <= local_date and (valid_to is None or local_date < valid_to):
            return token
    raise ValueError(f"No active device for {personnel_code} on {local_date}.")


def generate_valid_observations(config: dict, reference: dict, rng: random.Random) -> dict:
    period = config["period"]
    attendance = config["attendance"]
    zone = ZoneInfo(period["local_timezone"])
    start_date = date.fromisoformat(period["start_date"])
    end_date = date.fromisoformat(period["end_date"])
    weekday_names = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    probabilities = attendance["weekday_probabilities"]
    arrival_start = parse_clock(attendance["local_arrival_start"])
    arrival_end = parse_clock(attendance["local_arrival_end"])
    departure_start = parse_clock(attendance["local_departure_start"])
    departure_end = parse_clock(attendance["local_departure_end"])
    card_reader = config["access_points"]["card_readers"][0]["code"]
    wifi_points = [item["code"] for item in config["access_points"]["wifi_access_points"]]

    propensities = {
        person["personnel_code"]: rng.uniform(
            float(attendance["person_propensity_min"]),
            float(attendance["person_propensity_max"]),
        )
        for person in reference["people"]
    }

    card_rows: MutableMapping[str, List[dict]] = defaultdict(list)
    wifi_rows: MutableMapping[str, List[dict]] = defaultdict(list)
    expected_daily: List[dict] = []

    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() < 5:
            weekday_probability = float(probabilities[weekday_names[current_date.weekday()]])
            for person in reference["people"]:
                code = person["personnel_code"]
                probability = min(0.95, weekday_probability * propensities[code])
                if rng.random() >= probability:
                    continue

                arrival = random_local_datetime(
                    rng, current_date, arrival_start, arrival_end, zone
                )
                departure = random_local_datetime(
                    rng, current_date, departure_start, departure_end, zone
                )
                if departure <= arrival + timedelta(hours=4):
                    departure = arrival + timedelta(hours=4)

                card_time: Optional[datetime] = None
                if rng.random() < float(attendance["card_event_probability"]):
                    card_time = arrival - timedelta(seconds=rng.randint(0, 300))

                has_wifi = True
                if (
                    card_time is not None
                    and attendance["valid_card_only_days_allowed"]
                    and rng.random() < float(attendance.get("card_only_day_probability", 0))
                ):
                    has_wifi = False

                wifi_count = (
                    rng.randint(
                        int(attendance["wifi_observations_min"]),
                        int(attendance["wifi_observations_max"]),
                    )
                    if has_wifi
                    else 0
                )
                sample_start = arrival + timedelta(minutes=5)
                sample_end = departure - timedelta(minutes=5)
                sample_span = max(1, int((sample_end - sample_start).total_seconds()))
                wifi_times = (
                    sorted(
                        sample_start + timedelta(seconds=rng.randint(0, sample_span))
                        for _ in range(wifi_count)
                    )
                    if has_wifi
                    else []
                )
                month_key = current_date.strftime("%Y_%m")

                if has_wifi:
                    device_token = device_for_date(reference, code, current_date)
                    for observed_at in wifi_times:
                        wifi_rows[month_key].append(
                            {
                                "observed_at_raw": iso_utc(observed_at),
                                "device_token_raw": device_token,
                                "access_point_code_raw": rng.choice(wifi_points),
                                "signal_strength_raw": rng.randint(
                                    int(attendance["wifi_signal_strength_min"]),
                                    int(attendance["wifi_signal_strength_max"]),
                                ),
                                "validation_code": "",
                            }
                        )

                if card_time is not None:
                    card_rows[month_key].append(
                        {
                            "observed_at_raw": iso_utc(card_time),
                            "personnel_code_raw": code,
                            "access_point_code_raw": card_reader,
                            "validation_code": "",
                        }
                    )

                all_times = list(wifi_times)
                if card_time is not None:
                    all_times.append(card_time)
                if not all_times:
                    raise ValueError("Every valid attendance day must have at least one signal.")
                if card_time is not None and has_wifi:
                    detection_method = "BOTH"
                elif card_time is not None:
                    detection_method = "CARD"
                else:
                    detection_method = "WIFI"
                expected_daily.append(
                    {
                        "attendance_date_local": current_date.isoformat(),
                        "personnel_code": code,
                        "detection_method": detection_method,
                        "first_observed_at_utc": iso_utc(min(all_times)),
                        "last_observed_at_utc": iso_utc(max(all_times)),
                        "card_signal_count": 1 if card_time is not None else 0,
                        "wifi_signal_count": wifi_count,
                    }
                )
        current_date += timedelta(days=1)

    expected_daily.sort(
        key=lambda row: (row["attendance_date_local"], row["personnel_code"])
    )
    return {"card_rows": card_rows, "wifi_rows": wifi_rows, "expected_daily": expected_daily}


def add_controlled_anomalies(
    config: dict,
    reference: dict,
    observations: dict,
) -> None:
    period = config["period"]
    zone = ZoneInfo(period["local_timezone"])
    months = month_keys(
        date.fromisoformat(period["start_date"]), date.fromisoformat(period["end_date"])
    )
    card_reader = config["access_points"]["card_readers"][0]["code"]
    wifi_point = config["access_points"]["wifi_access_points"][0]["code"]
    person = reference["people"][0]["personnel_code"]

    for source_type, configured_codes in config["anomalies"].items():
        for validation_code, count in configured_codes.items():
            for index in range(int(count)):
                month_key = months[index % len(months)]
                sample_date = month_sample_date(month_key, index)
                sample_local = datetime.combine(sample_date, time(12, 0), tzinfo=zone)
                timestamp = iso_utc(sample_local)

                if source_type == "card":
                    row = {
                        "observed_at_raw": timestamp,
                        "personnel_code_raw": person,
                        "access_point_code_raw": card_reader,
                        "validation_code": validation_code,
                    }
                    if validation_code == "INVALID_TIMESTAMP":
                        row["observed_at_raw"] = "not-a-timestamp"
                    elif validation_code == "UNKNOWN_PERSONNEL":
                        row["personnel_code_raw"] = "PER-9999"
                    elif validation_code == "UNKNOWN_ACCESS_POINT":
                        row["access_point_code_raw"] = "CARD-UNKNOWN-01"
                    elif validation_code == "BLANK_PERSONNEL_CODE":
                        row["personnel_code_raw"] = ""
                    else:
                        raise ValueError(f"Unsupported card anomaly: {validation_code}")
                    observations["card_rows"][month_key].append(row)
                else:
                    token = device_for_date(reference, person, sample_date)
                    row = {
                        "observed_at_raw": timestamp,
                        "device_token_raw": token,
                        "access_point_code_raw": wifi_point,
                        "signal_strength_raw": -55,
                        "validation_code": validation_code,
                    }
                    if validation_code == "INVALID_TIMESTAMP":
                        row["observed_at_raw"] = "not-a-timestamp"
                    elif validation_code == "UNKNOWN_DEVICE":
                        row["device_token_raw"] = f"DEV-{0xF0000000 + index:08X}"
                    elif validation_code == "UNKNOWN_ACCESS_POINT":
                        row["access_point_code_raw"] = "WIFI-UNKNOWN-01"
                    elif validation_code == "INVALID_SIGNAL_STRENGTH":
                        row["signal_strength_raw"] = "not-an-int"
                    else:
                        raise ValueError(f"Unsupported Wi-Fi anomaly: {validation_code}")
                    observations["wifi_rows"][month_key].append(row)


def write_reference_files(output: Path, reference: dict) -> Dict[str, int]:
    mapping = {
        "offices.csv": reference["offices"],
        "departments.csv": reference["departments"],
        "people.csv": reference["people"],
        "devices.csv": reference["devices"],
        "device_assignments.csv": reference["device_assignments"],
        "access_points.csv": reference["access_points"],
    }
    counts = {}
    for filename, rows in mapping.items():
        counts[filename] = write_csv(
            output / "reference" / filename, REFERENCE_HEADERS[filename], rows
        )
    return counts


def source_sort_key(row: Mapping[str, object]) -> Tuple[str, str, str]:
    return (
        str(row.get("observed_at_raw", "")),
        str(row.get("validation_code", "")),
        str(row.get("personnel_code_raw", row.get("device_token_raw", ""))),
    )


def write_source_files(output: Path, config: dict, observations: dict) -> dict:
    start_date = date.fromisoformat(config["period"]["start_date"])
    end_date = date.fromisoformat(config["period"]["end_date"])
    months = month_keys(start_date, end_date)
    batch_results = []
    validation_results = []

    for month_key in months:
        for source_type, directory, prefix, header, collection_name in (
            ("CARD", "card", "card_events", CARD_HEADER, "card_rows"),
            ("WIFI", "wifi", "wifi_observations", WIFI_HEADER, "wifi_rows"),
        ):
            internal_rows = sorted(
                observations[collection_name].get(month_key, []), key=source_sort_key
            )
            output_rows = []
            validation_counts: Counter[str] = Counter()
            for row_number, internal_row in enumerate(internal_rows, start=1):
                row = dict(internal_row)
                validation_code = row.pop("validation_code")
                if validation_code:
                    validation_counts[validation_code] += 1
                row["source_row_number"] = row_number
                output_rows.append(row)

            filename = f"{prefix}_{month_key}.csv"
            path = output / directory / filename
            row_count = write_csv(path, header, output_rows)
            rejected = sum(validation_counts.values())
            batch_results.append(
                {
                    "source_type": source_type,
                    "source_file_name": filename,
                    "rows_received": row_count,
                    "rows_accepted": row_count - rejected,
                    "rows_rejected": rejected,
                    "file_sha256": sha256_file(path),
                }
            )
            for validation_code in sorted(validation_counts):
                validation_results.append(
                    {
                        "source_type": source_type,
                        "source_file_name": filename,
                        "validation_code": validation_code,
                        "expected_count": validation_counts[validation_code],
                    }
                )

    return {"batch_results": batch_results, "validation_results": validation_results}


def csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.reader(handle)) - 1


def build_manifest(
    output: Path,
    config: dict,
    reference_counts: Mapping[str, int],
    observations: dict,
    source_results: dict,
) -> dict:
    batch_results = source_results["batch_results"]
    source_received = sum(int(row["rows_received"]) for row in batch_results)
    source_accepted = sum(int(row["rows_accepted"]) for row in batch_results)
    source_rejected = sum(int(row["rows_rejected"]) for row in batch_results)
    guardrail = config["volume_guardrail"]
    if not int(guardrail["minimum_source_rows"]) <= source_received <= int(
        guardrail["maximum_source_rows"]
    ):
        raise ValueError(
            f"Generated source volume {source_received} is outside the configured guardrail."
        )

    detection_counts = Counter(
        row["detection_method"] for row in observations["expected_daily"]
    )
    validation_counts = Counter()
    for row in source_results["validation_results"]:
        validation_counts[f"{row['source_type']}:{row['validation_code']}"] += int(
            row["expected_count"]
        )

    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = path.relative_to(output).as_posix()
            files[relative] = {
                "rows": csv_row_count(path) if path.suffix == ".csv" else None,
                "sha256": sha256_file(path),
            }

    return {
        "contract_version": config["contract_version"],
        "generator_version": config["generator_version"],
        "random_seed": config["random_seed"],
        "python_compatibility": ">=3.9",
        "period": config["period"],
        "reference_counts": {
            "offices": reference_counts["offices.csv"],
            "departments": reference_counts["departments.csv"],
            "people": reference_counts["people.csv"],
            "devices": reference_counts["devices.csv"],
            "device_assignments": reference_counts["device_assignments.csv"],
            "access_points": reference_counts["access_points.csv"],
        },
        "batch_count": len(batch_results),
        "totals": {
            "source_rows": source_received,
            "accepted_rows": source_accepted,
            "rejected_rows": source_rejected,
            "attendance_signals": source_accepted,
            "person_days": len(observations["expected_daily"]),
            "both_person_days": detection_counts["BOTH"],
            "wifi_person_days": detection_counts["WIFI"],
            "card_person_days": detection_counts["CARD"],
        },
        "validation_counts": dict(sorted(validation_counts.items())),
        "files": files,
    }


def write_manifest(output: Path, manifest: Mapping[str, object]) -> None:
    with (output / "manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def generate(config_path: Path, output: Path, clean: bool) -> dict:
    config = load_config(config_path)
    prepare_output(output, clean)
    rng = random.Random(int(config["random_seed"]))

    reference = build_reference_data(config, rng)
    reference_counts = write_reference_files(output, reference)
    observations = generate_valid_observations(config, reference, rng)
    add_controlled_anomalies(config, reference, observations)
    source_results = write_source_files(output, config, observations)

    write_csv(
        output / "expected" / "expected_daily_attendance.csv",
        EXPECTED_DAILY_HEADER,
        observations["expected_daily"],
    )
    write_csv(
        output / "expected" / "expected_batch_results.csv",
        EXPECTED_BATCH_HEADER,
        source_results["batch_results"],
    )
    write_csv(
        output / "expected" / "expected_validation_counts.csv",
        EXPECTED_VALIDATION_HEADER,
        source_results["validation_results"],
    )

    manifest = build_manifest(
        output, config, reference_counts, observations, source_results
    )
    write_manifest(output, manifest)
    return manifest


def main() -> None:
    args = parse_args()
    manifest = generate(args.config.resolve(), args.output.resolve(), args.clean)
    totals = manifest["totals"]
    print(f"Output: {args.output.resolve()}")
    print(f"Contract version: {manifest['contract_version']}")
    print(f"Source rows: {totals['source_rows']}")
    print(f"Accepted rows: {totals['accepted_rows']}")
    print(f"Rejected rows: {totals['rejected_rows']}")
    print(f"Person-days: {totals['person_days']}")
    print(f"CARD person-days: {totals['card_person_days']}")
    print(f"BOTH person-days: {totals['both_person_days']}")
    print(f"WIFI person-days: {totals['wifi_person_days']}")


if __name__ == "__main__":
    main()
