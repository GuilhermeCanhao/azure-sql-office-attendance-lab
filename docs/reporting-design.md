# Reporting Design

## Status

The reporting layer was completed on 2026-07-17. The smallest reporting surface that supports the Tableau dashboard and privacy-safe data-quality reporting is deployed and independently verified. No procedure, permission, user, external identity, Azure resource, or canonical row was added or changed by the reporting deployment.

## Design decision

The `core.DailyAttendanceSummary` table is intentionally person-day grain. It contains a person key and observation timing, so publishing it directly would teach the wrong security pattern even though the lab data is fictional. Tableau should receive aggregate analytical results rather than a lightly renamed copy of the core table.

The reporting layer exposes four views and no stored procedure:

| View | Grain | Purpose |
|---|---|---|
| `report.vw_DailyAttendanceTrend` | Attendance date and office | Daily attendance, office capacity, CARD/WIFI/BOTH mix, badge-observed count, and Wi-Fi-only incremental count |
| `report.vw_DailyDepartmentAttendance` | Attendance date, office, and department | Department trend and detection-method counts without a person identifier |
| `report.vw_LoadQualitySummary` | Source type | Terminal batch and row reconciliation totals, including completed-with-rejections rather than treating `PARTIAL` as abandoned |
| `report.vw_ValidationIssueSummary` | Source type and validation code | Aggregate controlled rejection counts, including unmatched-device counts, without filenames, source-row numbers, or error text |

The previously listed detection-method view would repeat measures already present in both attendance views. The proposed generic reporting procedure has no approved parameterized use case yet. Excluding both keeps the public contract smaller and avoids granting an interface merely because it was once listed as a candidate.

## Column contracts

### `report.vw_DailyAttendanceTrend`

- `AttendanceDateLocal`
- `OfficeCode`
- `OfficeName`
- `OfficeCapacity`
- `PersonDayCount`
- `CardOnlyPersonDays`
- `WifiOnlyPersonDays`
- `BothPersonDays`
- `BadgeObservedPersonDays`
- `WifiObservedPersonDays`
- `OccupancyRate`

`BadgeObservedPersonDays` is `CARD + BOTH`. `WifiObservedPersonDays` is `WIFI + BOTH`. `WifiOnlyPersonDays` is the additional aggregate estimate found without a card signal; it must not be described as proof of an individual's location. `OccupancyRate` uses one office-day denominator and must be returned as a bounded decimal rather than formatted text.

### `report.vw_DailyDepartmentAttendance`

- `AttendanceDateLocal`
- `OfficeCode`
- `OfficeName`
- `DepartmentCode`
- `DepartmentName`
- `PersonDayCount`
- `CardOnlyPersonDays`
- `WifiOnlyPersonDays`
- `BothPersonDays`

Office capacity is deliberately omitted at department grain because repeating a whole-office denominator across departments invites invalid aggregation. Version 1 department assignments are static by contract; the view must not imply support for historical department movement.

### `report.vw_LoadQualitySummary`

- `SourceType`
- `TerminalBatchCount`
- `InProgressBatchCount`
- `CompletedWithoutRejectsBatchCount`
- `CompletedWithRejectsBatchCount`
- `FailedBatchCount`
- `RowsReceived`
- `RowsAccepted`
- `RowsRejected`
- `AcceptanceRate`

A terminal `PARTIAL` batch means that accepted and controlled rejected rows reconciled successfully. The reporting label is therefore `CompletedWithRejectsBatchCount`; it must not be presented as a partial transaction or abandoned load. `InProgressBatchCount` is an implementation-time safeguard: omitting it could make a `STARTED` and therefore unreconciled batch invisible in the aggregate publication surface.

### `report.vw_ValidationIssueSummary`

- `SourceType`
- `ValidationCode`
- `RejectedRowCount`

Validation codes are generic controlled categories. The view will not expose batch identifiers, source filenames, source-row numbers, raw values, free-form error descriptions, or timestamps.

## Disclosure boundary

Allowed reporting fields are fictional office and department labels, local dates, detection categories, capacities, aggregate person-day counts, aggregate batch totals, and aggregate validation-code counts.

The reporting layer must not expose:

- `PersonId`, personnel code, display name, or synthetic email;
- device or assignment identifiers;
- access-point identifiers;
- first or last individual observation timestamps;
- signal-level or source-row lineage;
- batch identifiers, checksums, source filenames, or error text;
- refresh, connection, account, tenant, subscription, endpoint, or client-address metadata.

The public Tableau workbook uses sanitized extracts of these aggregate views. It does not publish a live Azure SQL connection.

## Authorization contract

The existing empty `report_reader` role already has `SELECT` on the `report` schema and explicit denials on `stage`, `core`, report writes, schema alteration, and definition access. Creating reviewed views in `report` will make them readable through the existing publication boundary without adding a new grant.

