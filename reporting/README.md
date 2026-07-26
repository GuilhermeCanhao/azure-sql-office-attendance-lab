# Reporting Verification

This directory derives the reviewed aggregate report outputs from the generator's independent expected files and can compare them with the four deployed `report` views.

Default execution is offline and does not invoke Azure CLI or Azure SQL:

```bash
.venv/bin/python reporting/verify_reporting.py
```

The explicit live mode verifies the deployed reporting views:

```bash
.venv/bin/python reporting/verify_reporting.py --execute-verify
```

Runtime target values use the same validated, non-printed environment variables and short-lived Azure CLI token path as the controlled loader. The verifier queries only the reviewed `report` views. It does not read `stage` or `core`, print runtime targets, or persist credentials.
