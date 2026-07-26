#!/usr/bin/env python3
"""Frozen, privacy-safe acceptance contracts for Phase 7 monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


AUDIT_ACTIONS = (
    "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
    "FAILED_DATABASE_AUTHENTICATION_GROUP",
    "DATABASE_OBJECT_CHANGE_GROUP",
    "DATABASE_PRINCIPAL_CHANGE_GROUP",
    "DATABASE_ROLE_MEMBER_CHANGE_GROUP",
    "DATABASE_PERMISSION_CHANGE_GROUP",
)


class MonitoringContractError(RuntimeError):
    """A validation failure whose message is safe for public evidence."""


@dataclass(frozen=True)
class DurableAlertContract:
    metric: str = "cpu_percent"
    aggregation: str = "Average"
    operator: str = "GreaterThan"
    threshold: float = 80.0
    window_minutes: int = 5
    frequency_minutes: int = 1
    severity: int = 2
    enabled: bool = True
    auto_mitigate: bool = True


DURABLE_ALERT = DurableAlertContract()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MonitoringContractError(message)


def validate_storage_contract(storage: Mapping[str, object]) -> None:
    expected = {
        "kind": "StorageV2",
        "sku": "Standard_LRS",
        "tier": "Hot",
        "https_only": True,
        "minimum_tls": "TLS1_2",
        "blob_public_access": False,
        "shared_key_access": False,
        "cross_tenant_replication": False,
        "public_network_access": "Enabled",
        "default_action": "Deny",
        "bypass": "AzureServices",
        "soft_delete_enabled": False,
        "versioning_enabled": False,
        "change_feed_enabled": False,
    }
    for key, value in expected.items():
        require(storage.get(key) == value, f"Storage contract failed: {key}.")


def validate_audit_contract(policy: Mapping[str, object]) -> None:
    require(policy.get("state") == "Enabled", "Database auditing is not enabled.")
    require(policy.get("retention_days") == 7, "Audit retention is not seven days.")
    require(policy.get("managed_identity") is True, "Audit managed identity is not enabled.")
    require(policy.get("storage_endpoint_present") is True, "Audit storage destination is absent.")
    require(policy.get("storage_access_key_present") is False, "Audit policy contains a storage key.")
    require(policy.get("azure_monitor_target") is False, "Unexpected Azure Monitor audit target exists.")
    require(policy.get("event_hub_target") is False, "Unexpected Event Hub audit target exists.")
    require(
        tuple(sorted(policy.get("actions", ()))) == tuple(sorted(AUDIT_ACTIONS)),
        "Database audit action set is not exact.",
    )


def validate_foundation_contract(state: Mapping[str, object]) -> None:
    require(state.get("storage_count") == 1, "Audit storage inventory is not exact.")
    validate_storage_contract(state.get("storage", {}))
    require(state.get("server_identity") == "SystemAssigned", "Server identity is not system assigned.")
    require(state.get("writer_role_count") == 1, "Audit-writer role inventory is not exact.")
    require(state.get("reader_role_count") == 1, "Audit-reader role inventory is not exact.")
    require(state.get("server_audit_state") == "Disabled", "Server-level auditing is enabled.")


def validate_complete_contract(state: Mapping[str, object]) -> None:
    validate_foundation_contract(state)
    validate_audit_contract(state.get("database_audit", {}))
    require(state.get("log_analytics_count") == 0, "Unexpected Log Analytics workspace exists.")
    require(state.get("event_hub_namespace_count") == 0, "Unexpected Event Hub namespace exists.")
    require(state.get("action_group_count") == 1, "Action-group inventory is not exact.")
    action_group = state.get("action_group", {})
    require(action_group.get("enabled") is True, "Action group is not enabled.")
    require(action_group.get("email_receivers") == 1, "Email-receiver inventory is not exact.")
    require(
        action_group.get("common_alert_schema") is True,
        "Email receiver does not use the common alert schema.",
    )
    require(action_group.get("other_receivers") == 0, "Unexpected action-group receiver exists.")
    require(state.get("metric_alert_count") == 1, "Metric-alert inventory is not exact.")
    alert = state.get("durable_alert", {})
    for key, value in DURABLE_ALERT.__dict__.items():
        require(alert.get(key) == value, f"Durable alert contract failed: {key}.")
    require(alert.get("action_count") == 1, "Durable alert action inventory is not exact.")
    require(state.get("temporary_alert_count") == 0, "Temporary metric alert still exists.")


def safe_summary(state: Mapping[str, object], checkpoint: str) -> Sequence[str]:
    """Return only generic counts and statuses suitable for public evidence."""
    lines = [
        f"Checkpoint: {checkpoint}",
        f"StorageAccounts={state.get('storage_count', 0)}",
        f"WriterRoles={state.get('writer_role_count', 0)}",
        f"ReaderRoles={state.get('reader_role_count', 0)}",
        f"ServerAudit={state.get('server_audit_state', 'UNKNOWN')}",
    ]
    if checkpoint in {"audit", "complete"}:
        policy = state.get("database_audit", {})
        lines.append(f"DatabaseAudit={policy.get('state', 'UNKNOWN')}")
        lines.append(f"AuditRetentionDays={policy.get('retention_days', 0)}")
        lines.append(f"AuditActions={len(policy.get('actions', ())) }")
    if checkpoint == "complete":
        lines.append(f"ActionGroups={state.get('action_group_count', 0)}")
        lines.append(f"MetricAlerts={state.get('metric_alert_count', 0)}")
        lines.append(f"TemporaryAlerts={state.get('temporary_alert_count', 0)}")
    return tuple(lines)
