# Performance Optimization Design

## Status

The design was approved on 2026-07-17 and completed on 2026-07-18. The baseline and reversible candidate experiment passed their frozen contracts. The candidate index is retained with an explicit cleanup script; Query Store configuration, caches, Azure resources, firewall rules, identities, permissions, views, procedures, and canonical rows remain unchanged.

## Local implementation status

`performance/benchmark_reporting.py` defaults to a non-connecting validation run and requires the explicit `--execute-baseline` flag before it can acquire a short-lived Microsoft Entra token or connect to Azure SQL. It uses the existing guarded runtime-target and token helpers and never prints raw provider messages, plan XML, runtime targets, or connection metadata.

`performance/performance_common.py` builds the frozen 90-day and full-history oracle from the generator's independent reporting expectations. The live client verifies every returned row, parses only sanitized logical-read and timing facts, extracts only operator and access-path facts from the actual plan, and reads current index and Query Store options without modifying them. Session-scoped statistics and plan options are explicitly restored after use.

`performance/test_performance.py` covers the frozen totals, report-only parameterized query, statistics parser, sanitized plan extraction, drift rejection, candidate decision contract, and offline-default behavior. Together with the existing loader and reporting suites, 22 offline tests pass. Compilation, canonical dry runs, and privacy/safety scans also pass without Azure CLI or Azure SQL access.

## Read-only baseline result

The reviewed baseline ran after the harmless data-plane gate separated normal serverless resume from logical-server reachability. Ten measured primary executions each used exactly 169 logical reads against `DailyAttendanceSummary`; the full-history regression used 672. All 65 primary and 261 regression rows matched the frozen oracle. Median client elapsed time on the final complete run was 50.569 ms, while SQL CPU and elapsed values rounded to 0 ms and are not treated as meaningful measurement.

The actual plan used the clustered primary keys for `core.Office` and `core.DailyAttendanceSummary` and contained clustered index seeks, a nested loop, filter, stream aggregate, and compute scalars. The summary table had only its clustered primary-key index, and the candidate was absent.

Query Store was `READ_WRITE` with `AUTO` capture but did not retain the short workload. Its settings were left unchanged, so the accepted baseline measurement is the exact oracle comparison, stable `STATISTICS IO`, and sanitized actual plan. The public rollup is summarized in [Verification Summary](verification-summary.md).

The baseline justifies testing the narrower office/date/detection hypothesis under a separate approval because it differs materially from the current date-leading wide clustered path. It does not justify keeping the candidate before the identical after-measurement and regression contract passes.

## Candidate result

The candidate met the contract and is retained. The final post-regression measurement reduced primary logical reads from 169 to 60, a 64.50 percent reduction, and full-history reads from 672 to 233, a 65.33 percent reduction. The actual plan used `IX_core_DailyAttendanceSummary_OfficeDateMethod`, every aggregate row remained exact, and the candidate occupied 233 pages after the refresh regression workload.

The initial pre-regression candidate measurement was lower at 32 primary reads, 118 full-history reads, and 129 pages. The rollback-protected full-summary refresh exercised the index's write path and left a larger physical structure. The final decision intentionally uses the stabilized 60 / 233 / 233-page result rather than the initial best case.

The daily-summary regression initially exposed a historical test assumption: it expected its transaction-scoped rows to be the only summary input, which stopped being true after the canonical data load. The test now uses dates outside the canonical range and derives full-refresh expectations from all authoritative signals inside its transaction. It passed clean-state before the candidate index and passed again with it. No production procedure changed.

The reporting and refresh suites, fresh canonical verifier, fresh report-only verifier, exact candidate-definition audit, unchanged candidate measurement, and all 22 offline tests passed. The public rollup is summarized in [Verification Summary](verification-summary.md).

## Representative query

The experiment uses the Tableau-facing office daily-trend query through `report.vw_DailyAttendanceTrend`:

