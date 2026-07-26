# SQL Scripts

This directory contains ordered, rerunnable scripts for:

1. Schemas
2. Tables
3. Constraints and relationships
4. Load procedures and validation
5. Security roles and grants
6. Reporting views and procedures
7. Baseline and optimized indexes
8. Verification and negative tests
9. Cleanup

## Implemented order

| Order | Script | Purpose | Status |
|---|---|---|---|
| 001 | `001_create_schemas.sql` | Create the `stage`, `core`, and `report` security and ownership boundaries | Deployed and rerun successfully; ownership and empty state verified |
| 002 | `002_create_stage_tables.sql` | Create import-batch, raw landing, and validation-error tables | Deployed and rerun successfully; structure and constraints verified |
| 003 | `003_create_core_reference_tables.sql` | Create offices, departments, fictional people, devices, assignments, and access points | Deployed and rerun successfully; structure, constraints, and assignment index verified |
| 004 | `004_create_core_fact_tables.sql` | Create the authoritative normalized attendance fact and reproducible daily summary | Deployed and rerun successfully; tables, constraints, and integrity indexes verified |
| 005 | `005_create_assignment_procedure.sql` | Create the transaction-aware, concurrency-safe device-assignment procedure | Deployed and rerun successfully; positive, negative, concurrency-contract, and fixture-cleanup tests passed twice |
| 006 | `006_create_baseline_indexes.sql` | Add the minimum covering index required by daily reconciliation | Deployed and rerun successfully; exact three-key/two-include shape passed twice |
| 007 | `007_create_reference_loader.sql` | Create the strict, atomic, rerunnable JSON reference-data bootstrap | Deployed successfully; application, no-op rerun, conflict, rollback, and cleanup tests passed |
| 008 | `008_create_batch_loader.sql` | Create the application-locked monthly card/Wi-Fi batch lifecycle, validation, reconciliation, and recoverable failure procedures | Deployed successfully; both source types, duplicate checksum, failure recovery, rollback, and cleanup tests passed |
| 009 | `009_create_daily_summary_refresh.sql` | Rebuild the reproducible daily attendance projection from authoritative normalized signals, either fully or for an inclusive date range | Deployed successfully; full reconciliation, range replacement, invalid-range rejection, rollback, and cleanup tests passed |
| 010 | `010_create_batch_result_reader.sql` | Return one checksum-scoped batch result without granting the application loader direct staging-table access | Deployed and rerun successfully; exact result, three expected rejections, rollback, cleanup, and canonical regression checks passed |
| 011 | `011_create_security_roles.sql` | Create the durable `app_loader` and `report_reader` roles with controlled procedure and reporting-schema permissions | Deployed and rerun unchanged; exact catalog and behavioral permission suite passed twice |
| 012 | `012_create_reporting_views.sql` | Create four aggregate attendance and source-quality views without person-level publication | Deployed and rerun unchanged after focused decimal-scale correction; complete suite and report-only reconciliation passed |
| 014 | `014_create_performance_candidate.sql` | Create the measured office/date/detection summary index only from a confirmed absent state | Deployed; final post-regression result reduced primary reads by 64.50 percent and passed all regressions; retained by `KEEP` decision |
| 015 | `015_remove_performance_candidate.sql` | Transactionally remove and verify absence of the retained performance candidate | Exercised automatically during failed pre-acceptance attempts; retained as the explicit rollback path |

## Schema behavior verification

`tests/007_verify_schema_behavior.sql` is the rollback-protected schema behavior suite. It checks thirteen representative staging, reference, fact, lineage, and summary rules by confirming the exact constraint that rejects each invalid write. The unchanged suite passed twice with `13/13`, transaction rollback `PASS`, and fixture cleanup `PASS`. No deployment script is paired with 007 because it verifies the complete schema created by scripts 001–006.

The table layer is deliberately split into staging, core reference, and core fact scripts. This keeps each deployment atomic and gives each step a focused verification scope.

Each script must be rerunnable and must fail rather than leave a partially applied unit of work.
