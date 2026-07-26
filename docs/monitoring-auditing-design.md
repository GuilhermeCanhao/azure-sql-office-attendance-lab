# Monitoring and Auditing Design

## Status

The audit and alert implementations, private sample/fired/resolved notification delivery, cleanup, cost review, and complete regressions were verified on 2026-07-18. The monitoring and auditing work is complete.

One same-region Standard GPv2 LRS hot storage account now enforces HTTPS, TLS 1.2, disabled public blob access, disabled shared-key authorization, disabled cross-tenant replication, a default-deny network posture, and trusted Azure-service bypass. Blob soft delete, versioning, and change feed are disabled, so SQL auditing's seven-day retention is not silently extended. The logical server has a system-assigned managed identity with one storage-scoped Blob Data Contributor assignment; the existing administrator has one storage-scoped Blob Data Reader assignment.

Database auditing is enabled through managed identity with no key supplied, seven-day retention, and exactly the six approved groups. Server auditing remains disabled and no Azure Monitor audit destination is enabled. The first management request returned a provider internal error while the new identity and RBAC propagated; the same documented request then succeeded through the current stable API without weakening storage security.

The unchanged rollback-protected security suite and a focused database-permission audit probe both passed with complete rollback and cleanup. Fresh canonical and reporting reconciliation passed. Private audit-file verification returned successful-authentication, principal-change, role-membership-change, object-change, and database-permission-change categories; no raw record was exported. The complete live monitoring contract passes. The final inventory contains one private common-schema email action group, one exact durable high-CPU rule, and zero temporary rules. The offline monitoring suite now passes 13 tests; 36 loader, reporting, performance, and monitoring tests pass in total.

Recent built-in metrics showed that custom ingestion is unnecessary for this lab. The controlled alert window produced positive CPU and physical-data-read samples, no positive log-write, storage, or deadlock samples, and two connection-failure samples aligned with the known serverless readiness attempts.

## Design objectives

- Demonstrate native resource metrics, an actionable alert, database audit configuration, controlled audit activity, retention, and privacy-safe verification.
- Preserve the routine-zero-cost preference and remain below the existing €5 monthly ceiling.
- Avoid passwords, storage keys in scripts, Log Analytics ingestion, Event Hubs, Defender trials, broad raw-log publication, and unnecessary telemetry resources.
- Keep portal-first administration while using scripts and read-only commands for exact verification.

## Architecture decision

| Capability | Selected design | Deliberately excluded |
|---|---|---|
| Metrics | Native Azure Monitor platform metrics with their existing retention | Log Analytics workspace and metric export |
| Audit scope | Database-level policy for the one lab database | Server-level policy that would also audit `master` and future databases |
| Audit destination | Same-region Standard GPv2, LRS, hot Blob storage | Event Hubs, premium storage, geo-redundancy, immutable retention, and multiple destinations |
| Audit authentication | Logical-server system-assigned managed identity with storage-scoped `Storage Blob Data Contributor` | Storage access keys in configuration or project files |
| Human audit reading | Existing administrator receives storage-scoped `Storage Blob Data Reader`; no new credential | Anonymous blob access or a public audit container |
| Audit retention | Seven days, nonzero from first enablement | Unlimited retention and raw log archives in GitHub |
| Alerting | One stateful sustained-high-CPU metric alert and one email action group | SMS, webhook, automation, dynamic thresholds, and log-search alerts |
| Alert proof | Portal action-group test plus one temporary low-threshold CPU test rule, deleted after fired/resolved confirmation | Deliberate deadlock, failed-password attempt, firewall failure, or harmful load test |