```sql
SELECT
    AttendanceDateLocal,
    OfficeCode,
    OfficeName,
    OfficeCapacity,
    PersonDayCount,
    CardOnlyPersonDays,
    WifiOnlyPersonDays,
    BothPersonDays,
    BadgeObservedPersonDays,
    WifiObservedPersonDays,
    OccupancyRate
FROM report.vw_DailyAttendanceTrend
WHERE OfficeCode = @OfficeCode
  AND AttendanceDateLocal >= @FromDate
  AND AttendanceDateLocal <= @ThroughDate
ORDER BY AttendanceDateLocal;
```

This query is selected because it is the primary dashboard grain, stays inside the approved reporting boundary, has complete generator-derived expected output, and reads only date, office, detection, and aggregate count information from the underlying person-day summary. It does not expose or investigate an individual.

Plan capture and Query Store catalog inspection require administrative diagnostic authority, so the benchmark connection will use the existing Microsoft Entra administrator. The measured business statement itself queries only `report.vw_DailyAttendanceTrend`; no benchmark statement reads person-level `core` data. Existing permission tests continue to prove that `report_reader` can execute the reporting query while direct `stage` and `core` access remains denied.

The department query is not selected for the first experiment because its joins are necessary for the requested semantic grain and would make it harder to attribute a change specifically to the summary-table access path.

## Fixed parameter sets

| Set | Office | Inclusive dates | Expected result rows | Expected person-days | Expected `CARD` | Expected `WIFI` | Expected `BOTH` |
|---|---|---|---:|---:|---:|---:|---:|
| Primary dashboard window | `PT-LAB-01` | 2026-04-01 through 2026-06-30 | 65 | 9,195 | 316 | 2,717 | 6,162 |
| Full-history regression | `PT-LAB-01` | 2025-07-01 through 2026-06-30 | 261 | 37,151 | 1,236 | 11,082 | 24,833 |

The values are fixed before measurement and derived from the independent generator outputs. Changing the dates after seeing a plan or timing result would invalidate the comparison.

## Existing access path

`core.DailyAttendanceSummary` has a clustered primary key on `(AttendanceDateLocal, OfficeId, PersonId)`. The reporting query needs `DetectionMethod` but does not need the individual timestamps, signal counts, or refresh timestamp stored in each wide clustered row.

The existing nonclustered index on `core.AttendanceSignal (AttendanceDateLocal, OfficeId, PersonId) INCLUDE (SignalType, ObservedAtUtc)` supports summary reconstruction and is not a reporting-query index. It must not be repurposed or removed during this phase.

## Candidate hypothesis

Only after the baseline is captured, the plan may justify testing this narrow covering index:

```sql
CREATE INDEX IX_core_DailyAttendanceSummary_OfficeDateMethod
ON core.DailyAttendanceSummary
(
    OfficeId,
    AttendanceDateLocal,
    DetectionMethod
);
```

The hypothesis is that equality on office followed by the date range and detection grouping can use a narrower access path than the wide clustered rows. The clustered person key is carried by SQL Server's nonclustered index structure and is not an explicit reporting output.

This is not an approved deployment and not a guaranteed optimization. It will be rejected if the baseline plan already has an efficient equivalent path, if the candidate is unused, if logical reads do not improve materially, or if regression checks fail.

## Baseline measurement protocol

1. Pass the harmless data-plane readiness probe after any serverless resume.
2. Record the current index inventory and Query Store options with read-only catalog queries.
3. Use one fixed connection and a uniquely tagged, parameterized query text.
4. Execute two unmeasured warm-up runs; do not clear the buffer pool or plan cache.
5. Execute ten measured runs for the primary parameter set without actual-plan capture overhead.
6. Execute the full-history regression set and verify its complete result.
7. Capture one actual execution plan separately from timing runs.
8. Read only the tagged query's Query Store plan and runtime aggregates for the controlled measurement window.
9. Retain sanitized metrics and plan facts, not raw endpoints, accounts, client addresses, or unrestricted Query Store exports.

