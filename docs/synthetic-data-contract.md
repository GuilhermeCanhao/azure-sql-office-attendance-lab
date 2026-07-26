# Synthetic Data Contract

## Purpose

This contract defines the deterministic fictional dataset used by the generator, Azure SQL loading procedures, reconciliation tests, performance exercise, and Tableau dashboard. It is documented before generation so every later component works against the same expected truth.

The generated data is independent and synthetic. It does not model a real employer, office, workforce, schedule, network, device inventory, or access-control implementation.

## Contract version

| Field | Value |
|---|---|
| Contract version | `1.0` |
| Generator seed | `20260715` |
| Local timezone | `Europe/Lisbon` |
| SQL Server timezone | `GMT Standard Time` |
| History start | `2025-07-01` |
| History end | `2026-06-30` |
| Batch frequency | Monthly |
| Source files | 12 card files and 12 Wi-Fi files |

The generator must produce byte-identical files and SHA-256 checksums when the contract version, configuration, supported Python runtime, and seed are unchanged. Runtime timestamps are written to console output, not generated files, because a live timestamp would make an otherwise deterministic build differ.

## Fictional reference population

| Entity | Contract |
|---|---|
| Office | One fictional office, `PT-LAB-01`, with no real street or employer association |
| People | 300 clearly synthetic personnel records |
| Departments | Eight generic fictional departments with static version 1 membership |
| Card readers | One fictional main-entry reader |
| Wi-Fi access points | Four fictional access points |
| Devices | One active managed device per person at any point in time |
| Replacements | Fifteen people receive one replacement device during the history period |

All personnel codes, emails, device tokens, access-point codes, and names follow the constraints already deployed in Azure SQL. Emails use only `attendance-lab.example`. Device tokens are opaque values such as `DEV-8F2A91C4`, never MAC-address-shaped identifiers.

## Attendance truth model

The generator first creates a canonical person-day truth model. Source observations are then derived from that truth. This allows Azure SQL results to be checked against an independent expected answer rather than merely against the input rows.

- Only weekdays are eligible for attendance in version 1.
- The weekday probabilities sum to an expected average of approximately 2.35 attendance days per person per week.
- A person-specific deterministic propensity modifier provides variation without reproducing a real working pattern.
- Most valid attending person-days have two to four managed-device Wi-Fi observations.
- Approximately 70% of valid attending person-days have one card event.
- A small subset of card-observed days intentionally has no Wi-Fi observation, modelling cases where the card reader fills a Wi-Fi gap.
- Some attending days still have Wi-Fi signal but no individual card event, modelling shared-entry undercounting from badge data alone.
- Non-attending person-days produce neither source signal.

Expected valid classifications are therefore `CARD`, `WIFI`, and `BOTH`. The result is still an aggregate attendance estimate, not proof of individual presence.

## Time handling

Attendance times are created as timezone-aware local datetimes in `Europe/Lisbon` and converted to UTC before being written to the source files. This deliberately crosses daylight-saving transitions during the twelve-month history.

The generator must retain the local attendance date in the expected-results data. Azure SQL must independently reproduce that date from the UTC observation and the office's `GMT Standard Time` configuration.

## Controlled anomalies

Invalid rows are added only after the clean truth and valid source observations are complete. They appear as ordinary raw source rows so the Azure SQL loader must discover and quarantine them.

### Card anomalies

| Validation scenario | Fixed rows |
|---|---:|
| Malformed observation timestamp | 30 |
| Unknown synthetic personnel code | 30 |
| Unknown fictional access point | 30 |
| Blank personnel code | 30 |
| **Total** | **120** |

### Wi-Fi anomalies

| Validation scenario | Fixed rows |
|---|---:|
| Malformed observation timestamp | 90 |
| Unknown opaque device token | 90 |
| Unknown fictional access point | 90 |
| Invalid signal-strength value | 90 |
| **Total** | **360** |

An unknown device token is an inventory-resolution test. It is not presented as an assigned laptop failing to produce an observation.

## Expected scale

The fixed seed and probabilistic attendance model should produce approximately 125,000 to 145,000 total card and Wi-Fi source rows, including the 480 controlled anomalies. The manifest records the exact generated counts; the range is a guardrail rather than a hard-coded expected answer.

