# Backup and Point-in-Time Recovery Design

## Status

This recovery design was prepared on 2026-07-18 from read-only Azure inspection. No
restore database, recovery marker, backup policy, firewall rule, identity, or database
row was created or changed.

The source database is General Purpose serverless with locally redundant backups. Its
short-term retention is seven days, differential-backup frequency is twelve hours, a
valid point-in-time restore window exists, and no weekly, monthly, or yearly long-term
retention policy is enabled. Exactly one user database exists on the logical server. The
source was paused during inspection, and its maximum observed data-storage metric was
approximately 110 MiB.

Azure SQL Database manages full, differential, and transaction-log backups
automatically. A point-in-time restore creates a new database on the same logical server;
it never overwrites the source. The restored database is billed normally after restore
completion, even though the source uses the free offer. See the official
[automated-backup overview](https://learn.microsoft.com/azure/azure-sql/database/automated-backups-overview),
[backup recovery guidance](https://learn.microsoft.com/azure/azure-sql/database/recovery-using-backups),
and [serverless billing behavior](https://learn.microsoft.com/azure/azure-sql/database/serverless-tier-overview).

## Objective

Prove that the synthetic canonical database can be restored to an intentional point
before a controlled committed marker, independently reconciled, and removed without
changing or replacing the source database.

This is a user-error recovery exercise. It is not a regional-disaster, geo-restore,
failover, long-term-retention, server-deletion, or production cutover test.

## Selected design

| Area | Decision |
|---|---|
| Source | The one verified canonical Azure SQL database; never renamed or deleted |
| Restore mode | Point-in-time restore to a new database on the same logical server |
| Restore point | UTC restore point captured immediately before a committed fictional marker |
| Temporary target | One generically named database tagged `phase=8` and `lifecycle=temporary` |
| Target objective | Basic, locally redundant, non-zone-redundant |
| Target size rationale | Observed source use is far below Basic's 2 GiB limit |
| Network | Reuse the logical server's existing single-client firewall boundary |
| Authentication | Existing short-lived Microsoft Entra administrator session only |
| Verification | Independent canonical, reporting, structure, marker, role, and fixture checks |
| Cleanup | Delete target first, then remove the source marker and verify final inventory |
| Evidence | Generic states, counts, elapsed times, and cost only |

Basic is selected only for the temporary validation target. Azure permits a restore
request to specify a different service tier or compute size, and Basic is available in
the lab region. If Azure rejects that objective, stop and reassess cost before choosing
another tier; do not silently fall back to General Purpose.

## Why a committed marker is required

Restoring the latest available copy and comparing row totals would prove copy integrity,
but not that the requested timestamp was honored. The recovery exercise therefore uses one additive,
fictional marker created after the chosen restore point:

1. Pass the source data-plane readiness gate and fresh canonical/reporting verification.
2. Capture a second-precision UTC restore point from Azure SQL.
3. Wait at least five seconds so the marker transaction cannot share that timestamp.
4. In one committed transaction, create `dbo.Phase8RecoveryMarker` and insert one fixed
   synthetic marker row.
5. Verify the marker exists on the source.
6. Allow at least one transaction-log-backup interval before submitting the restore.

The restored database must contain every canonical object and row expected at the
restore point while the marker table is absent. The source must still contain the marker
until final cleanup. The marker contains no identity, endpoint, time-derived secret, or
real-world information.

## Recovery objectives

- **Exercise RPO:** only the deliberate post-restore-point marker is excluded. Canonical
  data, reporting objects, roles, and the retained performance index must match the source at
  the restore point.
- **Platform observation:** transaction-log backups are generally taken approximately
  every ten minutes, but exact scheduling is Azure-managed and is not an application
  guarantee.
- **Exercise RTO target:** restore completes within 60 minutes of submission; verification
  and deletion complete within 60 minutes after the target becomes online.
- If either limit is exceeded, cancel or delete the temporary target and record an
  incomplete exercise. Extending the billed window requires new approval.

## Preflight and cost gates

Before any write or restore:

1. Confirm one source user database and zero temporary restore databases.
2. Confirm source status, service objective, local backup redundancy, seven-day
   short-term retention, twelve-hour differential frequency, no LTR, and a valid restore
   window.
3. Confirm the source footprint remains below the target objective's limit.
4. Confirm current resource-group actual cost and forecast remain below the EUR 5 ceiling.
5. Pass fresh canonical, reporting, monitoring, zero-fixture, and empty-role checks.
6. Freeze the expected source inventory and generator-derived totals before creating the
   marker.

The restore target does not receive the source free-offer allowance. Basic is provisioned
compute and remains billable until deletion. The target must be deleted immediately after
verification, not left to auto-pause. Cost data can lag, so deletion proof is the primary
control and the final cost query is supporting confirmation.

## Portal-first restore sequence

1. Open the source database's **Restore** flow in the Azure portal.
2. Choose point-in-time restore and enter the captured pre-marker UTC restore point.
3. Keep the same logical server and region.
4. Select Basic, local backup redundancy, and no zone redundancy.
5. Apply only the approved project, phase, and temporary-lifecycle tags.
6. Review the estimated configuration and create exactly one target.
7. Use privacy-safe CLI queries to monitor operation state and elapsed time; do not retain
   deployment IDs or raw provider messages.
8. Treat a portal `Online` state as control-plane confirmation only. Pass the harmless SQL
   data-plane readiness probe before verification.

No BACPAC, storage credential, manual backup file, copy operation, extra server, new
firewall rule, or persistent identity is involved.

## Restored-database acceptance contract

The temporary target passes only when all of the following are true:

1. The data-plane probe succeeds through Microsoft Entra and ODBC Driver 18.
2. All six reference totals match the generator oracle.
3. All 24 batches, accepted/rejected totals, validation codes, signal totals, and 37,151
   person-days match the canonical oracle.
4. The 261 daily report rows, 2,087 department rows, load-quality rows, and validation
   rows match the reporting oracle.
5. `dbo.Phase8RecoveryMarker` is absent on the target and present on the source.
6. The retained performance candidate index exists with its exact key definition.
7. The four approved report views exist; `app_loader` and `report_reader` exist with zero
   members; no test fixtures exist.
8. Transparent Data Encryption is enabled with the expected service-managed posture.
9. The source database, its monitoring/audit configuration, and its canonical data remain
   unchanged.

A restored database's resource-level audit setting must be inspected rather than assumed
to match the source. If database-level auditing is present on the temporary target, disable
it before data-plane verification to avoid unnecessary raw-log retention; do not change
the source audit policy.

## Cleanup contract

Cleanup is part of the test, not a later housekeeping task:

1. Close every target connection.
2. Delete the temporary database immediately and wait until it is absent.
3. Verify the server again contains exactly one user database and zero temporary targets.
4. Remove the recovery marker from the source in a guarded transaction and verify absence.
5. Rerun fresh canonical, reporting, monitoring, empty-role, and zero-fixture verification
   against the source.
6. Review current cost and forecast, acknowledging billing-data delay.
7. Record generic restore duration, verification result, deletion duration, and final
   resource counts.

Azure can retain a deleted database's automated backups until its short-term retention
expires. That expected service-managed backup retention is not a surviving compute
resource and cannot be treated as deletion failure. No LTR policy will be created.

## Failure recovery

- If marker creation fails, roll it back or remove it and do not submit a restore.
- If restore submission fails, verify that no target exists. Delete any partial target
  before diagnosing the request.
- If the target does not become ready within the restore RTO, cancel or delete it and stop.
- If any data or structure comparison fails, capture only the failed generic check, delete
  the target, clean the source marker, and diagnose offline. Do not retain a billed target
  for convenience.
- If target deletion does not complete, treat it as a cost-control incident and do not
  continue to another phase.
- The source database is never deleted, renamed, replaced, scaled, or modified with data
  recovered from the target during this exercise.

## Privacy boundary

Database backups and BACPACs are never downloaded or committed. Public files exclude
restore deployment IDs, subscription or tenant values, server endpoints, client addresses,
administrator identities, connection strings, access tokens, portal screenshots, raw
errors, and target connection metadata. The source and restored data are entirely
fictional, but the same operational privacy boundary still applies.

## Exercise outcome

The recovery exercise is complete. The first approved live attempt was safely cleaned up but did not prove
recovery because the portal submitted its later default minute instead of the captured
pre-marker second. The independent verifier correctly rejected the target when the marker
was present, and the focused correction added a mandatory private exact-second comparator.

The corrected attempt reran every gate, created a new marker, waited a
normal log-backup interval, and refused the first Review value because it still did not
match. The portal time input displayed the intended value but did not update its submitted
state until the field was explicitly committed with Enter. Only after the Review value
returned `ExactSecondMatch=1` was one Basic restore submitted.

The temporary target became control-plane Online within the 60-minute restore objective.
Its Basic 2 GB, local, non-zone-redundant, tagged, TDE-enabled configuration passed. Target
database auditing was found enabled and was disabled on the target only; the source audit
remained enabled. After the readiness gate, the independent oracle proved the marker absent
on the target and present on the source and reconciled all canonical, reporting, structure,
role, index, and fixture checks. The target was then deleted and proved absent before the
guarded source-marker cleanup. Final source, monitoring, inventory, cost, compilation,
dry-run, privacy, and 49-test regressions passed.
