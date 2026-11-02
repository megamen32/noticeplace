"""Focused regression tests: actual MeshConnection transaction adapter, per-source AI scope, legacy mute. No external sends."""
import tempfile
import unittest
from pathlib import Path
from notification_center.core import NotificationCenter
from notification_center.mesh_store import Connection as MeshConnection

class RuleReviewContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name) / "fixtures.sqlite3",
            {"fixture-token": {"project": "fixture", "max_severity": "emergency", "agent_jobs": ["diagnostic"]}},
            default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.counter = 0

    def event(self, host="one"):
        self.counter += 1
        return self.center.create_event("fixture-token", f"fixture-{self.counter}", {
            "schema": "notify.event.v1", "project": "fixture", "recipient": "test-only",
            "kind": "incident", "severity": "critical", "title": "Synthetic CPU",
            "dedup_key": "cpu:" + host, "issue_family": "cpu-group",
            "source_id": "host:" + host, "host_id": host, "signal_type": "cpu",
            "event_type": "host.monitor", "agent_job": "diagnostic",
        })

    def test_mesh_connection_contract_owns_the_write_transaction(self):
        original = self.center._connection
        first = self.event()
        mesh = MeshConnection("/tmp/no-socket-needed-for-fixture")
        def transact(payload):
            cursor = original.execute(payload["sql"], payload.get("params", []))
            return {
                "columns": [d[0] for d in cursor.description] if cursor.description else [],
                "rows": [list(row) for row in cursor.fetchall()] if cursor.description else [],
                "rowcount": cursor.rowcount, "lastrowid": cursor.lastrowid,
            }
        mesh._request = transact
        self.center._connection = mesh
        try:
            after = self.center.set_notification_rule_option(
                first["incident_id"], "auto_ai", False, 0, "fixture:operator")
            self.assertFalse(after["auto_ai_enabled"])
            self.assertEqual(1, after["revision"])
            self.assertFalse(original.in_transaction)
        finally:
            self.center._connection = original

    def test_same_group_other_source_keeps_ai_enabled_and_queued(self):
        a = self.event("one")
        b = self.event("two")
        self.assertNotEqual(a["incident_id"], b["incident_id"])
        rule_a = self.center.notification_rule_settings(a["incident_id"])
        rule_b = self.center.notification_rule_settings(b["incident_id"])
        self.assertEqual(rule_a["dedup_key"], rule_b["dedup_key"])
        self.center.set_notification_rule_option(
            a["incident_id"], "auto_ai", False, rule_a["revision"], "fixture:operator")
        self.assertTrue(self.center.notification_rule_settings(b["incident_id"])["auto_ai_enabled"])
        self.assertFalse(self.center.notification_rule_settings(a["incident_id"])["auto_ai_enabled"])
        follow_b = self.event("two")
        follow_a = self.event("one")
        self.assertIsNotNone(follow_b["agent_job_delivery_id"])
        self.assertIsNone(follow_a["agent_job_delivery_id"])
        self.assertFalse(follow_a["notifications_muted"])

    def test_unmute_effectively_clears_family_and_legacy_dedup(self):
        a = self.event("one")
        incident_id = a["incident_id"]
        rule = self.center.notification_rule_settings(incident_id)
        self.assertNotEqual(rule["dedup_key"], "cpu:one")
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "INSERT INTO notification_mutes(project,recipient,dedup_key,muted_at,muted_by,unmuted_at,unmuted_by)"
                " VALUES (?,?,?,?,?,NULL,NULL)", ("fixture","test-only","cpu:one",1,"fixture:legacy"))
        self.assertFalse(self.center.notification_rule_settings(incident_id)["notifications_enabled"])
        after = self.center.set_notification_rule_option(
            incident_id, "notifications", True, rule["revision"], "fixture:operator")
        self.assertTrue(after["notifications_enabled"])
        self.assertFalse(self.event("one")["notifications_muted"])
