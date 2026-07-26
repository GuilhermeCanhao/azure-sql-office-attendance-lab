# Resource Naming

## Proposed names

| Resource | Proposed name | Notes |
|---|---|---|
| Resource group | `rg-azure-sql-office-attendance-lab-weu` | Isolates cost, access, verification, and cleanup |
| SQL database | `sqldb-office-attendance-lab` | Human-readable and service-specific |
| SQL logical server | `sql-office-attendance-lab-[random]` | Requires a globally unique, non-identifying suffix |
| Audit storage account | `stofficeattend[random]` | Lowercase, globally unique, no punctuation |

## Suffix rule

Use a short random suffix generated for uniqueness. Do not derive it from:

- Name or initials
- Employer
- Email address
- Subscription or tenant ID
- Birth year or other personal information

## Proposed tags

| Tag | Value |
|---|---|
| `project` | `azure-sql-office-attendance-lab` |
| `environment` | `lab` |
| `data-classification` | `synthetic` |
| `owner` | A non-email personal label chosen during provisioning |
| `cost-control` | `free-offer` |

Tags support organization and cost review. They do not secure resources and must not contain secrets or personal identifiers.
