# Documentation Guide

Start with the repository [README](../README.md). It is the public case study.

Use these notes when you want more detail behind a specific part of the lab:

| File | Use it for |
|---|---|
| [verification-summary.md](verification-summary.md) | Public summary of the checks that passed |
| [architecture.md](architecture.md) | System shape, trust boundaries, and data flow |
| [schema-design.md](schema-design.md) | Table responsibilities and relational model |
| [loading-contract.md](loading-contract.md) | Batch loading, validation, rejection handling, and rerun behavior |
| [security-design.md](security-design.md) | Loader and reporter roles, permissions, and negative tests |
| [reporting-design.md](reporting-design.md) | Aggregate reporting views and Tableau-facing grain |
| [performance-design.md](performance-design.md) | Baseline measurement, retained index, and trade-off |
| [monitoring-auditing-design.md](monitoring-auditing-design.md) | Audit, alerting, metrics, and cost-aware monitoring choices |
| [backup-recovery-design.md](backup-recovery-design.md) | Point-in-time restore design and cleanup path |
| [tableau-dashboard-design.md](tableau-dashboard-design.md) | Tableau Public dashboard boundary and aggregate extracts |
| [troubleshooting.md](troubleshooting.md) | Serverless resume, firewall, deployment, and Tableau driver issues |

The supporting docs are intentionally more technical than the README. They are appendices for someone who wants to inspect the design, not a second version of the same case study.