This reporting step does not bind a real Entra reporting identity. That remains deferred until the Tableau end-to-end connection test. Verification will use a transaction-scoped user `WITHOUT LOGIN` assigned to `report_reader`, followed by guaranteed `REVERT`, rollback, and independent cleanup checks.

## Verification contract

Implementation is acceptable only if all of the following pass:

1. A rerunnable deployment creates exactly the four approved views with the declared columns, order, compatible types, and `dbo` ownership chain.
2. Static checks reject any sensitive column name or unexpected object in the reporting contract.
3. Daily totals reconcile across the office and department views and preserve `CARD + WIFI + BOTH = PersonDayCount`.
4. Badge-observed, Wi-Fi-observed, Wi-Fi-only, capacity, and occupancy formulas reconcile exactly and remain within valid bounds.
5. Load-quality totals reconcile to terminal batches, and received, accepted, and rejected counts preserve the batch contract.
6. Validation totals reconcile to rejected rows and retain generic validation codes only.
7. A separate Python verifier derives expected report rows from the generator's independent daily, reference, batch-result, and validation-count files rather than from Azure SQL core tables.
8. Under `EXECUTE AS USER`, `report_reader` can select all four views but cannot select their underlying `stage` or `core` objects or modify a report view.
9. The behavior suite rolls back all test principals and fixtures and a separate administrator query confirms zero leftovers.
10. The unchanged deployment and complete suite pass a second time, followed by a fresh independent aggregate reconciliation.

## Implementation sequence

1. Add one rerunnable SQL deployment for the four views.
2. Add exact metadata, formula, permission, rollback, and cleanup tests.
3. Add the independent local expected-report verifier and offline tests.
4. Run compilation, offline verification, complete canonical dry comparison, privacy scanning, and SQL structural review.
5. Deploy only after the reporting contract, local verification, and privacy review pass.
6. Use the harmless data-plane probe as the deployment gate.
7. Deploy, run the live suite, rerun unchanged, independently reconcile, and document the sanitized result.

No Tableau workbook or external identity is part of this reporting step.

## Local implementation status

`sql/012_create_reporting_views.sql` creates the four approved views inside one rerunnable transaction. The only person key used by the view definitions is an internal join needed to associate the person-day summary with its static version 1 department; it is not projected through the reporting boundary.

`tests/013_verify_reporting_views.sql` requires exactly the four reviewed views and exact ordered column/type contracts. It rejects sensitive output columns, verifies formulas with fictional transaction-scoped fixtures, proves all four views are readable through a temporary `report_reader` user, confirms direct `core` and `stage` reads and report write/alter/definition authority remain denied, guarantees `REVERT`, rolls back, and independently checks cleanup.

`reporting/verify_reporting.py` defaults to offline mode. It derives the complete expected reporting rows from the generator's independent daily, reference, batch-result, and validation-count files. Its explicit future live mode queries only the four `report` views through the existing privacy-safe Entra-token connection helpers.

Local verification passed:

- Python compilation with an isolated temporary bytecode cache;
- all nine existing loader tests and six reporting tests, for fifteen total passes;
- an offline report oracle of 261 office-day rows, 2,087 department-day rows, two source-quality rows, and eight validation-category rows;
- exact dry reconciliation of 37,151 person-days, 134,372 received rows, 133,892 accepted rows, and 480 rejected rows;
- the unchanged complete loader dry run of 24 batches and 152 chunks;
- structural checks for four views, rollback and reversion guards, report-only verifier queries, whitespace, and credential patterns.

The reviewed deployment passed the harmless data-plane readiness gate. The initial deployment and SQL suite passed, but the fresh independent verifier detected that SQL Server's precision/scale rules had reduced the intermediate result of `decimal(38,10)` division before the final `decimal(9,6)` conversion. For example, `174 / 350` returned `0.497142` rather than rounding to `0.497143`.

The focused correction uses `decimal(19,6)` operands so the division retains sufficient intermediate scale. The SQL fixture was strengthened from an exactly representable `3 / 10` case to `3 / 17`, which must round to `0.176471`; this makes the regression observable without reusing the implementation expression as its oracle.

After the correction:

- exact metadata and aggregate formulas returned `PASS`;
- all four report-reader selections succeeded and three expected boundary denials were observed;
- transaction rollback and fixture cleanup returned `PASS`;
- the unchanged corrected deployment and complete suite passed again;
- the fresh report-only verifier matched 261 office-day rows, 2,087 department-day rows, two load-quality rows, eight validation-category rows, 37,151 person-days, 134,372 received rows, 133,892 accepted rows, and 480 rejected rows;
- the final audit found four reporting views, zero test users, references, batches, or memberships, and zero members in `app_loader` and `report_reader`.

No external identity was bound. The reporting layer is complete; the Tableau authentication test remains deferred to its end-to-end phase.
