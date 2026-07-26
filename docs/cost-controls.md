# Cost Controls

## Budget policy

| Control | Value |
|---|---|
| Routine target | €0 per month |
| Alert threshold | €2 |
| Personal ceiling | €5 per month unless explicitly reconsidered |
| Intended Azure region | West Europe |
| SQL configuration | Azure SQL Database free offer |
| Free-limit behavior | Pause until the next month |

## Implemented budget

| Setting | Verified value |
|---|---|
| Scope | `rg-azure-sql-office-attendance-lab-weu` |
| Name | `budget-office-attendance-lab` |
| Reset period | Monthly |
| Amount | €5 |
| Alert 1 | Enabled at 40% (€2 actual cost) |
| Alert 2 | Enabled at 100% (€5 actual cost) |
| Active period | 2026-07-01 through 2028-06-30 |

The notification recipient is deliberately excluded from public documentation. The portal was used to select actual-cost alerts. The preview Azure CLI representation returned `thresholdType` as null, while correctly returning both enabled thresholds; this is treated as a representation limitation rather than a missing configuration.

## Required controls

- Confirm the portal cost summary before creating a resource.
- Use a dedicated resource group so that cost and cleanup are unambiguous.
- Apply project and environment tags where supported.
- Review Cost Management after every phase.
- Avoid verbose diagnostic ingestion into Log Analytics.
- Keep audit-log storage and retention deliberately small.
- Do not enable Defender trials without recording their end dates and future billing behavior.
- Delete restored databases immediately after recovery verification.
- Record every resource in a cleanup checklist.

## Important limitation

An Azure budget sends notifications; it is not a hard spending cap. The primary safeguards are free-tier configuration, pause behavior, resource isolation, deliberate provisioning, and cleanup verification.

## Planned resources

| Resource | Expected billing posture | Created |
|---|---|---|
| Resource group | No direct charge | Yes — portal-created and CLI-verified |
| Cost Management budget | No direct charge | Yes — portal-created and CLI-verified |
| Azure SQL logical server | No separate compute charge | Yes — portal-created and CLI-verified |
| Free-offer Azure SQL Database | €0 within monthly limits | Yes — free limit enabled, overage disabled, and auto-pause verified |
| Audit storage account | Small usage-based storage cost | Yes — hardened and verified during monitoring setup |
| Temporary PITR target | Potential short-lived charge | No |

The recovery exercise proposes one Basic point-in-time restore target. It is not covered by the source
database's free offer and becomes normally billable when restore completes. The design
therefore requires deletion within 60 minutes after the target becomes online, verified
absence, a final one-user-database inventory, and a post-cleanup cost review. No higher
service objective may be selected without a new cost decision.

## Cleanup verification

At the end of a phase that creates temporary resources:

1. List all resources in the project resource group.
2. Remove temporary restore or test resources.
3. Confirm their deletion has completed.
4. Review current cost and forecast.
5. Record the result in project documentation.
