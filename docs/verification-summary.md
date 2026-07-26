# Verification Summary

This page answers a simple question: what was actually checked?

The tables below summarize the load, schema, security, reporting, performance, monitoring, auditing, recovery, and offline regression checks behind the case study. They are intentionally compact so a reviewer can see the results without reading every implementation note.

## Data generation and loading

The completed load used one deterministic fictional dataset:

| Check | Result |
|---|---:|
| Source period | 12 months |
| Monthly source files | 24 |
| Received source rows | 134,372 |
| Accepted rows | 133,892 |
| Controlled rejected rows | 480 |
| Reconciled aggregate person-days | 37,151 |
| CARD-only person-days | 1,236 |
| WIFI-only person-days | 11,082 |
| BOTH person-days | 24,833 |
| Failed, open, or unreconciled batches | 0 |

The loader was tested for repeatability. A second unchanged run detected already-processed content instead of appending duplicate rows.

## Schema and data-quality checks

The schema uses separate `stage`, `core`, and `report` boundaries. The behavior tests covered representative constraints across staging rows, reference data, normalized attendance signals, lineage, and daily summaries.

| Check | Result |
|---|---:|
| Schema behavior checks | 13 / 13 |
| SQL fixture cleanup | Passed |
| Invalid-row handling | Expected rejections only |
| Daily summary reconciliation | Matched independent oracle |

## Security boundary

The project separates the load path from the reporting path:

- the loader path executes controlled procedures instead of directly editing core reporting tables;
- the reporting path reads only aggregate `report` views;
- direct `stage` and `core` access is denied to the reporting role;
- permission tests include positive and negative cases and roll back their fixtures.

No password, persistent test user, long-lived application secret, connection string, tenant identifier, subscription identifier, or personal IP address is stored in the repository.

## Reporting output

The reporting layer exposes aggregate views only. It does not publish person-level attendance rows.

The public analytical outputs reconcile to:

| Output | Result |
|---|---:|
| Daily office rows | 261 |
| Department-day rows | 2,087 |
| Load-quality rows | 2 |
| Validation-code rows | 8 |
| Aggregate person-days | 37,151 |

## Performance result

The performance exercise measured the primary daily-attendance reporting query before and after one candidate index.

| Metric | Baseline | Retained index |
|---|---:|---:|
| Primary query logical reads | 169 | 60 |
| Full-history logical reads | 672 | 233 |
| Primary result rows | 65 | 65 |
| Full-history result rows | 261 | 261 |

The index was kept only after the query returned identical results, the plan used the candidate, and refresh/reporting regressions passed.

## Monitoring, auditing, and recovery

The operational checks covered:

- database-level auditing with raw audit records kept out of the repository;
- an alerting path with notification delivery checked outside the repository;
- a point-in-time restore exercise using a deliberate post-restore-point marker;
- cleanup of the temporary restore target and recovery marker after verification.

The public repository records the design and sanitized outcomes, not raw cloud output.

## Offline regression suite

The offline suite in this repository covers loader behavior, reporting reconciliation, performance parsing, monitoring contracts, recovery tooling, Tableau export/artifact checks, and temporary identity safety checks.

| Suite | Result |
|---|---:|
| Offline regression tests | 77 passed |

## What is intentionally not public

The following are excluded from this repository:

- raw Azure CLI output;
- portal screenshots;
- database endpoints;
- connection strings;
- credentials or tokens;
- tenant, subscription, account, or object identifiers;
- private IP addresses;
- raw audit files;
- Tableau workbook binaries or packaged extracts.
