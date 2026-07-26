# Troubleshooting Runbook

## Serverless resume followed by firewall rejection

### Symptom sequence

1. `sqlcmd` reported that the target database was not currently available.
2. Azure CLI subsequently reported database status `Online`.
3. The next connection reached the logical server but was rejected because the current client IP was not permitted.

### Diagnosis

The incident contained two independent conditions:

- The General Purpose serverless database had auto-paused after inactivity. The first data-plane connection triggered resume but returned the expected temporary-unavailability error before the database was ready.
- The workstation's public IP had changed since the original single-address firewall rule was created. Control-plane queries still worked because they use Azure Resource Manager rather than the SQL data endpoint.

### Resolution

1. Query database status through Azure CLI without exposing account identifiers.
2. Wait for status `Online`, then test with a harmless database-context query.
3. In the SQL logical server's Networking page, add the automatically detected current client IPv4 address.
4. Remove the stale client rule so only one single-address rule remains.
5. Keep broad Azure-service access disabled and save.
6. Verify only the firewall-rule count and names through Azure CLI; do not retain address values.
7. Retry the harmless connection before rerunning a deployment script.

### Safety and partial-execution assessment

Both errors occurred during login, before a database session was established. Therefore, the deployment script could not have partially executed. When a failure occurs after login, inspect the script's transaction and the resulting database state instead of making this assumption.

### Prevention and operational lesson

- Expect the first connection to a paused serverless database to require retry logic.
- Treat residential public IP rules as temporary operational configuration.
- Diagnose control-plane state, network access, authentication, and SQL execution as separate layers.
- Never solve a single-client firewall problem by enabling access for all Azure services or by adding an unnecessarily broad address range.
- Keep raw errors and screenshots private when they contain IP addresses, server names, account identifiers, or tracing IDs.

### Confirmed recurrence during fact-table deployment

The temporary-unavailability condition recurred before the core fact-table deployment. Azure Resource Manager reported the database as `Online`, but immediate SQL logins still failed. A harmless database-context query later returned `ONLINE`, after which the transactional deployment and verification succeeded. This confirms that control-plane status can precede full data-plane readiness and that a successful data-plane probe is the correct deployment gate after serverless resume.

### Confirmed recurrence before reference-loader deployment

The same two-layer sequence occurred before the reference-loader deployment: an initial connection triggered serverless resume, and the following connection exposed a changed residential client address. The stale single-address rule was replaced through the portal with Azure's detected current address; broad Azure-service access remained disabled. A privacy-safe CLI check confirmed exactly one firewall rule, and a harmless data-plane query returned `ONLINE` before deployment. The procedure and rollback-protected test then completed successfully, proving that the access correction did not require weakening the network boundary.

### Diagnostic separation before monthly-loader deployment

Before the monthly-loader deployment, repeated target-database connections returned the transient unavailable error even while Azure Resource Manager reported `Online`, free-limit use enabled, and free-limit exhaustion behavior set to `AutoPause`. A Microsoft Entra connection to the logical server's `master` database succeeded, proving that authentication, the client firewall rule, and logical-server routing were healthy. The next target-database probe returned `ONLINE`; deployment and rollback-protected tests then passed.

This adds a useful diagnostic separator: when control-plane state says `Online` but the target remains unavailable, test `master` before changing configuration. A successful server-level connection narrows the remaining issue to target-database readiness.

### Loader-client probe confirmation

The guarded Python client's first harmless probe reproduced the established serverless pattern without exposing private connection details. Complete offline verification passed, the logical server and Microsoft Entra path were reachable through a successful `master` diagnostic, and the target was correctly classified as not yet data-plane ready. A privacy-safe control-plane query reported `Resuming`, then `Online`. The next unchanged probe returned data-plane readiness `PASS` and explicitly confirmed that no database data changed.

This verifies the client-side diagnostic boundary before the canonical load: authentication, selected-network access, TLS, logical-server routing, target selection, token handling, and the harmless database-context query work together. It also confirms that `Online` should be followed by a successful target query rather than treated as sufficient confirmation by itself.

## Verification-query warning

An initial schema verification query used `COUNT` over the nullable side of a `LEFT JOIN`, producing a harmless null-elimination warning. Replacing it with `SUM(CASE WHEN object_id IS NULL THEN 0 ELSE 1 END)` preserved the intended count and removed the warning. The warning came from the diagnostic query, not from schema deployment.

## Alert-test safeguards

The portal action-group test picker opened but returned no selectable sample type. The action group itself was not weakened or recreated; the supported test-notification interface accepted the same private common-schema receiver instead. Delivery remains a private recipient confirmation, not an inference from the management request.

The existing clean-baseline benchmark also refused to run because the retained performance candidate index is intentionally retained. Removing the index would have invalidated established state, and using candidate mode would have introduced write-path work. A bounded read-only alert-workload mode was added instead. It reuses the frozen reporting query and independent oracle, accepts the retained index, and makes no schema or data change.

Alerts Management showed the temporary rule fired, then lagged after six consecutive zero CPU samples. The metric-rule status endpoint returned `Healthy` for both instances and provided the authoritative resolution confirmation. The temporary rule was deleted only after that status was verified.

## Tableau Azure SQL JDBC authentication dependencies

Tableau Desktop Free Edition on macOS uses its Java connector for Azure SQL; the existing
Microsoft ODBC Driver 18 used by the Python loader does not satisfy that connector. The first
Tableau attempt reported no suitable JDBC driver. Installing the checksum-verified Microsoft
JDBC Driver 13.4.0 JRE11 in Tableau's per-user driver directory moved the failure boundary to
authentication, where Tableau then reported that MSAL4J was unavailable.

The Microsoft JDBC 13.4.0 manifest requires MSAL4J in the compatible 1.23 range, and its
pinned Azure Identity 1.18.2 metadata resolves to MSAL4J 1.23.1. That MSAL4J artifact has two
runtime dependencies for this path: Azure JSON 1.4.0 and SLF4J API 1.7.36. All three JARs were
downloaded from Maven Central, checked against published checksum sidecars, archive-tested,
installed per-user, and byte-compared. After a complete Tableau restart, the unchanged
service-principal connection reached the Data Source page.

Every failed live identity attempt was deleted before dependency troubleshooting continued,
and the successful identity was removed immediately after proof. The transferable lesson is
to diagnose connector presence, Java authentication libraries, Entra propagation, Azure SQL
permissions, and server readiness as separate layers. Do not broaden Azure permissions or
rotate credentials to repair a local Java classpath failure.
