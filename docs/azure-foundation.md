# Azure Foundation

## Deployment summary

The Azure SQL foundation was created through the Azure portal on 2026-07-14 and independently verified with privacy-safe Azure CLI queries.

## Verified configuration

| Area | Verified state |
|---|---|
| Resource boundary | Dedicated West Europe resource group |
| Database | `sqldb-office-attendance-lab`, Online |
| Service tier | General Purpose serverless, `GP_S_Gen5_2` |
| Compute | 2 vCores, minimum capacity 0.5 vCores |
| Storage | 32 GiB maximum |
| Free offer | Enabled |
| Free-limit behavior | Auto-pause until the next monthly allowance |
| Inactivity auto-pause | 60 minutes |
| Zone redundancy | Disabled |
| Backup redundancy | Locally redundant |
| Network access | Public endpoint enabled |
| Firewall | One single-client rule; address excluded from public files |
| Broad Azure-service access | Disabled |
| Minimum TLS | 1.2 |
| Authentication | Microsoft Entra and SQL authentication enabled |
| Microsoft Entra administrator | Configured; identity excluded from public files |
| Encryption at rest | Transparent Data Encryption with service-managed key |
| Microsoft Defender for SQL | Not enabled; avoids post-trial billing |
| Ledger and secure enclaves | Not enabled |
| Data source | Blank database |
| Tags | Project, environment, data classification, owner, and cost-control tags applied to server and database |

## Administrative reasoning

- The free offer and automatic free-limit pause protect the monthly zero-cost target more directly than the budget alone.
- A public endpoint is required for administration from the local workstation and later Tableau Desktop development. Access is constrained to the current client IP instead of allowing all Azure services.
- A private endpoint was excluded because it would add networking complexity and possible cost without improving the learning outcome of this small personal lab.
- Both authentication methods are enabled: Microsoft Entra is preferred for human administration, while contained SQL identities can support controlled loader and reporting scenarios. The server administrator will not be used as a routine application identity.
- Locally redundant backups are an accepted free-offer trade-off. Point-in-time recovery will be tested later, but regional-outage recovery is outside version 1 scope.
- Defender for SQL was deliberately declined because the 30-day trial converts to a paid server-level feature. Native monitoring, auditing, vulnerability review, Query Store, and least-privilege tests will still be demonstrated separately.

## Verification summary

Privacy-safe CLI output confirmed:

- Database status `Online`
- General Purpose serverless objective `GP_S_Gen5_2`
- 32 GiB maximum size, 0.5 minimum capacity, and 60-minute auto-pause
- Free-offer use enabled with `AutoPause` exhaustion behavior
- Logical server state `Ready`, public network access enabled, and TLS 1.2
- Exactly one firewall rule without retaining its IP address
- Exactly one Microsoft Entra administrator without retaining the identity
- All five governance tags on both the server and database

Raw portal screenshots are private because they expose account, subscription, server, or network identifiers. Public files must be cropped or recreated from sanitized command output.

## Connectivity result

Connectivity was verified with the Go-based `sqlcmd` using Microsoft Entra authentication. The connection reached `sqldb-office-attendance-lab`, and a harmless query returned database status `ONLINE`. No identity, password, token, connection string, or firewall address was retained.

This confirms that the logical server endpoint, current-client firewall rule, TLS path, Microsoft Entra administrator, database targeting, and serverless resume path work together. The foundation setup is complete.

Cost and forecast will continue to be reviewed after each phase because Azure usage data can be delayed.
