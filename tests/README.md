# Verification Tests

Implemented test categories:

- Schema and object existence
- Primary, foreign, unique, check, and default constraints
- Valid and invalid staged loads
- Batch reconciliation and transaction rollback
- Card and Wi-Fi identity resolution
- Duplicate source-observation handling
- `CARD`, `WIFI`, and `BOTH` daily reconciliation
- Unmatched synthetic-device handling
- Administrator, loader, and reporter permission tests
- Masked or restricted-field behavior
- Reporting reconciliation
- Query performance before and after optimization
- Audit and monitoring checks
- Point-in-time recovery validation
- Cleanup verification

Every implementation step defines expected results before it is considered complete.

## Schema behavior suite

`007_verify_schema_behavior.sql` deliberately attempts thirteen invalid writes covering batch timing and reconciliation, staging-row processing, reserved synthetic identifiers, composite fact integrity, device semantics, source-lineage uniqueness, and summary consistency. Each test confirms the exact named constraint responsible for rejection.

All fixtures and valid control rows are created inside one transaction. The suite rolls that transaction back, verifies that no fixtures remain, and reports its results from a table variable that survives the rollback. Two unchanged executions each returned thirteen passes, transaction rollback `PASS`, and fixture cleanup `PASS`.

## Reference-bootstrap test

`008_verify_reference_loader.sql` creates a complete six-entity synthetic reference graph inside a transaction. It proves that the first call inserts the graph, an identical second call is an `UNCHANGED` no-op, and a conflicting natural key fails without changing the retained row. The outer transaction is rolled back and independent cleanup checks confirm that no test fixtures remain.

The deployed procedure returned `PASS` for first application, unchanged rerun, conflict rejection, transaction rollback, and fixture cleanup. The test loaded no canonical reference data.

## Monthly batch-loader test

`009_verify_batch_loader.sql` creates a temporary synthetic reference graph and exercises the four-procedure batch lifecycle for both card and Wi-Fi sources. It proves deterministic first-error precedence, accepted/rejected reconciliation, authoritative source lineage, duplicate-checksum idempotency, cleaned failure state, safe retry, transaction rollback, and independent fixture cleanup.

The deployed component returned `PASS` for card validation, Wi-Fi validation, duplicate checksum handling, failure recovery, transaction rollback, and fixture cleanup. The test loaded no canonical data.

## Daily-summary refresh test

`010_verify_daily_summary_refresh.sql` creates temporary authoritative card and Wi-Fi signals and verifies the derived daily projection. It proves full rebuild reconciliation, inclusive date-range replacement, `CARD` / `WIFI` / `BOTH` classification, first/last observation and signal-count aggregation, invalid-range rejection, transaction rollback, and independent fixture cleanup.

The deployed procedure returned `PASS` for full reconciliation, range replacement, invalid-range rejection, transaction rollback, and fixture cleanup. The test loaded no canonical data.

## Python loader safety tests

`loader/test_loader.py` runs without Azure CLI or Azure SQL. It verifies the canonical manifest plan and 1,000-row chunk guardrail, the complete six-array reference payload, runtime-only target validation, privacy-safe database error categories, and suppression of unexpected exception text.

The loader modules compile under the project Python runtime, all five offline tests pass, and the complete dry-run independently revalidates 24 source files containing 134,372 rows and plans 152 JSON chunks. Default invocation is dry-run, and the canonical path requires `--execute-load`. These checks do not acquire a token or change Azure SQL.

The first reviewed canonical execution produced 24 expected `PARTIAL` batches and exact independent reconciliation. The reviewed unchanged execution then returned reference `UNCHANGED`, all 24 batches `ALREADY_PROCESSED`, and identical integrated and fresh-connection verifier results. Together with the rollback-protected failure/retry and reference-conflict suites, these outcomes satisfy the loading acceptance contract.

## Controlled batch-result test

`011_verify_batch_result_reader.sql` inserts one synthetic terminal batch inside a transaction and verifies the exact privacy-safe result returned by `stage.usp_GetImportBatchResult`. It also expects rejection of a mismatched checksum, non-positive identifier, and null checksum, then rolls back and independently checks fixture cleanup.

