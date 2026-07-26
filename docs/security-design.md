# Identity and Security Design

## Status

The security boundary was completed on 2026-07-17. The controlled batch-result procedure, least-privilege client path, and durable `app_loader` and `report_reader` roles are deployed and verified. The roles contain no members, and no login, external user, password, application secret, or persistent test principal was created.

Microsoft Entra remains the authentication authority. The database remains passwordless from the client perspective: the existing administrator and any later external principal authenticate with short-lived Entra tokens. Database roles define authorization independently from those identities.

## Security objectives

- Keep the Microsoft Entra administrator available for deployment, identity administration, controlled verification, and recovery rather than routine loading or reporting.
- Permit the loading client to execute only the interfaces required for monthly source processing and daily-summary refresh.
- Prevent the loading identity from reading or modifying `stage`, `core`, or `report` objects directly.
- Permit the reporting identity to read only approved privacy-safe objects in `report`.
- Keep reference bootstrap, device assignment, independent reconciliation, object deployment, and security administration outside the application-loader boundary.
- Prove both allowed and rejected behavior without storing a password, token, endpoint, connection string, tenant identifier, subscription identifier, or account identifier.

## Durable role model

| Principal or role | Intended use | Positive permissions | Explicit boundary |
|---|---|---|---|
| Microsoft Entra administrator | Deployment, identity administration, controlled verification, recovery | Existing database-owner authority | Not used by an application or Tableau; not added to either custom role |
| `app_loader` database role | Routine source-batch transport | `EXECUTE` on the four batch lifecycle procedures, a new read-only batch-result procedure, and the daily-summary refresh procedure | No direct `SELECT`, `INSERT`, `UPDATE`, or `DELETE` on `stage`, `core`, or `report`; no reference bootstrap, assignment, reporting, DDL, role, or grant authority |
| `report_reader` database role | Tableau and other approved read-only analysis | `SELECT` on the `report` schema; object-level `EXECUTE` only for any reporting procedure reviewed for reporting use | No access to `stage` or `core`; no DML, DDL, loader procedure, refresh, bootstrap, assignment, role, or grant authority |

All three schemas are owned by `dbo`. The controlled procedures use static SQL, so the common ownership chain permits their internal table access without granting the caller direct table permissions. Neither custom role receives `ALTER` on a schema because that permission could let a principal create an object that takes advantage of the shared owner and bypasses the intended boundary.

The `report` schema is the publication boundary. A schema-level `SELECT` grant means every object placed there must already be reviewed as privacy-safe. Reporting stored procedures are not granted schema-wide execution; each one must be granted explicitly after its result contract is reviewed.

## Procedure allocation

| Procedure | Administrator | `app_loader` | `report_reader` | Reason |
|---|---:|---:|---:|---|
| `core.usp_BootstrapReferenceData` | Yes | No | No | Reference creation is a controlled administrative operation, not routine monthly ingestion |
| `core.usp_AssignDevice` | Yes | No | No | Changes a governed identity relationship |
| `stage.usp_BeginImportBatch` | Yes | Yes | No | Required to register, recover, or recognize a source batch |
| `stage.usp_AppendImportChunk` | Yes | Yes | No | Required to transport bounded JSON chunks |
| `stage.usp_FinalizeImportBatch` | Yes | Yes | No | Required to validate and atomically finalize one source file |
| `stage.usp_FailImportBatch` | Yes | Yes | No | Required to leave a reconciled failure state after a client-side failure |
| `stage.usp_GetImportBatchResult` | Yes | Yes | No | Returns only the checksum-scoped persisted result for one batch so idempotency does not require direct table access; deployed, rerun, and behaviorally verified |
| `core.usp_RefreshDailyAttendanceSummary` | Yes | Yes | No | Required after all expected source batches reconcile |
| Reporting procedures | Yes | No | Individually approved | Results must be reviewed before each object-level grant |

## Existing-client gap

The initial loading client is safe for the approved administrative load, but it is not yet a least-privilege runtime client. Its already-processed path directly selects one row from `stage.ImportBatch`. Granting table access to preserve that lookup would weaken the role boundary.

The security implementation adds `stage.usp_GetImportBatchResult`, scopes its lookup by both batch identifier and content checksum, and replaces the direct query in `loader/load_data.py` with that controlled call. Live verification passed the exact-result contract, three expected rejections, transaction rollback, fixture cleanup, and unchanged redeployment. The existing independent verifier remains an administrative verification tool because its purpose requires broad read access to canonical reference, staging, error, fact, and summary data.

