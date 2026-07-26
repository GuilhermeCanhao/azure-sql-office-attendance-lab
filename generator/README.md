# Synthetic-Data Generator

The generator creates a deterministic canonical attendance truth and derives two fictional source datasets from it:

- Card-access events
- Managed-device Wi-Fi observations

The documented assumptions, anomaly counts, output files, reproducibility rules, and acceptance criteria are defined in [Synthetic Data Contract](../docs/synthetic-data-contract.md). Machine-readable inputs live in `config.json`.

## Design rules

- Use only Python's standard library for generation.
- Use a local `random.Random` instance initialized from the configured seed.
- Create timezone-aware local datetimes with `zoneinfo`, then emit UTC observations.
- Create the clean canonical truth before adding controlled invalid source rows.
- Keep expected results independent from the later Azure SQL reconciliation.
- Write files in stable deterministic order with explicit UTF-8 encoding and fixed CSV line endings.
- Calculate SHA-256 over the final bytes of every generated file.
- Never embed credentials, Azure endpoints, connection details, real identities, or employer terminology.

## Source-control boundary

The full `output/` directory is generated build output and is ignored by Git. The repository retains:

- `generate_data.py`
- `config.json`
- This documentation
- A small reviewed `sample/` directory added only after privacy validation

## Generate and verify

Run these commands from the project root:

```bash
python3 generator/generate_data.py --output generator/output/run-a --clean
python3 generator/verify_output.py --output generator/output/run-a

python3 generator/generate_data.py --output generator/output/run-b --clean
python3 generator/verify_output.py \
  --output generator/output/run-a \
  --compare generator/output/run-b
```

The verifier does not trust the expected-result files by themselves. It independently:

- checks the complete manifest inventory, SHA-256 hashes, CSV headers, and row counts;
- validates synthetic identifiers, reserved email domains, reference counts, and device-assignment intervals;
- reclassifies every card and Wi-Fi source row against the configured validation rules;
- resolves valid device observations to the person assigned at the observation time;
- reconstructs every daily attendance result from accepted raw signals;
- reconciles batch, anomaly, signal, and person-day totals; and
- compares two complete manifests and their bytes for deterministic reproduction.

## Verified baseline

Two clean local runs using contract version `1.0` and seed `20260715` produced identical manifest hashes:

```text
Manifest SHA-256: 15aa28b77394ec6f5de42e5f5dd8fdf8eda3809542619b93c8356d65e520e4fa
Source rows:      134372
Accepted rows:    133892
Rejected rows:    480
Person-days:      37151
CARD person-days: 1236
BOTH person-days: 24833
WIFI person-days: 11082
Checksum checks:  PASS
Contract checks:  PASS
Run comparison:   PASS
```

These totals are the deterministic baseline for the Azure SQL load and reconciliation checks. The full generated files remain ignored build output.

## Current status

The generator and independent verifier are implemented, reproducibility is proven locally, and the Azure SQL load path has used this deterministic output as its baseline. A 40 KB public sample has also been generated and independently checked: ten fictional people, one replacement-device history, 20 card rows, 28 Wi-Fi rows, all eight controlled quality cases, and nine checksummed CSV files passed privacy and integrity verification.
