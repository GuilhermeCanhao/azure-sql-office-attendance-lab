#!/usr/bin/env python3
"""Verify private audit delivery using only generic server-side category counts."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping


MONITORING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MONITORING_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
for directory in (MONITORING_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    SafeLoaderError,
    acquire_azure_sql_token,
    connect_ready_target,
    runtime_target,
    safe_main_error,
)


AUDIT_PREFIX_RE = re.compile(
    r"^https://[a-z0-9]+\.blob\.core\.windows\.net/"
    r"sqldbauditlogs/[A-Za-z0-9-]+/[A-Za-z0-9_-]+/$"
)
EXPECTED_CATEGORIES = (
    "SUCCESSFUL_AUTHENTICATION",
    "PRINCIPAL_CHANGE",
    "ROLE_MEMBER_CHANGE",
    "OBJECT_CHANGE",
    "PERMISSION_CHANGE",
)

CATEGORY_SQL = """
WITH private_events AS
(
    SELECT
        CASE
            WHEN action_id IN ('DBAS', 'LGIS') THEN 'SUCCESSFUL_AUTHENTICATION'
            WHEN statement LIKE '%CREATE USER tst_phase7_audit%' THEN 'PRINCIPAL_CHANGE'
            WHEN statement LIKE '%ALTER ROLE report_reader ADD MEMBER tst_phase7_audit%' THEN 'ROLE_MEMBER_CHANGE'
            WHEN statement LIKE '%CREATE VIEW report.vw_Phase7AuditProbe%' THEN 'OBJECT_CHANGE'
            WHEN statement LIKE '%GRANT CONNECT TO tst_phase7_audit%'
              OR statement LIKE '%DENY CREATE TABLE TO tst_phase7_audit%' THEN 'PERMISSION_CHANGE'
        END AS CategoryName
    FROM sys.fn_get_audit_file_v2(?, DEFAULT, DEFAULT, ?, ?)
)
SELECT CategoryName, COUNT_BIG(*)
FROM private_events
WHERE CategoryName IS NOT NULL
GROUP BY CategoryName
ORDER BY CategoryName;
"""

ACTION_INVENTORY_SQL = """
SELECT action_id, COALESCE(class_type, ''), COUNT_BIG(*)
FROM sys.fn_get_audit_file_v2(?, DEFAULT, DEFAULT, ?, ?)
GROUP BY action_id, class_type
ORDER BY action_id, class_type;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Read private audit blobs through Azure SQL using the existing Entra session.",
    )
    return parser.parse_args()


def audit_prefix() -> str:
    value = os.environ.get("ATTENDANCE_AUDIT_PREFIX", "").strip()
    if not value:
        raise SafeLoaderError("Runtime audit prefix is missing.")
    if not AUDIT_PREFIX_RE.fullmatch(value):
        raise SafeLoaderError("Runtime audit prefix format is invalid.")
    return value


def verify_category_counts(counts: Mapping[str, int]) -> None:
    for category in EXPECTED_CATEGORIES:
        if counts.get(category, 0) < 1:
            raise SafeLoaderError(f"Audit delivery category is missing: {category}.")
    if any(category not in EXPECTED_CATEGORIES for category in counts):
        raise SafeLoaderError("Audit delivery returned an unexpected public category.")


def audit_error_category(exc: BaseException) -> str:
    text = " ".join(str(part) for part in getattr(exc, "args", ())).lower()
    if "fn_get_audit_file_v2" in text and ("not recognized" in text or "could not find" in text):
        return "FUNCTION_UNAVAILABLE"
    if "37620" in text or "37621" in text or "starttimefilter" in text or "endtimefilter" in text:
        return "TIME_FILTER_FORMAT"
    if "invalid audit file" in text or "33224" in text or "does not exist" in text:
        return "DELIVERY_PENDING_OR_PATH"
    if "permission" in text or "denied" in text or "not authorized" in text:
        return "ACCESS"
    if "parameter" in text or "operand type clash" in text or "conversion failed" in text:
        return "PARAMETER_BINDING"
    if "syntax" in text:
        return "SYNTAX"
    if "timeout" in text:
        return "TIMEOUT"
    return "UNCLASSIFIED"


def audit_error_signature(exc: BaseException) -> str:
    parts = tuple(str(part) for part in getattr(exc, "args", ()))
    sqlstate = parts[0] if parts and re.fullmatch(r"[0-9A-Z]{5}", parts[0]) else "NONE"
    numbers = []
    for part in parts[1:]:
        numbers.extend(re.findall(r"\((\d{4,6})\)", part))
    unique = tuple(dict.fromkeys(numbers))[:3]
    return f"SQLSTATE_{sqlstate}_SQL_{'-'.join(unique) if unique else 'NONE'}"


def read_category_counts(connection, prefix: str) -> Mapping[str, int]:
    end_value = datetime.utcnow()
    start_value = end_value - timedelta(hours=2)
    start = start_value.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_value.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = connection.cursor().execute(CATEGORY_SQL, prefix, start, end).fetchall()
    except Exception as exc:
        category = audit_error_category(exc)
        signature = audit_error_signature(exc)
        raise SafeLoaderError(
            f"Private audit-log query failed ({category}; {signature}); "
            "blob and database details were suppressed."
        ) from exc
    return {str(name): int(count) for name, count in rows}


def read_safe_action_inventory(connection, prefix: str):
    end_value = datetime.utcnow()
    start_value = end_value - timedelta(hours=2)
    start = start_value.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_value.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = connection.cursor().execute(
            ACTION_INVENTORY_SQL, prefix, start, end
        ).fetchall()
    except Exception as exc:
        raise SafeLoaderError(
            "Safe audit-action inventory failed; private details were suppressed."
        ) from exc
    inventory = []
    for action_id, class_type, count in rows:
        action = str(action_id).strip()
        class_name = str(class_type).strip()
        if not re.fullmatch(r"[A-Z0-9]{1,4}", action) or not re.fullmatch(
            r"[A-Z0-9]{0,4}", class_name
        ):
            raise SafeLoaderError("Audit action inventory contained an unsafe code.")
        inventory.append((action, class_name or "NONE", int(count)))
    return tuple(inventory)


def main() -> int:
    connection = None
    try:
        args = parse_args()
        if not args.execute:
            verify_category_counts({category: 1 for category in EXPECTED_CATEGORIES})
            print("Private audit-delivery contract: PASS Categories=5 LookbackHours=2")
            print("Mode: DRY_RUN — Azure CLI, Azure SQL, and Blob storage were not accessed.")
            return 0

        server, database = runtime_target()
        prefix = audit_prefix()
        token_struct = acquire_azure_sql_token()
        connection = connect_ready_target(server, database, token_struct)
        print("Data-plane readiness probe: PASS")
        counts = read_category_counts(connection, prefix)
        try:
            verify_category_counts(counts)
        except SafeLoaderError:
            inventory = read_safe_action_inventory(connection, prefix)
            encoded = ",".join(
                f"{action}/{class_name}:{count}"
                for action, class_name, count in inventory
            )
            print(f"SafeActionInventory={encoded or 'EMPTY'}")
            raise
        print("Private audit delivery: PASS Categories=5")
        for category in EXPECTED_CATEGORIES:
            print(f"{category}={counts[category]}")
        print("RawAuditRecordsExported=0")
        return 0
    except Exception as exc:
        print(f"Private audit delivery: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
