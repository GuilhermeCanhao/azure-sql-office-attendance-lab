# Schema Design

## Design principles

- The initial dataset contains one fictional office, but office identity is not hard-coded into facts.
- Raw card and Wi-Fi rows remain distinguishable from validated relational facts.
- Every imported row is traceable to a batch and source row number.
- `AttendanceSignal` is the authoritative normalized fact.
- `DailyAttendanceSummary` is derived and reproducible.
- Reporter access is provided through the `report` schema, not direct table grants.
- Invalid or unmatched source rows remain observable without contaminating the core model.
- Observation timestamps are stored in UTC; the fictional Portuguese office uses SQL Server time-zone name `GMT Standard Time` when deriving its local attendance date.

## Schemas

| Schema | Responsibility |
|---|---|
| `stage` | Import batches, raw source rows, and validation errors |
| `core` | Constrained business entities, normalized signals, and reproducible daily summaries |
| `report` | Privacy-safe analytical views and reviewed stored procedures |

## Proposed tables

### `stage.ImportBatch`

Durable record of one source-file load.

| Column | Purpose |
|---|---|
| `ImportBatchId` | Surrogate primary key |
| `SourceType` | Constrained to `CARD` or `WIFI` |
| `SourceFileName` | Synthetic input filename, not a local absolute path |
| `FileChecksum` | Detect accidental repeated loads |
| `StartedAt`, `CompletedAt` | Operational timing |
| `Status` | `STARTED`, `COMPLETED`, `PARTIAL`, or `FAILED` |
| `RowsReceived`, `RowsAccepted`, `RowsRejected` | Reconciliation counts |
| `ErrorMessage` | Sanitized batch-level failure detail |

Duplicate-content constraint: unique combination of source type and checksum. A renamed file with identical content must not bypass duplicate-batch detection.

### `stage.CardAccessEvent`

Raw fictional card-access rows awaiting validation.

| Column | Purpose |
|---|---|
| `ImportBatchId`, `SourceRowNumber` | Batch lineage and composite uniqueness |
| `ObservedAtRaw` | Untrusted source timestamp text |
| `PersonnelCodeRaw` | Untrusted synthetic personnel reference |
| `AccessPointCodeRaw` | Untrusted fictional reader reference |
| `ProcessingStatus` | Pending, accepted, or rejected |

### `stage.WifiObservation`

Raw fictional managed-device observations awaiting validation.

| Column | Purpose |
|---|---|
| `ImportBatchId`, `SourceRowNumber` | Batch lineage and composite uniqueness |
| `ObservedAtRaw` | Untrusted source timestamp text |
| `DeviceTokenRaw` | Opaque synthetic device token |
| `AccessPointCodeRaw` | Untrusted fictional access-point reference |
| `SignalStrengthRaw` | Optional untrusted text value, parsed as an integer during validation |
| `ProcessingStatus` | Pending, accepted, or rejected |

Keeping signal strength as text is intentional. Raw landing must accept controlled malformed synthetic values so validation can reject and audit them; assigning an integer type here would move that failure outside the documented validation process.

### `stage.ImportError`

One sanitized validation error per rejected source row and rule.

Important columns: batch, source row, source table, validation code, and safe error description. It will not store an unrestricted copy of the original row.

### `core.Office`

One row initially, including fictional office code, display name, timezone, capacity, and active state. `OfficeCode` is unique. Core reference identifiers use `int` consistently; high-growth operational and fact identifiers use `bigint`.

### `core.Department`

Fictional organizational grouping used for aggregate reporting. `DepartmentCode` is unique.

### `core.Person`

Synthetic personnel code, fictional display name, synthetic email, department, and validity dates. Department assignments remain static throughout version 1; historical department movement is an explicitly deferred extension. A separate current-state flag is deliberately omitted because it could drift out of agreement with the validity dates.

Constraints include:

- Unique personnel code
- Unique synthetic email
- Email must use the project's reserved example domain
- `ValidTo` is null or later than `ValidFrom`; `ValidTo` is an exclusive boundary

Direct reporter access will not be granted. Reporting views will expose only fields required by the dashboard.

### `core.Device`

Opaque synthetic device token and lifecycle state. Tokens will look like `DEV-8F2A91C4`, not MAC addresses.

### `core.PersonDeviceAssignment`

Timestamp-bounded relationship between a fictional person and device. All validity periods are half-open: `ValidFrom` is inclusive and `ValidTo` is exclusive.

