#!/usr/bin/env python3
"""Offline-default verifier for the Phase 7 monitoring contract."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


MONITORING_DIR = Path(__file__).resolve().parent
if str(MONITORING_DIR) not in sys.path:
    sys.path.insert(0, str(MONITORING_DIR))

from monitoring_common import (  # noqa: E402
    AUDIT_ACTIONS,
    DURABLE_ALERT,
    MonitoringContractError,
    safe_summary,
    validate_audit_contract,
    validate_complete_contract,
    validate_foundation_contract,
)


PROJECT_TAG = "azure-sql-office-attendance-lab"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint",
        choices=("foundation", "audit", "complete"),
        default="complete",
        help="Acceptance boundary to verify.",
    )
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="Read the approved Azure resource group through the existing CLI session.",
    )
    return parser.parse_args()


def _az_json(arguments: Sequence[str]) -> object:
    executable = shutil.which("az")
    if executable is None:
        raise MonitoringContractError("Azure CLI is unavailable.")
    command = [executable, *arguments, "--only-show-errors", "--output", "json"]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MonitoringContractError("Azure read-only verification could not start.") from exc
    if completed.returncode != 0:
        raise MonitoringContractError("Azure read-only verification failed; provider details were suppressed.")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MonitoringContractError("Azure returned an unusable verification response.") from exc


def _one(rows: object, label: str) -> Mapping[str, object]:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise MonitoringContractError(f"{label} inventory is not exactly one.")
    return rows[0]


def _bool_or_false(value: object) -> bool:
    return value is True


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _duration_minutes(value: object) -> int:
    return {"PT1M": 1, "PT5M": 5}.get(str(value), 0)


def collect_live_state() -> dict:
    group = _one(
        _az_json(["group", "list", "--query", f"[?tags.project=='{PROJECT_TAG}']"]),
        "Project resource group",
    )
    group_name = str(group["name"])
    server_list_item = _one(
        _az_json(["sql", "server", "list", "--resource-group", group_name]),
        "SQL server",
    )
    server_name = str(server_list_item["name"])
    server = _az_json([
        "sql", "server", "show", "--resource-group", group_name, "--name", server_name,
    ])
    database = _one(
        [row for row in _az_json([
            "sql", "db", "list", "--resource-group", group_name, "--server", server_name
        ]) if row.get("name") != "master"],
        "User database",
    )
    database_id = str(database["id"])
    storage_rows = _az_json(["storage", "account", "list", "--resource-group", group_name])
    storage = _one(storage_rows, "Audit storage")
    storage_name = str(storage["name"])
    storage_id = str(storage["id"])
    blob = _az_json([
        "storage", "account", "blob-service-properties", "show",
        "--resource-group", group_name, "--account-name", storage_name,
    ])
    identity = _mapping(server.get("identity"))
    principal_id = str(identity.get("principalId", ""))
    signed_in = _az_json(["ad", "signed-in-user", "show"])
    signed_in_id = str(signed_in.get("id", ""))
    writer_roles = _az_json([
        "role", "assignment", "list", "--scope", storage_id,
        "--assignee", principal_id,
    ])
    reader_roles = _az_json([
        "role", "assignment", "list", "--scope", storage_id,
        "--assignee", signed_in_id,
    ])
    audit = _az_json([
        "rest", "--method", "get", "--url",
        f"{database_id}/extendedAuditingSettings/default?api-version=2023-08-01",
    ])
    server_audit = _az_json([
        "sql", "server", "audit-policy", "show",
        "--resource-group", group_name, "--name", server_name,
    ])
    properties = _mapping(audit.get("properties"))
    network_rules = _mapping(storage.get("networkRuleSet"))
    delete_retention = _mapping(blob.get("deleteRetentionPolicy"))
    change_feed = _mapping(blob.get("changeFeed"))
    workspaces = _az_json([
        "monitor", "log-analytics", "workspace", "list", "--resource-group", group_name,
    ])
    event_hubs = _az_json([
        "eventhubs", "namespace", "list", "--resource-group", group_name,
    ])
    action_groups = _az_json([
        "monitor", "action-group", "list", "--resource-group", group_name,
    ])
    action_group = _one(action_groups, "Action group")
    email_receivers = _list(action_group.get("emailReceivers"))
    other_receiver_fields = (
        "armRoleReceivers", "automationRunbookReceivers", "azureAppPushReceivers",
        "azureFunctionReceivers", "eventHubReceivers", "incidentReceivers",
        "itsmReceivers", "logicAppReceivers", "smsReceivers", "voiceReceivers",
        "webhookReceivers",
    )
    metric_alerts = _az_json([
        "monitor", "metrics", "alert", "list", "--resource-group", group_name,
    ])
    durable_rows = [
        row for row in metric_alerts
        if _mapping(row.get("tags")).get("lifecycle") == "durable"
    ]
    durable = durable_rows[0] if len(durable_rows) == 1 else {}
    criteria = _list(_mapping(durable.get("criteria")).get("allOf"))
    criterion = _mapping(criteria[0]) if criteria else {}
    state = {
        "storage_count": len(storage_rows),
        "storage": {
            "kind": storage.get("kind"),
            "sku": storage.get("sku", {}).get("name"),
            "tier": storage.get("accessTier"),
            "https_only": storage.get("enableHttpsTrafficOnly"),
            "minimum_tls": storage.get("minimumTlsVersion"),
            "blob_public_access": storage.get("allowBlobPublicAccess"),
            "shared_key_access": storage.get("allowSharedKeyAccess"),
            "cross_tenant_replication": storage.get("allowCrossTenantReplication"),
            "public_network_access": storage.get("publicNetworkAccess"),
            "default_action": network_rules.get("defaultAction"),
            "bypass": network_rules.get("bypass"),
            "soft_delete_enabled": _bool_or_false(delete_retention.get("enabled")),
            "versioning_enabled": _bool_or_false(blob.get("isVersioningEnabled")),
            "change_feed_enabled": _bool_or_false(change_feed.get("enabled")),
        },
        "server_identity": identity.get("type"),
        "writer_role_count": sum(
            row.get("roleDefinitionName") == "Storage Blob Data Contributor" for row in writer_roles
        ),
        "reader_role_count": sum(
            row.get("roleDefinitionName") == "Storage Blob Data Reader" for row in reader_roles
        ),
        "server_audit_state": server_audit.get("state"),
        "database_audit": {
            "state": properties.get("state"),
            "retention_days": properties.get("retentionDays"),
            "managed_identity": properties.get("isManagedIdentityInUse"),
            "storage_endpoint_present": bool(properties.get("storageEndpoint")),
            "storage_access_key_present": bool(properties.get("storageAccountAccessKey")),
            "azure_monitor_target": _bool_or_false(properties.get("isAzureMonitorTargetEnabled")),
            "event_hub_target": False,
            "actions": tuple(properties.get("auditActionsAndGroups", ())),
        },
        "log_analytics_count": len(workspaces),
        "event_hub_namespace_count": len(event_hubs),
        "action_group_count": len(action_groups),
        "action_group": {
            "enabled": action_group.get("enabled"),
            "email_receivers": len(email_receivers),
            "common_alert_schema": (
                email_receivers[0].get("useCommonAlertSchema")
                if len(email_receivers) == 1 and isinstance(email_receivers[0], dict)
                else False
            ),
            "other_receivers": sum(
                len(_list(action_group.get(field))) for field in other_receiver_fields
            ),
        },
        "metric_alert_count": len(metric_alerts),
        "temporary_alert_count": sum(
            _mapping(row.get("tags")).get("lifecycle") == "temporary"
            for row in metric_alerts
        ),
        "durable_alert": {
            "metric": criterion.get("metricName"),
            "aggregation": criterion.get("timeAggregation"),
            "operator": criterion.get("operator"),
            "threshold": float(criterion.get("threshold", -1)),
            "window_minutes": _duration_minutes(durable.get("windowSize")),
            "frequency_minutes": _duration_minutes(durable.get("evaluationFrequency")),
            "severity": durable.get("severity"),
            "enabled": durable.get("enabled"),
            "auto_mitigate": durable.get("autoMitigate"),
            "action_count": len(_list(durable.get("actions"))),
        },
    }
    if state["database_audit"]["storage_access_key_present"]:
        raise MonitoringContractError("Audit policy unexpectedly returned storage-key material.")
    return state


def offline_state() -> dict:
    return {
        "storage_count": 1,
        "storage": {
            "kind": "StorageV2", "sku": "Standard_LRS", "tier": "Hot",
            "https_only": True, "minimum_tls": "TLS1_2", "blob_public_access": False,
            "shared_key_access": False, "cross_tenant_replication": False,
            "public_network_access": "Enabled", "default_action": "Deny",
            "bypass": "AzureServices", "soft_delete_enabled": False,
            "versioning_enabled": False, "change_feed_enabled": False,
        },
        "server_identity": "SystemAssigned", "writer_role_count": 1,
        "reader_role_count": 1, "server_audit_state": "Disabled",
        "database_audit": {
            "state": "Enabled", "retention_days": 7, "managed_identity": True,
            "storage_endpoint_present": True, "storage_access_key_present": False,
            "azure_monitor_target": False, "event_hub_target": False,
            "actions": AUDIT_ACTIONS,
        },
        "log_analytics_count": 0, "event_hub_namespace_count": 0,
        "action_group_count": 1, "metric_alert_count": 1,
        "action_group": {
            "enabled": True, "email_receivers": 1,
            "common_alert_schema": True, "other_receivers": 0,
        },
        "temporary_alert_count": 0,
        "durable_alert": {**DURABLE_ALERT.__dict__, "action_count": 1},
    }


def verify_state(state: Mapping[str, object], checkpoint: str) -> None:
    if checkpoint == "foundation":
        validate_foundation_contract(state)
    elif checkpoint == "audit":
        validate_foundation_contract(state)
        validate_audit_contract(state.get("database_audit", {}))
    else:
        validate_complete_contract(state)


def main() -> int:
    try:
        args = parse_args()
        state = collect_live_state() if args.execute_live else offline_state()
        verify_state(state, args.checkpoint)
        print("Phase 7 monitoring verification: PASS")
        for line in safe_summary(state, args.checkpoint):
            print(line)
        print(f"Mode: {'LIVE_READ_ONLY' if args.execute_live else 'DRY_RUN'}")
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, MonitoringContractError) else "Unexpected details were suppressed."
        print(f"Phase 7 monitoring verification: FAIL — {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