The live suite returned exact result `PASS`, three expected rejections, transaction rollback `PASS`, and fixture cleanup `PASS`. A second unchanged procedure deployment completed successfully. The independent canonical verifier then reproduced all canonical-load totals, including 24 batches, 133,892 accepted signals, 480 rejected rows, and 37,151 person-days, with zero unreconciled batches, pending rows, or unexpected fixtures.

The nine-test Python safety suite also checks that `loader/load_data.py` no longer contains a direct `stage.ImportBatch` query, passes the expected batch identifier and checksum to the controlled interface, and processes batches and refreshes the summary in least-privilege mode without invoking reference bootstrap. The existing `--execute-load` path remains administrator-only and retains the independent verifier.

## Security-role test

`012_verify_security_roles.sql` is the positive and negative permission suite. It verifies administrator control and separation from the application roles, then creates transaction-scoped users `WITHOUT LOGIN`, one synthetic terminal batch, and one updatable reporting view over that fixture. Under `EXECUTE AS USER`, the loader must execute the six approved procedures and read its controlled batch result while six direct, administrative, or DDL permissions are denied. The reporter must read the approved `report` view through ownership chaining while direct staging access and five loader, refresh, write, or DDL permissions are denied. The suite guarantees `REVERT`, rolls back every fixture, and independently verifies cleanup.

The suite passed twice in Azure SQL, including after an unchanged role redeployment: both positive paths passed, all twelve expected denials were observed, the outer transaction rolled back, and fixture cleanup passed. A separate audit found zero test users, views, batches, and memberships. The fresh canonical verifier also reproduced the canonical-load totals without unreconciled batches, pending rows, or unexpected fixtures.

## Reporting-view test

`013_verify_reporting_views.sql` is the locally implemented reporting contract and behavior suite. It requires exactly four `report` views with the approved ordered columns and data types, rejects sensitive output columns and ownership overrides, and verifies office-day, department-day, load-quality, validation-category, occupancy, and detection formulas with transaction-scoped fictional fixtures.

A temporary user `WITHOUT LOGIN` assigned to `report_reader` must select all four views while direct `core` and `stage` reads and report write, alteration, and definition permissions remain denied. The suite guarantees context reversion, rolls back every user, membership, reference, summary, batch, and validation fixture, and independently verifies cleanup.

`reporting/test_reporting.py` adds six offline tests for the generator-derived report inventory and totals, visibility of in-progress and failed batch categories, report-only database query boundary, exact mocked database comparison, drift rejection, and offline-default execution. Together with the nine loader tests, all fifteen tests pass. The independent dry verifier produces 261 office-day rows, 2,087 department-day rows, two load-quality rows, and eight validation rows that reconcile to 37,151 person-days, 134,372 received rows, 133,892 accepted rows, and 480 rejected rows without accessing Azure.

The initial live SQL suite passed, while the fresh independent verifier exposed an intermediate decimal-scale truncation in the occupancy result. The corrected view uses narrower decimal operands that retain sufficient division scale, and the fixture now verifies the non-terminating ratio `3 / 17 = 0.176471` rather than an exactly representable ratio.

The corrected live suite passed exact metadata, formulas, four report-reader selections, three expected denials, rollback, and cleanup. The unchanged corrected deployment and suite passed again. A fresh report-only verifier matched all 261 office-day, 2,087 department-day, two load-quality, and eight validation rows. The final audit found exactly four report views and zero test fixtures or application-role memberships.

## Performance-benchmark tests

`performance/test_performance.py` contains seven offline tests for the frozen 90-day and full-history totals, the parameterized report-only query boundary, privacy-safe statistics parsing, sanitized actual-plan extraction, complete row-drift rejection, the strict `KEEP` / `NO_CHANGE` candidate contract, and non-connecting default execution.

The benchmark dry run validates 65 primary-window rows and 9,195 person-days plus 261 full-history rows and 37,151 person-days. Together with the nine loader and six reporting tests, all 22 offline tests pass. No test acquires an access token, connects to Azure SQL, changes Query Store, creates an index, or modifies canonical data.

