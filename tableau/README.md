# Tableau Tooling

This directory contains the scripts used to prepare and check the Tableau aggregate extracts. The public dashboard is published on Tableau Public, but no workbook binary or packaged extract is committed to the repository.

## Aggregate-export contract

The exporter produces four separate UTF-8 CSV files plus a deterministic manifest:

- `daily_attendance_trend.csv` — 261 office-day rows;
- `daily_department_attendance.csv` — 2,087 department-day rows;
- `load_quality_summary.csv` — two source-type rows;
- `validation_issue_summary.csv` — eight source/validation-code rows;
- `tableau_export_manifest.json` — columns, row counts, SHA-256 values, and expected totals.

The files stay separate because their grains differ. Joining them would multiply measures. Generated exports live under ignored `tableau/output/` and are not committed automatically.

Default dry run:

```bash
.venv/bin/python tableau/export_tableau_data.py
```

Explicit local write and readback check:

```bash
.venv/bin/python tableau/export_tableau_data.py --write-exports
.venv/bin/python tableau/export_tableau_data.py --verify-exports
```

The explicit writer refuses symlink destinations and unexpected existing content. Output contains no person, device, access-point, event, batch identifier, source filename, checksum, individual timestamp, email, endpoint, or credential field.

## Workbook and package privacy verification

Default policy dry run:

```bash
.venv/bin/python tableau/verify_tableau_artifact.py
```

After a local workbook exists:

```bash
.venv/bin/python tableau/verify_tableau_artifact.py \
  --artifact /private/path/to/reviewed-workbook.twb
```

The verifier checks the aggregate export directory and inspects a plain or packaged Tableau workbook for private metadata. It rejects Azure SQL endpoints, IP or email addresses, private absolute paths, connection strings, credentials, account identifiers, live SQL connectors, direct `stage` or `core` references, restricted fields, unsafe ZIP paths, unexpected CSV sources, and ambiguous package structure.

The published workbook is available here:

[Azure SQL Office Attendance Analytics Lab](https://public.tableau.com/app/profile/guilherme.canh.o/viz/azure-sql-office-attendance-lab-tableau-public/AttendanceOverview)

## Tests

```bash
.venv/bin/python -m unittest tableau/test_tableau.py
```

The tests cover expected totals, deterministic output, local readback, drift rejection, offline-default behavior, workbook/package inspection, private-metadata rejection, ZIP safety, unexpected-source rejection, and absence of a live client path.

## Temporary reporting identity

The [temporary reporting identity note](../docs/tableau-service-principal-design.md) is implemented by `manage_service_principal.py` and `service_principal_common.py`. Default execution is offline:

```bash
.venv/bin/python tableau/manage_service_principal.py
```

Live modes are explicit:

```bash
.venv/bin/python tableau/manage_service_principal.py --execute-preflight
.venv/bin/python tableau/manage_service_principal.py --execute-create-bind-verify
.venv/bin/python tableau/manage_service_principal.py --execute-cleanup
```

Do not execute these modes merely because they exist. Preflight is read-only but opens Azure CLI and Azure SQL. Creation makes a temporary Entra application, service principal, short-lived credential, contained database user, and role membership. Cleanup deletes them. The completed connectivity proof ended with zero residual identities and zero `report_reader` members.

The client does not accept secrets or private Azure identifiers on the command line. Runtime target environment variables are used only for the administrator data-plane connection, and generated credentials are kept out of repository output.

Offline tests:

```bash
.venv/bin/python -m unittest tableau/test_service_principal.py
```

The final published dashboard contains three dashboards: Attendance Overview, Department and Signal Mix, and Data Quality. Each dashboard includes visible synthetic-data and non-authoritative-use disclosures.
