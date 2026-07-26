# Controlled Loader

This directory contains the privacy-safe local transport client and independent post-load verifier defined in the [controlled staging-load contract](../docs/loading-contract.md).

The client uses a short-lived Microsoft Entra token obtained from the existing Azure CLI session. The token is held only in process memory and passed to Microsoft ODBC Driver 18 through `SQL_COPT_SS_ACCESS_TOKEN`. No password, persistent credential, endpoint, connection string, client address, account identifier, or raw provider exception is printed or stored.

## Components

| File | Purpose |
|---|---|
| `load_data.py` | Offline validation, harmless connectivity probing, and explicitly authorized canonical loading |
| `verify_loaded_data.py` | Independent reconciliation of Azure SQL with the manifest and expected files |
| `loader_common.py` | Shared contract, chunking, token, connection, and privacy-safety helpers |
| `test_loader.py` | Offline contract, guardrail, target-validation, error-category, and output-suppression tests |
| `requirements.txt` | Pinned project-local Python dependency |

## Local prerequisites

- Python 3.9 or later
- Microsoft ODBC Driver 18 for SQL Server
- Azure CLI on `PATH` with an active personal-lab session
- Dependencies from `requirements.txt` installed in the project-local virtual environment

## Safety modes

Run commands from the project root with the project-local Python interpreter.

```bash
# Default: complete offline verification only
.venv/bin/python loader/load_data.py

# Equivalent explicit form
.venv/bin/python loader/load_data.py --dry-run
```

Dry-run mode verifies the independent generator contract, complete manifest inventory, file hashes, row counts, reference payload, source schemas, and all planned JSON chunks. It does not invoke Azure CLI, acquire a token, or connect to Azure SQL.

The optional probe requires runtime-only target variables:

```bash
export ATTENDANCE_SQL_SERVER='<runtime server endpoint>'
export ATTENDANCE_SQL_DATABASE='<runtime database name>'
.venv/bin/python loader/load_data.py --probe
```

Do not put these variables in `.env`, shell scripts, screenshots, issue text, or committed files. The probe executes only a harmless database-context and status query. If the target is still resuming, it can probe `master` to distinguish logical-server reachability from target-database readiness. It never changes Azure resources, firewall configuration, or database data.

## Canonical-load gate

The real load is deliberately unavailable through the default invocation. It requires the explicit `--execute-load` flag:

```bash
.venv/bin/python loader/load_data.py --execute-load
```

The first canonical load and one unchanged idempotency rerun have been approved and completed. Future executions remain explicit recovery or verification operations and should be deliberately approved. The client:

1. repeats complete offline verification;
2. acquires a short-lived Azure SQL token and passes the data-plane probe;
3. bootstraps the six reference entities through the verified procedure;
4. processes each canonical source file on one retained ODBC session;
5. sends JSON chunks containing at most 1,000 rows;
6. commits only after that file finalizes with the independently expected counts;
7. records a sanitized reconciled failure state after rolling back a failed file;
8. accepts an already-processed checksum only after its persisted terminal counts match expectations;
9. runs the verified full daily-summary refresh after all 24 batches reconcile; and
10. invokes the independent loaded-data verifier.

The batch begin, chunk append, finalize, and failure calls remain on the same ODBC session because the checksum application lock is session-owned. Each newly processed monthly file has one client transaction covering every chunk and finalization.

## Least-privilege source-load gate

The future `app_loader` role must not bootstrap references or run the administrative verifier. Its explicit client path is:

```bash
.venv/bin/python loader/load_data.py --execute-source-load
```

This mode still performs the complete offline verification and harmless readiness gate. It then processes the 24 source batches, verifies an already-processed batch through the checksum-scoped `stage.usp_GetImportBatchResult` procedure, and performs the full daily-summary refresh. It does not call `core.usp_BootstrapReferenceData`, query `stage.ImportBatch` directly, or invoke the independent verifier.

The mode is implemented and tested locally but has not been executed against Azure SQL. It becomes operational only after the controlled batch-result procedure and `app_loader` permissions are deployed and behaviorally verified.

## Independent post-load verification

After a load, or as a separate read-only check, run:

```bash
.venv/bin/python loader/verify_loaded_data.py
```

The verifier compares Azure SQL with the generator's independent references, expected batch results, expected validation counts, and complete expected daily file. It requires exact canonical reference and batch inventories; reconciles staging, error, fact, and summary totals; checks `CARD`, `WIFI`, and `BOTH` person-days; and rejects pending rows, abandoned or failed batches, and test fixtures.

## Local verification

```bash
PYTHONPYCACHEPREFIX=/tmp/attendance-loader-pycache \
  .venv/bin/python -m py_compile loader/*.py

PYTHONPYCACHEPREFIX=/tmp/attendance-loader-pycache \
  .venv/bin/python -m unittest loader/test_loader.py -v

.venv/bin/python loader/load_data.py --dry-run
```

Dry-run and test commands do not change Azure SQL. Nine offline tests now also prove that the client contains no direct `stage.ImportBatch` query, sends the expected batch identifier and checksum to the controlled result procedure, skips bootstrap in the source-only path, and keeps execution modes mutually exclusive. Only an explicit load gate can run database procedures.
