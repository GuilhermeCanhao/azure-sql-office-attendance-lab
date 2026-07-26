#!/usr/bin/env python3
"""Safely validate, probe, or execute the controlled canonical Azure SQL load."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

from loader_common import (
    CARD_HEADER,
    DEFAULT_DATA_DIR,
    WIFI_HEADER,
    DatasetPlan,
    SafeLoaderError,
    SourceBatch,
    acquire_azure_sql_token,
    build_dataset_plan,
    build_reference_payload,
    connect_ready_target,
    iter_csv_chunks,
    result_row,
    runtime_target,
    safe_main_error,
    sha256_file,
    validate_dry_run,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Canonical generated-data directory (not printed).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Run only offline validation (the default).",
    )
    mode.add_argument(
        "--probe",
        action="store_true",
        help="Run offline validation plus a harmless Azure SQL readiness probe.",
    )
    mode.add_argument(
        "--execute-load",
        action="store_true",
        help="Administrator-only canonical reference, source, summary, and verification path.",
    )
    mode.add_argument(
        "--execute-source-load",
        action="store_true",
        help="Least-privilege source-batch and summary path; no bootstrap or verifier.",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def _execute_result(cursor, sql: str, *parameters) -> Dict[str, object]:
    try:
        cursor.execute(sql, *parameters)
        row = result_row(cursor)
    except Exception as exc:
        raise SafeLoaderError("A controlled database procedure failed; private details were suppressed.") from exc
    if row is None:
        raise SafeLoaderError("A controlled database procedure returned no result.")
    return row


def bootstrap_references(connection, plan: DatasetPlan) -> str:
    payload = build_reference_payload(plan)
    cursor = connection.cursor()
    result = _execute_result(
        cursor,
        "DECLARE @payload nvarchar(max) = ?; "
        "EXEC core.usp_BootstrapReferenceData @ReferencePayload = @payload;",
        payload,
    )
    outcome = str(result.get("BootstrapResult", ""))
    require(outcome in {"APPLIED", "UNCHANGED"}, "Reference bootstrap returned an unexpected outcome.")
    expected_keys = {
        "OfficesInserted", "DepartmentsInserted", "PeopleInserted", "DevicesInserted",
        "AssignmentsInserted", "AccessPointsInserted",
    }
    require(expected_keys.issubset(result), "Reference bootstrap result is incomplete.")
    if outcome == "UNCHANGED":
        require(sum(int(result[key]) for key in expected_keys) == 0, "Unchanged reference bootstrap inserted rows.")
    return outcome


def _begin_batch(connection, batch: SourceBatch) -> Dict[str, object]:
    return _execute_result(
        connection.cursor(),
        "EXEC stage.usp_BeginImportBatch "
        "@SourceType = ?, @SourceFileName = ?, @FileChecksum = ?;",
        batch.source_type,
        batch.source_file_name,
        bytes.fromhex(batch.file_sha256),
    )


def _verify_existing_batch(connection, batch: SourceBatch, import_batch_id: int) -> None:
    result = _execute_result(
        connection.cursor(),
        "EXEC stage.usp_GetImportBatchResult "
        "@ImportBatchId = ?, @FileChecksum = ?;",
        import_batch_id,
        bytes.fromhex(batch.file_sha256),
    )
    actual = (
        str(result.get("SourceType", "")),
        str(result.get("SourceFileName", "")),
        str(result.get("FileChecksumHex", "")).lower(),
        str(result.get("Status", "")),
        int(result.get("RowsReceived", -1)),
        int(result.get("RowsAccepted", -1)),
        int(result.get("RowsRejected", -1)),
    )
    expected = (
        batch.source_type, batch.source_file_name, batch.file_sha256, batch.expected_status,
        batch.rows_received, batch.rows_accepted, batch.rows_rejected,
    )
    require(actual == expected, f"Already-processed batch does not match expectations: {batch.source_file_name}.")


def _record_failed_batch(
    connection, batch: SourceBatch, import_batch_id: int, lock_released: bool
) -> str:
    try:
        connection.rollback()
        connection.autocommit = True
        if lock_released:
            reacquired = _begin_batch(connection, batch)
            require(
                int(reacquired.get("ImportBatchId", -1)) == import_batch_id
                and reacquired.get("BeginResult") == "READY",
                "Failed batch could not be reacquired for recovery.",
            )
        connection.autocommit = False
        result = _execute_result(
            connection.cursor(),
            "EXEC stage.usp_FailImportBatch @ImportBatchId = ?, @ErrorCategory = ?;",
            import_batch_id,
            "CLIENT_LOAD_FAILURE",
        )
        require(result.get("FinalStatus") == "FAILED", "Failed batch did not reach FAILED state.")
        connection.commit()
        connection.autocommit = True
        return "RECORDED"
    except Exception:
        try:
            connection.rollback()
            connection.autocommit = True
        except Exception:
            pass
        return "DEFERRED"


def process_batch(connection, batch: SourceBatch) -> str:
    require(
        sha256_file(batch.path) == batch.file_sha256,
        f"Source checksum changed before batch registration: {batch.source_file_name}.",
    )
    connection.autocommit = True
    begin = _begin_batch(connection, batch)
    import_batch_id = int(begin.get("ImportBatchId", -1))
    require(import_batch_id > 0, "Batch registration returned an invalid identifier.")
    begin_result = str(begin.get("BeginResult", ""))
    if begin_result == "ALREADY_PROCESSED":
        _verify_existing_batch(connection, batch, import_batch_id)
        return "ALREADY_PROCESSED"
    require(begin_result == "READY", "Batch registration returned an unexpected outcome.")

    finalized = False
    try:
        connection.autocommit = False
        header = CARD_HEADER if batch.source_type == "CARD" else WIFI_HEADER
        appended = 0
        for chunk in iter_csv_chunks(batch.path, header):
            rows_json = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
            result = _execute_result(
                connection.cursor(),
                "DECLARE @rows nvarchar(max) = ?; "
                "EXEC stage.usp_AppendImportChunk @ImportBatchId = ?, @RowsJson = @rows;",
                rows_json,
                import_batch_id,
            )
            require(
                int(result.get("ImportBatchId", -1)) == import_batch_id
                and int(result.get("RowsAppended", -1)) == len(chunk),
                "Chunk append result does not reconcile.",
            )
            appended += len(chunk)
        require(appended == batch.rows_received, "Appended row count does not match the expected file count.")
        result = _execute_result(
            connection.cursor(),
            "EXEC stage.usp_FinalizeImportBatch @ImportBatchId = ?, @ExpectedRows = ?;",
            import_batch_id,
            batch.rows_received,
        )
        finalized = True
        actual = (
            int(result.get("RowsReceived", -1)),
            int(result.get("RowsAccepted", -1)),
            int(result.get("RowsRejected", -1)),
            str(result.get("FinalStatus", "")),
        )
        expected = (
            batch.rows_received, batch.rows_accepted, batch.rows_rejected, batch.expected_status
        )
        require(actual == expected, "Finalized batch does not match the independent expectation.")
        connection.commit()
        connection.autocommit = True
        return batch.expected_status
    except Exception as exc:
        recovery = _record_failed_batch(connection, batch, import_batch_id, finalized)
        raise SafeLoaderError(
            f"Batch stopped safely: {batch.source_file_name}; failure recovery={recovery}."
        ) from exc


def refresh_daily_summary(connection, expected_rows: int) -> None:
    connection.autocommit = True
    result = _execute_result(
        connection.cursor(),
        "EXEC core.usp_RefreshDailyAttendanceSummary @FromDate = NULL, @ThroughDate = NULL;",
    )
    require(result.get("RefreshScope") == "FULL", "Daily-summary refresh was not full scope.")
    require(int(result.get("RowsRefreshed", -1)) == expected_rows, "Daily-summary row count differs from expectation.")


def execute_source_load(connection, plan: DatasetPlan) -> dict:
    outcomes = {"ALREADY_PROCESSED": 0, "COMPLETED": 0, "PARTIAL": 0}
    for number, batch in enumerate(plan.batches, start=1):
        outcome = process_batch(connection, batch)
        outcomes[outcome] += 1
        print(f"Batch {number:02d}/24 {batch.source_type} {batch.source_file_name}: {outcome}")
    refresh_daily_summary(connection, int(plan.manifest["totals"]["person_days"]))
    print("Daily-summary refresh: FULL PASS")
    return {"outcomes": outcomes}


def execute_load(connection, plan: DatasetPlan) -> dict:
    bootstrap = bootstrap_references(connection, plan)
    print(f"Reference bootstrap: {bootstrap}")
    source_result = execute_source_load(connection, plan)

    from verify_loaded_data import verify_database

    verification = verify_database(connection, plan)
    print("Independent loaded-data verification: PASS")
    return {
        "bootstrap": bootstrap,
        "outcomes": source_result["outcomes"],
        "verification": verification,
    }


def main() -> int:
    connection = None
    try:
        args = parse_args()
        plan = build_dataset_plan(args.data_dir)
        dry_run = validate_dry_run(plan)
        print(
            "Offline validation: PASS "
            f"Batches={dry_run['batches']} Rows={dry_run['rows']} Chunks={dry_run['chunks']}"
        )
        if not args.probe and not args.execute_load and not args.execute_source_load:
            print("Mode: DRY_RUN — Azure CLI and Azure SQL were not accessed.")
            return 0

        server, database = runtime_target()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)
        print("Azure SQL data-plane readiness: PASS")
        if args.probe:
            print("Mode: PROBE — no database data was changed.")
            return 0

        if args.execute_source_load:
            execute_source_load(connection, plan)
            print("Least-privilege source load and summary refresh: PASS")
        else:
            execute_load(connection, plan)
            print("Canonical load and reconciliation: PASS")
        return 0
    except Exception as exc:
        print(f"Loader: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