The client also bootstraps reference data on a full canonical invocation. That operation remains administrator-only. The existing `--execute-load` command is therefore an administrative deployment/recovery path. The locally implemented `--execute-source-load` path processes only source batches and refreshes the daily summary; it does not call reference bootstrap or the independent verifier.

## Identity binding decision

The security implementation established and tested the durable database roles before any real external workload identity was bound. Permission tests created database users `WITHOUT LOGIN` inside a transaction, added them to one role, executed positive and negative checks with `EXECUTE AS USER`, then rolled back. This proved effective database authorization without creating a password, application secret, unused Entra identity, or permanent test principal.

The existing Entra administrator token already proves the authentication path. A real Entra group, user, managed identity, or service principal will be bound only when a corresponding consumer exists and can be tested end to end. The Tableau connection test is the natural point to bind the reporting principal. A future hosted loader should prefer a managed identity; the current local lab must not invent a persistent application credential merely to simulate one.

## Permission verification contract

The security test must be rerunnable and leave no users, batches, views, rows, or other fixtures. It should run as the Entra administrator, create the no-login test users and any probe object inside an outer transaction, use `TRY/CATCH` with guaranteed `REVERT`, and roll back at the end.

Expected positive results:

- the administrator can inspect and manage the database security model;
- `app_loader` has `EXECUTE` on exactly the reviewed batch-result, lifecycle, and refresh procedures;
- `app_loader` can obtain a controlled result for one known synthetic batch without selecting the table;
- `report_reader` can select a transaction-scoped updatable view in `report` through ownership chaining while a write through the same otherwise-valid view is denied; and
- role and permission catalog queries match the declared matrix.

Expected negative results:

- `app_loader` cannot directly select from or modify any `stage`, `core`, or `report` table or view;
- `app_loader` cannot execute reference bootstrap, device assignment, or reporting procedures;
- `report_reader` cannot select from `stage` or `core` or execute loader, bootstrap, assignment, or refresh procedures;
- neither custom role can insert, update, delete, create, alter, drop, grant, deny, add role members, impersonate another user, or view unapproved object definitions; and
- all expected denials are caught and counted, unexpected success fails the suite, the outer transaction rolls back, and a final administrator check confirms fixture cleanup.

The database administrator is deliberately not subject to a negative data-access test: database-owner authority bypasses ordinary denies. Its boundary is separation of duties and non-routine use, verified by confirming that no application or reporting workflow uses the administrator identity.

## Implementation sequence after approval

1. Add and locally review the controlled batch-result procedure and its rollback-protected behavior test.
2. Replace the loader's direct `stage.ImportBatch` lookup and add offline tests proving the direct query is gone.
3. Add rerunnable role-and-permission deployment SQL.
4. Add the rollback-protected positive and negative permission suite.
5. Use the harmless data-plane probe as the deployment gate; diagnose readiness, firewall, authentication, and routing separately.
6. Deploy the additive SQL, run the permission suite, verify cleanup, and capture only sanitized counts and outcomes.
7. Update the sanitized project documentation after verification.

## Completed implementation status

The controlled interface prerequisite and `sql/011_create_security_roles.sql` are deployed and verified. The loader receives exactly six object-level procedure grants and explicit direct-data, schema-alteration, and definition denials across `stage`, `core`, and `report`. The reporter receives schema-level `SELECT` only on `report`, explicit write, alteration, and definition denials there, and complete staging and core denials. The script creates no login, user, password, secret, or external identity.

`tests/012_verify_security_roles.sql` verifies the exact permission catalog, administrator separation, loader use of the controlled batch-result interface, reporter selection through an updatable transaction-scoped reporting view, direct staging isolation, report-write rejection, six loader denials, six reporter denials, DDL permission rejection, guaranteed `REVERT`, transaction rollback, and independent fixture cleanup.

The live suite passed twice, including after an unchanged role redeployment: administrator control `PASS`, loader positive behavior `PASS`, six loader denials, reporter positive behavior `PASS`, six reporter denials, transaction rollback `PASS`, and fixture cleanup `PASS`. A separate audit found zero test users, views, batches, and memberships. The fresh independent verifier reproduced all canonical totals with zero unreconciled batches, pending rows, or unexpected fixtures.

Two focused test-harness corrections were required before the final passes. An attempted unauthorized DDL statement could make the enclosing transaction uncommittable, so DDL denial is proved with effective-permission inspection instead. A constant-only view rejected writes for structural reasons before authorization was evaluated, so the write-denial test now uses an otherwise-updatable view. Both failed sessions were followed by independent zero-fixture audits before retrying.

External identity binding remains deliberately deferred until a real consumer can be tested end to end. That is a future approval boundary, not unfinished role verification.
