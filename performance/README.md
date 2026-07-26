# Performance Benchmark

This directory contains the offline-default benchmark and its independent workload oracle.

Default execution validates the frozen 90-day and full-history contracts without invoking Azure CLI or Azure SQL:

```bash
.venv/bin/python performance/benchmark_reporting.py
```

The explicit baseline mode runs the read-only Azure SQL measurement path:

```bash
.venv/bin/python performance/benchmark_reporting.py --execute-baseline
```

The explicit candidate mode creates only the candidate index, measures it, removes it automatically on failure or `NO_CHANGE`, and retains it only after every live and independent regression passes:

```bash
.venv/bin/python performance/benchmark_reporting.py --execute-candidate
```

The live mode uses the existing short-lived Microsoft Entra token helpers, queries the aggregate reporting view, reads only diagnostic catalogs, performs two warm-ups and ten measurements, verifies every result row, extracts sanitized statistics and plan facts, and leaves Query Store, caches, indexes, settings, permissions, identities, and data unchanged.

Raw plan XML, provider messages, runtime targets, and connection metadata are never printed or stored by the benchmark.

Monitoring can reuse the same frozen aggregate query and independent row oracle as a
bounded, read-only metric-alert workload. This mode deliberately accepts the retained
retained candidate index and does not claim to be a clean performance baseline:

```bash
.venv/bin/python performance/benchmark_reporting.py \
  --execute-alert-workload --alert-workload-iterations 500
```

The read-only baseline matched every frozen row and recorded 169 summary logical reads for each of ten primary measurements and 672 for full history. The plan used the existing clustered primary keys. Query Store remained `READ_WRITE` / `AUTO` but did not capture the short workload, so no setting was changed to force it.

The candidate passed the strict contract and is retained. Final post-regression reads were 60 primary and 233 full history, reductions of 64.50 and 65.33 percent. The index used 233 pages, appeared in the actual plan, and passed refresh, reporting, fresh canonical, and fresh report-only verification. See [Verification Summary](../docs/verification-summary.md).