Azure SQL auditing supports Azure Storage, Log Analytics, and Event Hubs, and Microsoft recommends managed identity over storage access keys. Native platform metrics are retained for 93 days without requiring a Log Analytics workspace. See the official [Azure SQL auditing overview](https://learn.microsoft.com/azure/azure-sql/database/auditing-overview), [auditing setup guidance](https://learn.microsoft.com/azure/azure-sql/database/auditing-setup), [Azure Monitor metrics retention](https://learn.microsoft.com/azure/azure-monitor/metrics/data-platform-metrics), and [Azure SQL metrics and alerts](https://learn.microsoft.com/azure/azure-sql/database/monitoring-metrics-alerts).

## Storage security contract

The proposed storage account must use:

- West Europe, Standard general-purpose v2, locally redundant storage, and hot access tier;
- secure transfer required and minimum TLS 1.2;
- public blob access disabled;
- shared-key authorization disabled after managed-identity auditing and administrator data-reader access are verified;
- no hierarchical namespace, SFTP, NFS, versioning, change feed, static website, or public container;
- a selected-network posture with trusted Azure-service access for the audit writer;
- no private endpoint, because private networking is outside version 1;
- seven-day SQL auditing retention from the first enabled policy.

If portal log reading requires temporary workstation network access, add only the current client address and remove it immediately after private review. Never publish that address. Blob soft delete must be reviewed explicitly because it extends effective raw-log retention after SQL auditing deletes a blob; any enabled soft-delete interval must be included in the documented effective-retention calculation.

## Audit action contract

Do not accept the portal's verbose default blindly. `BATCH_COMPLETED_GROUP` can retain statement text for every query and stored procedure, increasing both privacy exposure and storage volume.

The proposed database policy uses only:

- `SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP`;
- `FAILED_DATABASE_AUTHENTICATION_GROUP`;
- `DATABASE_OBJECT_CHANGE_GROUP`;
- `DATABASE_PRINCIPAL_CHANGE_GROUP`;
- `DATABASE_ROLE_MEMBER_CHANGE_GROUP`;
- `DATABASE_PERMISSION_CHANGE_GROUP`.

The portal will configure the destination, managed-identity authentication, and seven-day retention. The exact custom action set will then be applied reproducibly through the supported Azure management interface and verified read-only. Server-level auditing must remain disabled to prevent duplicate records.

Raw audit records may contain the administrator identity, client address, statement text, resource identifiers, storage paths, session identifiers, and timestamps. They remain private. Public documentation is limited to policy state, retention, generic action-category counts, success/failure counts, controlled test labels, and confirmation that no raw log was committed.

## Metric and alert contract

The monitoring review will use built-in metrics including:

- `cpu_percent`;
- `physical_data_read_percent`;
- `log_write_percent`;
- `storage_percent`;
- `connection_failed` and `connection_failed_user_error`;
- `deadlock`;
- successful connections where available.

The durable alert is:

| Setting | Value |
|---|---|
| Scope | The one Azure SQL database |
| Signal | `cpu_percent` |
| Aggregation | Average |
| Operator | Greater than |
| Threshold | 80 percent |
| Window | 5 minutes |
| Evaluation frequency | 1 minute |
| Severity | 2 |
| State | Enabled and automatically resolved |
| Action | One private email receiver through a dedicated action group |

This alert represents sustained resource pressure rather than a brief serverless wake-up spike. The email address and notification payload are never committed.

## Controlled verification sequence

1. Reconfirm cost, resource group, audit-policy, diagnostic-setting, identity, storage, action-group, and alert starting state through the portal and privacy-safe read-only queries.
2. Create the storage account through the portal with the approved security and redundancy settings.
3. Enable the logical server's system-assigned managed identity and grant only storage-scoped audit-writer access.
4. Grant the existing administrator storage-scoped read-only Blob data access for private review; create no new credential.
5. Enable database-level auditing to storage with seven-day retention, then replace the verbose default action set with the approved narrow set.
6. Verify that server-level auditing remains disabled and that no Log Analytics or Event Hub destination exists.
7. Pass the harmless SQL data-plane readiness gate.
8. Run the rollback-protected security behavior suite once to produce controlled principal, role-membership, permission, and object-change events; then run fresh canonical and report-only verification.
9. Wait for audit delivery and confirm the expected generic event categories without exporting or publishing raw records.
10. Review native CPU, data I/O, log-write, storage, connection, and deadlock metrics for the controlled window.
11. Create the email action group with common alert schema, request a sample notification through the supported test interface, and confirm delivery privately.
12. Create a temporary stateful `cpu_percent > 0` test rule, run the bounded read-only oracle-verified reporting workload, observe fired and healthy/resolved states, and delete the test rule.
13. Create or verify the durable `cpu_percent > 80` rule and confirm that exactly one durable project metric alert remains.
14. Verify seven-day retention, role assignments, storage network controls, zero fixtures, empty application roles, Defender disabled, and current cost/forecast.
15. Publish only sanitized counts and configuration summaries.

## Acceptance contract

This work is complete only when all of the following pass:

1. Exactly one approved audit storage account exists with the intended region, SKU, redundancy, TLS, public-access, authorization, network, and retention posture.
2. The logical server has one system-assigned managed identity with only the required storage-scoped writer role for this purpose.
3. Database auditing is enabled, server auditing remains disabled, retention is seven days, and the six approved actions/groups are exact.
4. No Log Analytics workspace, Event Hub destination, Defender plan, or unrelated diagnostic sink is enabled.
5. Controlled rollback-protected activity produces the expected audit categories and no fixture remains.
6. Native metrics show the controlled workload without a new ingestion pipeline.
7. The private action-group test succeeds.
8. The temporary low-threshold alert fires, resolves, and is deleted; the final environment contains exactly one enabled sustained-high-CPU rule.
9. Fresh canonical and report-only verifiers reproduce all load and reporting totals.
10. Audit-log content, email addresses, resource IDs, identities, IP addresses, endpoints, storage paths, and notification payloads are absent from public files.
11. Cost and forecast remain within the €5 ceiling, and the ongoing storage and alert cost is reviewed rather than assumed to be free.

## Failure recovery and cleanup

- If audit writes fail, disable the database policy before changing storage security; diagnose identity, RBAC, network access, region, and retention separately.
- If controlled SQL verification fails, guarantee context reversion and rollback, run independent cleanup checks, and do not treat the session as a successful audit check.
- If the action-group test fails, correct the notification path before creating the durable alert.
- Delete the temporary alert even if firing or notification verification fails.
- If monitoring and auditing are abandoned, disable database auditing, remove audit-specific role assignments, remove the server identity if unused elsewhere, delete alert rules and the action group, then delete the storage account after confirming no later work depends on it.
- At final project cleanup, either retain the small monitoring configuration with an explicit cost decision or remove it in the same dependency-safe order.

## Completed boundary

The Azure implementation, private notification proof, temporary-rule cleanup, native-metric review, cost review, and complete regression verification are finished. The monitoring and auditing work closed without authorizing or adding Log Analytics, Event Hubs, Defender for SQL, private endpoints, additional recipients, longer retention, broader audit actions, or a higher cost ceiling.
