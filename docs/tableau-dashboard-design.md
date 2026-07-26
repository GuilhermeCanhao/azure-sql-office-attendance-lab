# Tableau Dashboard Design

The Tableau workbook is a public companion to the Azure SQL lab. It helps a reviewer see the aggregate output, but it is not the security boundary and it is not a live connection to Azure SQL.

[Azure SQL Office Attendance Analytics Lab](https://public.tableau.com/app/profile/guilherme.canh.o/viz/azure-sql-office-attendance-lab-tableau-public/AttendanceOverview)

## Reporting sources

The workbook uses four separate aggregate extracts. I kept them separate because they have different grains; joining them into one table would multiply measures.

| Data source | Grain | Rows | Used for |
|---|---|---:|---|
| `report.vw_DailyAttendanceTrend` | Date and office | 261 | KPIs, daily trend, and CARD/WIFI/BOTH mix |
| `report.vw_DailyDepartmentAttendance` | Date, office, and department | 2,087 | Department totals and signal mix |
| `report.vw_LoadQualitySummary` | Source type | 2 | Accepted and rejected load counts |
| `report.vw_ValidationIssueSummary` | Source type and validation code | 8 | Controlled rejection reasons |

The same totals used by the database checks are visible in the workbook: 37,151 person-days, including 1,236 CARD-only, 11,082 WIFI-only, and 24,833 BOTH person-days; 134,372 received source rows; 133,892 accepted rows; and 480 rejected rows.

## Public boundary

The published workbook contains only fictional aggregate data. It has no live Azure SQL connection, saved credential, database endpoint, account identifier, person-level attendance rows, device rows, source filenames, checksums, or individual event timestamps.

Tableau Public should be treated as public by default. Download settings may reduce casual access, but they are not the privacy control. The privacy control is that the workbook starts from synthetic aggregate extracts.

## Dashboard pages

The workbook has three dashboards:

1. **Attendance Overview** — total person-days, average daily attendance, average occupancy rate, daily attendance trend, and daily signal mix.
2. **Department and Signal Mix** — department-level attendance totals and CARD/WIFI/BOTH composition.
3. **Data Quality** — accepted/rejected rates, terminal batch state, and validation-code counts.

Each dashboard includes the same disclosure: the data is fictional and synthetic, and the output is an aggregate attendance estimate only. It should not be used as evidence of individual presence or for payroll, disciplinary, performance, or access-control decisions.

## Checks before publishing

Before publishing, I checked the package for private connection metadata and reconciled the extract row counts and totals against the reporting output. Tableau Public converted the sources to embedded extracts during publication, so I checked the publication package again before treating the public link as ready.

The workbook binary is not committed to the repository. The repository keeps the source scripts, aggregate-export tooling, artifact checks, and public documentation.

## Known limits

- The public dashboard is not live-connected to Azure SQL.
- The dashboard does not expose individual attendance rows.
- The date filter applies only to the daily trend and daily signal-mix charts; the headline KPIs keep the full canonical period.
- Weekly and weekday views are intentionally omitted because they would add little to this version without a separately documented calendar model.

## Sources

- [Tableau Azure SQL Database connector](https://help.tableau.com/current/pro/desktop/en-us/examples_azure_sql_database.htm)
- [Save workbooks with Tableau Public](https://help.tableau.com/current/pro/desktop/en-us/publish_workbooks_tableaupublic.htm)
- [Tableau Public FAQ](https://help.tableau.com/current/pro/desktop/en-us/public_faq.htm)
