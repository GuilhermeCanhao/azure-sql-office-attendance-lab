# Public Synthetic-Data Sample

This directory is a small deterministic excerpt of the full generated dataset. It lets a portfolio reviewer inspect the CSV contracts without committing or downloading the complete build output.

The sample contains:

- one fictional office and the generic reference values needed to interpret the excerpt;
- ten fictional people using the reserved `attendance-lab.example` domain;
- opaque synthetic device tokens, including one fictional replacement history;
- 20 card rows: 16 valid examples and one example of each controlled card validation case;
- 28 Wi-Fi rows: 24 valid examples and one example of each controlled Wi-Fi validation case; and
- a small validation-count oracle and SHA-256 manifest.

The sample is illustrative and is not the Azure SQL load input. The full dataset is generated locally from the committed configuration and remains excluded from source control.

Regenerate and validate it from the project root:

```bash
python3 generator/create_public_sample.py \
  --input generator/output/run-a \
  --output generator/sample \
  --clean

python3 generator/verify_public_sample.py \
  --sample generator/sample
```

The verifier rejects unexpected files, checksum or row-count changes, non-reserved emails, malformed synthetic identifiers, MAC-like identifiers, IPv4 addresses, Azure SQL endpoints, credential terminology, employer-pipeline terminology, missing quality cases, and inconsistent source lineage.
