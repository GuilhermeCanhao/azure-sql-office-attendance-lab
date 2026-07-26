# Local Tooling

## Check performed

Read-only prerequisite check performed on 2026-07-14.

| Tool | Status | Observed version or location | Intended use |
|---|---|---|---|
| Git | Available | 2.50.1 (Apple Git) | Version control |
| Visual Studio Code | Available | `/usr/local/bin/code` | SQL, Python, and documentation editing |
| Python | Available | 3.9.6 | Synthetic-data generation and controlled loader runtime |
| Homebrew | Available | `/opt/homebrew/bin/brew` | Controlled installation of missing command-line tools |
| Tableau Desktop | Available | Apple silicon 2025.2 | Dashboard development |
| Azure CLI | Available | 2.88.0 at `/opt/homebrew/bin/az` | Azure resource inspection and repeatable administration |
| sqlcmd | Available | 1.10.0 at `/opt/homebrew/bin/sqlcmd` | Database connectivity and script execution |
| Microsoft ODBC Driver for SQL Server | Available | 18.6.2.1 | Encrypted Azure SQL connectivity from Python |
| pyodbc | Available in project virtual environment | 5.3.0 | Parameterized calls to the controlled loading procedures |

## Installation verification

Azure CLI and the modern Go-based `sqlcmd` were installed with Homebrew on 2026-07-14. Both executables resolved from `/opt/homebrew/bin`, and their version checks completed successfully.

Homebrew also displayed an unrelated warning that the existing `ngrok/ngrok` tap was untrusted. No trust exception was added because ngrok is outside this project's scope. The warning did not prevent either required tool from installing.

During the first `az group show` verification, Azure CLI 2.88.0 emitted a Python `SyntaxWarning` from its bundled deployment-stacks model. The command still completed successfully and returned the expected resource-group state and tags. This was recorded as a benign tool-dependency warning rather than treated as an Azure provisioning failure.

The Azure portal remains useful for guided inspection, while the command-line tools provide repeatable verification and stronger administration confirmation.

## Controlled-loader prerequisite check

The loader-specific prerequisite check completed successfully on 2026-07-15:

- Microsoft ODBC Driver 18 was installed through the official Microsoft Homebrew tap.
- A project-local Python virtual environment was created rather than modifying the system Python environment.
- `pyodbc` 5.3.0 was installed in that virtual environment and detected `ODBC Driver 18 for SQL Server`.
- The existing `sqlcmd` 1.10.0 installation remained available.

The Python dependency is pinned in `loader/requirements.txt`. The ODBC driver is an operating-system prerequisite and is intentionally not managed by `pip`.

Homebrew reported that other formulae on the workstation were outdated. They were not upgraded because unrelated workstation changes are outside this project's scope. It also repeated the unrelated untrusted `ngrok/ngrok` tap warning; no trust exception was added.
