#!/usr/bin/env python3
"""Shared, privacy-safe helpers for the controlled Azure SQL loader."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
SQL_COPT_SS_ACCESS_TOKEN = 1256
TOKEN_RESOURCE = "https://database.windows.net/"
CHUNK_SIZE = 1000
TOKEN_MINIMUM_LIFETIME_SECONDS = 300

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "generator" / "output" / "run-a"
CONFIG_PATH = PROJECT_ROOT / "generator" / "config.json"
GENERATOR_VERIFIER_PATH = PROJECT_ROOT / "generator" / "verify_output.py"

SERVER_RE = re.compile(r"^[A-Za-z0-9-]+\.database\.windows\.net$")
DATABASE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

REFERENCE_HEADERS: Mapping[str, Sequence[str]] = {
    "offices.csv": ("office_code", "display_name", "time_zone_name", "capacity", "is_active"),
    "departments.csv": ("department_code", "department_name", "is_active"),
    "people.csv": (
        "personnel_code", "display_name", "synthetic_email", "department_code",
        "valid_from", "valid_to",
    ),
    "devices.csv": ("device_token", "device_status"),
    "device_assignments.csv": (
        "personnel_code", "device_token", "valid_from_utc", "valid_to_utc",
    ),
    "access_points.csv": (
        "office_code", "access_point_code", "access_point_type", "display_label", "is_active",
    ),
}
CARD_HEADER = (
    "source_row_number", "observed_at_raw", "personnel_code_raw", "access_point_code_raw",
)
WIFI_HEADER = (
    "source_row_number", "observed_at_raw", "device_token_raw", "access_point_code_raw",
    "signal_strength_raw",
)
EXPECTED_BATCH_HEADER = (
    "source_type", "source_file_name", "rows_received", "rows_accepted", "rows_rejected",
    "file_sha256",
)
EXPECTED_VALIDATION_HEADER = (
    "source_type", "source_file_name", "validation_code", "expected_count",
)
EXPECTED_DAILY_HEADER = (
    "attendance_date_local", "personnel_code", "detection_method", "first_observed_at_utc",
    "last_observed_at_utc", "card_signal_count", "wifi_signal_count",
)


class SafeLoaderError(RuntimeError):
    """An error whose message is safe to display in project evidence."""


@dataclass(frozen=True)
class SourceBatch:
    source_type: str
    source_file_name: str
    rows_received: int
    rows_accepted: int
    rows_rejected: int
    file_sha256: str
    path: Path

    @property
    def expected_status(self) -> str:
        return "PARTIAL" if self.rows_rejected else "COMPLETED"


@dataclass(frozen=True)
class DatasetPlan:
    data_dir: Path
    manifest: dict
    reference_rows: Mapping[str, List[dict]]
    batches: Tuple[SourceBatch, ...]
    expected_validations: Tuple[dict, ...]
    expected_daily_path: Path


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv_rows(path: Path, expected_header: Sequence[str]) -> List[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(expected_header):
            raise SafeLoaderError("A generated CSV has an unexpected header.")
        return list(reader)


def iter_csv_chunks(
    path: Path, expected_header: Sequence[str], chunk_size: int = CHUNK_SIZE
) -> Iterator[List[dict]]:
    if not 1 <= chunk_size <= CHUNK_SIZE:
        raise SafeLoaderError("Chunk size must be between 1 and 1,000 rows.")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(expected_header):
            raise SafeLoaderError("A source CSV has an unexpected header.")
        chunk: List[dict] = []
        for row in reader:
            chunk.append(dict(row))
            if len(chunk) == chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


def _run_independent_generator_verifier(data_dir: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "attendance_generator_verifier", GENERATOR_VERIFIER_PATH
    )
    if spec is None or spec.loader is None:
        raise SafeLoaderError("The independent generator verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        config = module.load_json(CONFIG_PATH)
        return module.verify_output(config, data_dir)
    except Exception as exc:
        raise SafeLoaderError("Independent generated-data verification failed.") from exc


def build_dataset_plan(data_dir: Path) -> DatasetPlan:
    resolved = data_dir.expanduser().resolve()
    manifest = _run_independent_generator_verifier(resolved)
    reference_rows = {
        filename: read_csv_rows(resolved / "reference" / filename, header)
        for filename, header in REFERENCE_HEADERS.items()
    }
    expected_batches = read_csv_rows(
        resolved / "expected" / "expected_batch_results.csv", EXPECTED_BATCH_HEADER
    )
    batches: List[SourceBatch] = []
    for row in expected_batches:
        source_type = row["source_type"]
        if source_type not in {"CARD", "WIFI"}:
            raise SafeLoaderError("An expected batch has an unsupported source type.")
        directory = "card" if source_type == "CARD" else "wifi"
        filename = row["source_file_name"]
        if Path(filename).name != filename:
            raise SafeLoaderError("An expected source filename is not a basename.")
        relative = f"{directory}/{filename}"
        record = manifest["files"].get(relative)
        if record is None:
            raise SafeLoaderError("An expected source file is absent from the manifest.")
        if record["sha256"] != row["file_sha256"] or int(record["rows"]) != int(row["rows_received"]):
            raise SafeLoaderError("An expected batch disagrees with the manifest.")
        batches.append(
            SourceBatch(
                source_type=source_type,
                source_file_name=filename,
                rows_received=int(row["rows_received"]),
                rows_accepted=int(row["rows_accepted"]),
                rows_rejected=int(row["rows_rejected"]),
                file_sha256=row["file_sha256"],
                path=resolved / relative,
            )
        )
    expected_validations = tuple(
        read_csv_rows(
            resolved / "expected" / "expected_validation_counts.csv",
            EXPECTED_VALIDATION_HEADER,
        )
    )
    if len(batches) != int(manifest["batch_count"]):
        raise SafeLoaderError("Expected batch inventory does not match the manifest.")
    return DatasetPlan(
        data_dir=resolved,
        manifest=manifest,
        reference_rows=reference_rows,
        batches=tuple(batches),
        expected_validations=expected_validations,
        expected_daily_path=resolved / "expected" / "expected_daily_attendance.csv",
    )


def build_reference_payload(plan: DatasetPlan) -> str:
    rows = plan.reference_rows
    payload = {
        "offices": [
            {**row, "capacity": int(row["capacity"]), "is_active": int(row["is_active"])}
            for row in rows["offices.csv"]
        ],
        "departments": [
            {**row, "is_active": int(row["is_active"])}
            for row in rows["departments.csv"]
        ],
        "people": [
            {**row, "valid_to": row["valid_to"] or None}
            for row in rows["people.csv"]
        ],
        "devices": list(rows["devices.csv"]),
        "device_assignments": [
            {**row, "valid_to_utc": row["valid_to_utc"] or None}
            for row in rows["device_assignments.csv"]
        ],
        "access_points": [
            {**row, "is_active": int(row["is_active"])}
            for row in rows["access_points.csv"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def validate_dry_run(plan: DatasetPlan) -> dict:
    payload = build_reference_payload(plan)
    json.loads(payload)
    chunk_count = 0
    observed_rows = 0
    for batch in plan.batches:
        header = CARD_HEADER if batch.source_type == "CARD" else WIFI_HEADER
        batch_rows = 0
        for chunk in iter_csv_chunks(batch.path, header):
            if len(chunk) > CHUNK_SIZE:
                raise SafeLoaderError("A planned JSON chunk exceeds 1,000 rows.")
            json.loads(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
            chunk_count += 1
            batch_rows += len(chunk)
        if batch_rows != batch.rows_received:
            raise SafeLoaderError("A planned batch row count changed after verification.")
        observed_rows += batch_rows
    if observed_rows != int(plan.manifest["totals"]["source_rows"]):
        raise SafeLoaderError("Planned source-row total does not match the manifest.")
    return {"batches": len(plan.batches), "rows": observed_rows, "chunks": chunk_count}


def runtime_target() -> Tuple[str, str]:
    server = os.environ.get("ATTENDANCE_SQL_SERVER", "").strip()
    database = os.environ.get("ATTENDANCE_SQL_DATABASE", "").strip()
    if not server or not database:
        raise SafeLoaderError(
            "Runtime target is missing. Set ATTENDANCE_SQL_SERVER and ATTENDANCE_SQL_DATABASE."
        )
    if not SERVER_RE.fullmatch(server) or not DATABASE_RE.fullmatch(database):
        raise SafeLoaderError("Runtime target format is invalid.")
    return server, database


def acquire_azure_sql_token() -> bytes:
    azure_cli = shutil.which("az")
    if azure_cli is None:
        raise SafeLoaderError("Azure CLI is not installed or is unavailable on PATH.")
    command = [
        azure_cli,
        "account",
        "get-access-token",
        "--resource",
        TOKEN_RESOURCE,
        "--query",
        "{accessToken:accessToken,expires_on:expires_on}",
        "--output",
        "json",
        "--only-show-errors",
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SafeLoaderError("Azure CLI token acquisition could not be started.") from exc
    if completed.returncode != 0:
        raise SafeLoaderError("Azure CLI token acquisition failed; refresh the existing session.")
    try:
        response = json.loads(completed.stdout)
        token = response["accessToken"]
        expires_on = int(response["expires_on"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SafeLoaderError("Azure CLI returned an unusable token response.") from exc
    if not isinstance(token, str) or not token:
        raise SafeLoaderError("Azure CLI returned an empty access token.")
    if expires_on - int(time.time()) < TOKEN_MINIMUM_LIFETIME_SECONDS:
        raise SafeLoaderError("The Azure SQL access token is too close to expiry.")
    token_bytes = token.encode("utf-16-le")
    return struct.pack("<I", len(token_bytes)) + token_bytes


def classify_database_error(exc: BaseException) -> str:
    text = " ".join(str(part) for part in getattr(exc, "args", ())).lower()
    if "im002" in text or "data source name not found" in text:
        return "DRIVER_CONFIGURATION"
    if "40615" in text or "firewall" in text or "client with ip address" in text:
        return "FIREWALL"
    if "40613" in text or "not currently available" in text or "temporarily unavailable" in text:
        return "DATABASE_NOT_READY"
    if "18456" in text or "login failed" in text or "token" in text or "principal" in text:
        return "AUTHENTICATION"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "TLS_VALIDATION"
    if "08001" in text or "08004" in text or "tcp" in text or "server was not found" in text:
        return "NETWORK_ROUTING"
    return "DATABASE_OPERATION"


def connect_with_token(server: str, database: str, token_struct: bytes):
    try:
        import pyodbc
    except ImportError as exc:
        raise SafeLoaderError("Project-local pyodbc is not installed.") from exc
    if ODBC_DRIVER not in pyodbc.drivers():
        raise SafeLoaderError("Microsoft ODBC Driver 18 for SQL Server is not installed.")
    connection_string = (
        f"Driver={{{ODBC_DRIVER}}};Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=120;"
    )
    try:
        return pyodbc.connect(
            connection_string,
            attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
            autocommit=True,
            timeout=120,
        )
    except Exception as exc:
        category = classify_database_error(exc)
        raise SafeLoaderError(f"Azure SQL connection failed: {category}.") from exc


def probe_connection(connection) -> None:
    try:
        cursor = connection.cursor()
        row = cursor.execute(
            "SELECT DB_NAME(), CONVERT(varchar(20), DATABASEPROPERTYEX(DB_NAME(), 'Status'));"
        ).fetchone()
    except Exception as exc:
        category = classify_database_error(exc)
        raise SafeLoaderError(f"Azure SQL readiness probe failed: {category}.") from exc
    if row is None or str(row[1]).upper() != "ONLINE":
        raise SafeLoaderError("Azure SQL target is reachable but not data-plane ready.")


def connect_ready_target(server: str, database: str, token_struct: bytes):
    """Connect to the target and use master only to isolate resume readiness."""
    connection = None
    try:
        connection = connect_with_token(server, database, token_struct)
        probe_connection(connection)
        return connection
    except SafeLoaderError as exc:
        if connection is not None:
            connection.close()
        if "DATABASE_NOT_READY" not in str(exc) and "not data-plane ready" not in str(exc):
            raise
        master_connection = None
        try:
            master_connection = connect_with_token(server, "master", token_struct)
            probe_connection(master_connection)
        except SafeLoaderError:
            raise
        finally:
            if master_connection is not None:
                try:
                    master_connection.close()
                except Exception:
                    pass
        raise SafeLoaderError(
            "Azure SQL logical server is reachable, but the target database is not data-plane ready."
        ) from exc


def result_row(cursor) -> Optional[Dict[str, object]]:
    while cursor.description is None:
        if not cursor.nextset():
            return None
    columns = [column[0] for column in cursor.description]
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


def safe_main_error(exc: BaseException) -> str:
    if isinstance(exc, SafeLoaderError):
        return str(exc)
    return "The operation stopped because an unexpected local error was safely suppressed."