This size is sufficient for batch loading, reconciliation, Query Store, execution-plan, indexing, and Tableau work while remaining appropriate for the Azure SQL free offer.

## Output contract

```text
generator/output/
├── reference/
│   ├── offices.csv
│   ├── departments.csv
│   ├── people.csv
│   ├── devices.csv
│   ├── device_assignments.csv
│   └── access_points.csv
├── card/
│   └── card_events_YYYY_MM.csv
├── wifi/
│   └── wifi_observations_YYYY_MM.csv
├── expected/
│   ├── expected_daily_attendance.csv
│   ├── expected_batch_results.csv
│   └── expected_validation_counts.csv
└── manifest.json
```

The complete `generator/output/` directory is reproducible build output and is excluded from Git. The repository retains the generator, configuration, documentation, and a small reviewed sample rather than committing the full dataset.

## CSV column contracts

Every CSV uses UTF-8, a header row, comma delimiters, RFC 4180 quoting, and `\n` line endings. Files are written in stable key order. Empty optional values are represented by an empty field.

### Reference files

| File | Columns in order |
|---|---|
| `offices.csv` | `office_code`, `display_name`, `time_zone_name`, `capacity`, `is_active` |
| `departments.csv` | `department_code`, `department_name`, `is_active` |
| `people.csv` | `personnel_code`, `display_name`, `synthetic_email`, `department_code`, `valid_from`, `valid_to` |
| `devices.csv` | `device_token`, `device_status` |
| `device_assignments.csv` | `personnel_code`, `device_token`, `valid_from_utc`, `valid_to_utc` |
| `access_points.csv` | `office_code`, `access_point_code`, `access_point_type`, `display_label`, `is_active` |

### Monthly source files

| Source | Columns in order |
|---|---|
| Card | `source_row_number`, `observed_at_raw`, `personnel_code_raw`, `access_point_code_raw` |
| Wi-Fi | `source_row_number`, `observed_at_raw`, `device_token_raw`, `access_point_code_raw`, `signal_strength_raw` |

Source row numbers start at one independently within every file and remain unique within that file. Valid UTC timestamps use ISO 8601 with millisecond precision and a trailing `Z`. Invalid rows deliberately retain the same columns and do not contain a label revealing their expected validation result.

### Independent expected-results files

| File | Columns in order |
|---|---|
| `expected_daily_attendance.csv` | `attendance_date_local`, `personnel_code`, `detection_method`, `first_observed_at_utc`, `last_observed_at_utc`, `card_signal_count`, `wifi_signal_count` |
| `expected_batch_results.csv` | `source_type`, `source_file_name`, `rows_received`, `rows_accepted`, `rows_rejected`, `file_sha256` |
| `expected_validation_counts.csv` | `source_type`, `source_file_name`, `validation_code`, `expected_count` |

The expected files are derived from the canonical truth and anomaly plan, not from Azure SQL output. They therefore remain an independent reconciliation target.

## Manifest requirements

`manifest.json` must contain:

- Contract and generator versions
- Seed and supported Python runtime
- Date range and timezone names
- Counts for every reference entity
- Exact row count and SHA-256 checksum for every output file
- Accepted and rejected count for every monthly source batch
- Expected count for every validation code
- Expected daily `BOTH` and `WIFI` totals
- Expected overall source, accepted, rejected, fact, and person-day totals

## Acceptance criteria

The generator is accepted only when:

1. Two clean runs with the same configuration produce identical data-file checksums and counts.
2. All generated identifiers satisfy the deployed schema formats and are unique where required.
3. Every synthetic email uses the reserved project domain.
4. Device-assignment periods are half-open and never overlap.
5. No valid weekend attendance exists.
6. Every valid attending person-day contains Wi-Fi signal.
7. No valid `CARD`-only person-day exists.
8. Controlled anomalies exactly match the configured counts and validation codes.
9. Source volume remains within the documented guardrail.
10. Expected daily results reconcile to valid source observations.
11. Every output file is listed in the manifest with a matching SHA-256 checksum.
12. Privacy scanning finds no real names, email domains, locations, MAC addresses, credentials, Azure identifiers, or employer terminology.

The completed Azure load added a second acceptance layer: actual batch, rejection, fact, and daily-summary results matched the independent expected files and manifest.
