# Monitoring Verifier

This directory freezes the reviewed storage, database-audit, role, destination, and durable-alert contracts in executable Python checks.

The default is entirely offline and does not invoke Azure CLI or Azure SQL:

```bash
.venv/bin/python monitoring/verify_monitoring.py
```

Live verification is always explicit and read-only. A bounded implementation stage can be selected while monitoring is being implemented:

```bash
.venv/bin/python monitoring/verify_monitoring.py --execute-live --checkpoint foundation
.venv/bin/python monitoring/verify_monitoring.py --execute-live --checkpoint audit
.venv/bin/python monitoring/verify_monitoring.py --execute-live --checkpoint complete
```

The complete live verifier discovers the tagged lab resource group through the existing
Azure CLI session and verifies the audit foundation, zero Log Analytics workspaces,
zero Event Hub namespaces, one enabled common-schema email action, zero other
receivers, one exact durable metric alert, and zero temporary alerts. Runtime names and
identifiers remain in memory; output contains only generic statuses and counts. It never
requests or displays storage keys, access tokens, endpoints, identities, connection
strings, client addresses, email addresses, or raw provider errors.

The controlled audit workload reuses the existing rollback-protected `tests/012_verify_security_roles.sql` suite. Canonical data and reporting regressions continue to use the independent load and reporting verifiers. Raw audit blobs and notification payloads remain private and outside the public repository.

The controlled activity runner is also offline by default:

```bash
.venv/bin/python monitoring/generate_audit_activity.py
```

Its explicit live mode first passes the SQL connectivity gate, runs the unchanged rollback-protected security suite, proves fixture cleanup, and then runs fresh canonical and aggregate-report reconciliations:

```bash
.venv/bin/python monitoring/generate_audit_activity.py --execute
```

`verify_audit_delivery.py` reads the private audit files through Azure SQL's audit-file function and performs its filtering inside the database. It returns only five generic category counts; raw records, statements, identities, addresses, and storage paths never leave the query:

```bash
.venv/bin/python monitoring/verify_audit_delivery.py
.venv/bin/python monitoring/verify_audit_delivery.py --execute
```