The reviewed live baseline then verified every frozen row, returned exactly 169 summary logical reads in all ten measured primary runs, and used 672 reads for the full-history regression. Sanitized plan output showed the existing clustered primary keys; the proposed candidate index remained absent. Query Store did not capture the short workload, and its `READ_WRITE` / `AUTO` configuration was left unchanged.

The candidate experiment exposed that the daily-summary suite's original full-refresh metadata assumed an otherwise empty database. The fixture dates now sit outside the canonical range, and expected full-refresh bounds and person-day counts are derived from the transaction's complete authoritative signal set. The corrected suite passed first without the candidate and then with it; the production refresh procedure was unchanged.

The retained candidate's final post-regression measurement used 60 primary and 233 full-history logical reads versus the 169 / 672 baseline. It appeared in the actual plan, occupied 233 pages, and passed the corrected refresh suite, reporting suite, fresh loaded-data verifier, fresh report-only verifier, exact-definition audit, unchanged measurement, and all 22 offline tests.

## Controlled audit and monitoring tests

`014_verify_controlled_audit_activity.sql` is additive and leaves the verified security-role suite unchanged. It creates one no-login user and one report view, adds the user to the reporting role, issues safe database-scoped `GRANT CONNECT` and `DENY CREATE TABLE` statements, rolls the complete transaction back, and independently proves that no user, view, or membership survived.

`monitoring/test_monitoring.py` contains 13 offline tests for exact storage, managed-identity, audit-action, retention, destination, action-group, receiver, and alert contracts; rejection of verbose batch auditing, storage keys, extra destinations or receivers, weak storage, wrong alert thresholds, and missing alert actions; null-safe Azure response parsing; offline-default execution; rollback-source coverage; complete generic audit-category delivery; safe error classification; and sanitized output. The complete monitoring live contract, controlled audit and alert lifecycles, fresh canonical/report-only reconciliation, private notification delivery, and cleanup all passed.

## Recovery tests

`recovery/test_recovery.py` contains 13 offline tests for the exact Basic/local/non-zone
target policy; source-size, seven-day retention, twelve-hour differential, no-LTR, EUR 5,
and two 60-minute boundaries; clean live-control inventory; guarded client-owned marker
transactions; distinct source/target names; exact name confirmations; deletion and target-
audit gates; exact portal-reviewed restore seconds; unambiguous UTC parsing; complete
generator/reporting oracles; and non-connecting default execution.

The dry verifier freezes 24 batches, 133,892 accepted rows, 480 rejected rows, 37,151
person-days, 261 daily rows, and 2,087 department rows. Future explicit live mode reuses
the independent canonical-load and reporting comparisons, and adds exact retained-
index, four-view, empty-role, zero-fixture, TDE, and pre-marker/post-marker checks. Marker
creation validates its controlled result before commit; cleanup requires prior restore-
deletion confirmation and validates its result before commit.

All 49 offline tests now pass: nine loader, six reporting, eight performance, thirteen
monitoring, and thirteen recovery. Compilation, all recovery dry runs, and the public-sample
privacy verifier also pass. No recovery test requested a token, connected to Azure SQL,
created a marker, submitted a restore, or deleted a resource.

## Tableau offline-tooling tests

`tableau/test_tableau.py` verifies the four-file aggregate contract, frozen totals, byte
determinism, explicit local write and byte-for-byte readback, drift and unexpected-file
rejection, default no-write behavior, safe plain and packaged workbook inspection, private
connection and restricted-field rejection, ZIP traversal protection, known-source
enforcement, fail-closed Hyper handling, and absence of Azure CLI, ODBC, token, or live
Tableau paths.

The Tableau tooling reuses the independent reporting oracle and remains offline by design.
No test creates or binds an identity, opens Azure SQL or Tableau, writes a workbook, or
publishes to Tableau Public.

`tableau/test_service_principal.py` adds offline coverage for the temporary reporting
identity: exact one-hour/zero-role policy, fixed transactional binding and cleanup SQL,
clean preflight, precise role and schema permissions, actual stage/core denials,
offline-default execution, provider-error suppression, empty API/Azure/directory role
inventories, exact credential metadata, invalid-secret rejection, identifier-free cleanup
discovery, timed clipboard clearing, partial-failure cleanup, and absence of a secret-file or
command-line credential path. The existence of guarded live flags is not approval to run them.