The deployed `core.usp_AssignDevice` procedure rejects overlapping assignments for the same device under serializable range-locking semantics, and the loader will not receive direct table-modification permission. This cross-row rule cannot be expressed reliably as a simple `CHECK` constraint. A unique supporting index on device and start time makes identical boundaries impossible and supports the procedure's range check; it does not independently prevent every overlap. Behavioral verification proved that bounded and open-ended periods are enforced as half-open intervals: exact adjacency succeeds, while partial, contained, and open-ended overlaps fail.

### `core.AccessPoint`

Fictional access-point code, office, type (`CARD_READER` or `WIFI_AP`), display label, and active state.

### `core.AttendanceSignal`

Authoritative normalized fact produced from an accepted card event or resolved Wi-Fi observation.

| Column | Purpose |
|---|---|
| `AttendanceSignalId` | Surrogate primary key |
| `ImportBatchId`, `SourceRowNumber` | Complete source lineage |
| `OfficeId`, `PersonId`, `AccessPointId` | Resolved relational references |
| `DeviceId` | Resolved opaque device for Wi-Fi signals; null for card signals |
| `SignalType` | `CARD` or `WIFI` |
| `ObservedAtUtc` | Validated UTC timestamp |
| `AttendanceDateLocal` | Office-local reporting date calculated during the controlled load |

Source lineage is the authoritative uniqueness rule: one batch row can produce at most one normalized signal. Observation-value uniqueness is not enforced because two legitimate source events could share the same timestamp and resolved identifiers. Composite foreign keys ensure that signal type matches batch source type and that the access point belongs to the recorded office.

### `core.DailyAttendanceSummary`

One row per attendance date, office, and person.

| Column | Purpose |
|---|---|
| `AttendanceDateLocal`, `OfficeId`, `PersonId` | Natural uniqueness of the daily result |
| `DetectionMethod` | `CARD`, `WIFI`, or `BOTH` |
| `FirstObservedAtUtc`, `LastObservedAtUtc` | Daily observed range |
| `CardSignalCount`, `WifiSignalCount` | Reconciliation support |
| `RefreshedAtUtc` | Procedure execution timestamp |

The summary will be refreshed by a stored procedure from `core.AttendanceSignal`; manual inserts by the reporting identity are prohibited.

`AttendanceSignal` is the source of truth. `DailyAttendanceSummary` is a controlled, disposable projection that must always be reproducible and reconcilable from the fact table.

## Reporting interfaces

The reporting layer deployed the reviewed aggregate publication surface:

- `report.vw_DailyAttendanceTrend`
- `report.vw_DailyDepartmentAttendance`
- `report.vw_LoadQualitySummary`
- `report.vw_ValidationIssueSummary`

No reporting procedure was created because version 1 has no justified parameterized procedure contract. The future Tableau identity will use the `report_reader` role's read-only `report` schema boundary and will not receive direct `stage` or `core` access.

## Baseline indexing decision

The minimum baseline index is implemented as `core.AttendanceSignal (AttendanceDateLocal, OfficeId, PersonId) INCLUDE (SignalType, ObservedAtUtc)`. Its key order matches the daily-summary grouping, while the included columns cover classification counts and first/last observation calculations without widening the navigation key.

Existing indexes already cover batch lineage, natural reference lookup, device-assignment resolution, composite integrity, and the daily-summary natural key. The following remain hypotheses rather than indexes added blindly:

- `core.AttendanceSignal (PersonId, ObservedAtUtc)` for person/date investigation
- A date-oriented reporting index on the daily summary shaped by the measured Tableau query
- Any index proposed by Query Store or the execution plan after representative synthetic data exists

The performance exercise will preserve an intentionally inefficient baseline, inspect its actual execution plan and Query Store telemetry, then add only a measured index or query rewrite. This separation avoids claiming that an empty-table deployment proves runtime performance.

## Resolved implementation decisions

- Unmatched Wi-Fi devices are rejected from the authoritative fact and exposed only through privacy-safe aggregate data-quality counts.
- The daily summary is a physical, reproducible table refreshed by a controlled procedure so the lab can demonstrate reconciliation, permissions, failure handling, and performance optimization.
- Duplicate batch detection uses source type plus content checksum, independent of filename.
- Source lineage controls normalized-signal uniqueness; value-identical observations are not assumed to be duplicates.
- Device-assignment overlap is prevented through a controlled procedure combined with denial of direct loader DML.
- The fictional office uses `GMT Standard Time` for SQL Server time-zone conversion while all authoritative observation timestamps remain UTC.

Historical department attribution is deliberately excluded from version 1. The fictional generator will keep departments stable so this simplification does not make historical results misleading.