The experiment must not use `DBCC DROPCLEANBUFFERS`, `DBCC FREEPROCCACHE`, Query Store clearing, automatic-tuning changes, forced plans, Query Store hints, service-tier changes, auto-pause changes, or temporary paid resources.

## Metrics and interpretation

Primary measurements:

- logical reads by object from `SET STATISTICS IO`;
- seek, scan, join, sort, and aggregate operators in the actual plan;
- estimated and actual row counts at the important access operator;
- whether the candidate index is used after deployment.

Supporting checks:

- CPU and elapsed time from `SET STATISTICS TIME`;
- Query Store execution count, average duration, CPU, logical I/O, and plan identity;
- index page count and storage footprint.

The database is serverless and the canonical data is small. Millisecond differences can be dominated by resume, compilation, network, and shared-service noise. Median elapsed time will be reported, but logical reads and a justified plan-shape change are the acceptance basis.

## Acceptance contract

Before and after any candidate index, all of the following must hold:

1. The primary query returns exactly 65 rows and 9,195 person-days with the expected detection totals.
2. The full-history query returns exactly 261 rows and 37,151 person-days with the expected detection totals.
3. Every returned aggregate row matches the independent reporting oracle; row count alone is insufficient.
4. The candidate reduces primary-window logical reads by at least 30 percent.
5. Full-history logical reads do not regress.
6. The actual plan uses the candidate for the intended summary access rather than retaining it unused.
7. Median CPU and elapsed time are reported without claiming significance when values are within normal noise.
8. The complete reporting verifier and all fifteen offline tests still pass.
9. The rollback-protected daily-summary refresh and reporting-view behavior suites still pass.
10. The final database contains no benchmark fixtures, forced plan, Query Store hint, or unintended index.

If these conditions are not met, the candidate index must be removed and the valid engineering outcome is `NO CHANGE`. The portfolio must not manufacture an improvement merely to satisfy the phase title.

## Write and storage trade-off

Any nonclustered index adds storage and must be maintained when the reproducible daily summary is rebuilt. The performance exercise records the candidate's page count and reruns the rollback-protected refresh behavior suite. A tiny read improvement is not enough to justify permanent write overhead, even in this small lab.

The experiment does not perform an uncontrolled canonical rebuild merely to produce timing data. Functional refresh regression uses the existing transaction-scoped fixture suite unless a later measurement justifies more.

## Query Store boundary

Query Store is supporting telemetry, not the sole benchmark. Its current operation mode and capture policy must be inspected before the workload. Existing history will not be cleared. A unique safe query tag and bounded measurement timestamps will isolate the controlled executions as far as the current capture policy permits.

If the current policy does not capture the short workload, the performance exercise will rely on `STATISTICS IO`, `STATISTICS TIME`, and the actual plan rather than changing database-wide Query Store settings merely to force telemetry.

## Privacy and publication

The benchmark uses only fictional office code and aggregate dates. Raw command output and plan XML remain private until reviewed. Public documentation may contain sanitized query text, generic object and index names, aggregate row counts, logical reads, duration statistics, plan operators, and index size.

It must exclude endpoints, connection strings, tokens, client addresses, account, tenant, and subscription identifiers, administrator identities, tracing identifiers, and unrelated Query Store text.

## Controlled sequence

1. Implement a local read-only benchmark client with offline contract tests and a non-connecting dry run. **Complete.**
2. Implement sanitized metric parsing, exact result comparison, and plan-summary extraction. **Complete.**
3. Run compilation, offline tests, privacy scanning, and dry-run verification. **Complete.**
4. Run the Azure SQL baseline workload only after the local dry run and privacy review pass. **Complete.**
5. Measure and document the baseline without changing the database. **Complete.**
6. Review the baseline and decide whether the candidate index deserves a separate deployment step. **Complete.**
7. Deploy the candidate, repeat the identical experiment and regression suites, then keep or remove it strictly by the acceptance contract. **Complete: `KEEP`.**
