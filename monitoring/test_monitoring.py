#!/usr/bin/env python3
"""Offline tests for the Phase 7 monitoring and privacy contracts."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MONITORING_DIR = Path(__file__).resolve().parent
LOADER_DIR = MONITORING_DIR.parent / "loader"
for directory in (MONITORING_DIR, LOADER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from monitoring_common import (  # noqa: E402
    AUDIT_ACTIONS,
    MonitoringContractError,
    validate_audit_contract,
    validate_complete_contract,
)
from loader_common import SafeLoaderError  # noqa: E402
import verify_monitoring  # noqa: E402
import generate_audit_activity  # noqa: E402
import verify_audit_delivery  # noqa: E402


class MonitoringContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = verify_monitoring.offline_state()

    def test_frozen_complete_contract_passes(self) -> None:
        validate_complete_contract(self.state)

    def test_verbose_batch_auditing_is_rejected(self) -> None:
        policy = dict(self.state["database_audit"])
        policy["actions"] = (*AUDIT_ACTIONS, "BATCH_COMPLETED_GROUP")
        with self.assertRaisesRegex(MonitoringContractError, "action set is not exact"):
            validate_audit_contract(policy)

    def test_storage_key_and_extra_destination_are_rejected(self) -> None:
        keyed = dict(self.state["database_audit"])
        keyed["storage_access_key_present"] = True
        with self.assertRaisesRegex(MonitoringContractError, "storage key"):
            validate_audit_contract(keyed)
        monitored = dict(self.state["database_audit"])
        monitored["azure_monitor_target"] = True
        with self.assertRaisesRegex(MonitoringContractError, "Azure Monitor"):
            validate_audit_contract(monitored)

    def test_weak_storage_and_wrong_alert_are_rejected(self) -> None:
        weak = dict(self.state)
        weak["storage"] = dict(self.state["storage"], shared_key_access=True)
        with self.assertRaisesRegex(MonitoringContractError, "shared_key_access"):
            validate_complete_contract(weak)
        wrong_alert = dict(self.state)
        wrong_alert["durable_alert"] = dict(self.state["durable_alert"], threshold=0.0)
        with self.assertRaisesRegex(MonitoringContractError, "threshold"):
            validate_complete_contract(wrong_alert)

    def test_extra_receiver_and_missing_alert_action_are_rejected(self) -> None:
        extra_receiver = dict(self.state)
        extra_receiver["action_group"] = dict(
            self.state["action_group"], other_receivers=1
        )
        with self.assertRaisesRegex(MonitoringContractError, "Unexpected action-group"):
            validate_complete_contract(extra_receiver)
        missing_action = dict(self.state)
        missing_action["durable_alert"] = dict(
            self.state["durable_alert"], action_count=0
        )
        with self.assertRaisesRegex(MonitoringContractError, "action inventory"):
            validate_complete_contract(missing_action)

    def test_default_mode_never_calls_azure(self) -> None:
        with patch.object(sys, "argv", ["verify_monitoring.py"]):
            with patch.object(verify_monitoring, "collect_live_state") as live:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(verify_monitoring.main(), 0)
        live.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_optional_null_objects_are_safe(self) -> None:
        self.assertEqual(verify_monitoring._mapping(None), {})
        self.assertEqual(verify_monitoring._list(None), [])
        self.assertFalse(verify_monitoring._bool_or_false(None))
        self.assertEqual(verify_monitoring._duration_minutes("PT5M"), 5)

    def test_safe_summary_excludes_runtime_identifiers(self) -> None:
        output = "\n".join(verify_monitoring.safe_summary(self.state, "complete"))
        for forbidden in (
            "subscriptionid", "tenantid", "accountid", "endpoint=", "@",
            ".database.windows.net", "connection string",
        ):
            self.assertNotIn(forbidden, output.lower())

    def test_controlled_activity_source_is_rollback_protected(self) -> None:
        self.assertEqual(
            generate_audit_activity.validate_suite_source(),
            {"required_markers": 14, "expected_result_fields": 15},
        )

    def test_controlled_activity_default_is_offline(self) -> None:
        with patch.object(sys, "argv", ["generate_audit_activity.py"]):
            with patch.object(generate_audit_activity, "runtime_target") as runtime:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(generate_audit_activity.main(), 0)
        runtime.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_audit_delivery_requires_all_generic_categories(self) -> None:
        counts = {category: 1 for category in verify_audit_delivery.EXPECTED_CATEGORIES}
        verify_audit_delivery.verify_category_counts(counts)
        counts.pop("PERMISSION_CHANGE")
        with self.assertRaisesRegex(SafeLoaderError, "PERMISSION_CHANGE"):
            verify_audit_delivery.verify_category_counts(counts)

    def test_audit_delivery_default_is_offline(self) -> None:
        with patch.object(sys, "argv", ["verify_audit_delivery.py"]):
            with patch.object(verify_audit_delivery, "runtime_target") as runtime:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(verify_audit_delivery.main(), 0)
        runtime.assert_not_called()
        self.assertIn("DRY_RUN", output.getvalue())

    def test_audit_delivery_errors_are_safely_categorized(self) -> None:
        self.assertEqual(
            verify_audit_delivery.audit_error_category(
                Exception("33224 invalid audit file at private endpoint")
            ),
            "DELIVERY_PENDING_OR_PATH",
        )
        self.assertEqual(
            verify_audit_delivery.audit_error_category(
                Exception("permission denied for private identity")
            ),
            "ACCESS",
        )
        self.assertEqual(
            verify_audit_delivery.audit_error_signature(
                Exception("42000", "private message (33224) (0)")
            ),
            "SQLSTATE_42000_SQL_33224",
        )


if __name__ == "__main__":
    unittest.main()
