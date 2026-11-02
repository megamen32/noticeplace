"""Offline contract for independent NoticePlace notification and AI-start controls."""
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError
from notification_center.incident_card import controls


class _Codec:
    def encode(self, action, incident_id):
        return f"n:{action}:{incident_id}"


class NotificationRuleSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.center = NotificationCenter(
            Path(tmp.name) / "rule.sqlite3",
            {"test-token": {"project": "fleet-health", "max_severity": "emergency",
                            "agent_jobs": ["diagnostic"]}},
            default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)
        self.counter = 0

    def event(self, key="cpu"):
        self.counter += 1
        payload = {
            "schema": "notify.event.v1", "project": "fleet-health",
            "recipient": "test-only", "kind": "incident", "severity": "critical",
            "title": "Synthetic CPU event", "dedup_key": f"monitor:{key}",
            "event_type": "monitor.signal", "agent_job": "diagnostic",
        }
        return self.center.create_event("test-token", f"synthetic-{self.counter}", payload)

    def test_independent_scoped_rules_and_revision_cas(self):
        first = self.event()
        incident_id = first["incident_id"]
        self.assertIsNotNone(first["agent_job_delivery_id"])
        rule = self.center.notification_rule_settings(incident_id)
        self.assertTrue(rule["notifications_enabled"])
        self.assertTrue(rule["auto_ai_enabled"])
        self.assertEqual(0, rule["revision"])

        updated = self.center.set_notification_rule_option(incident_id, "auto_ai", False, 0, "test:operator")
        self.assertFalse(updated["auto_ai_enabled"])
        self.assertTrue(updated["notifications_enabled"])
        self.assertEqual(1, updated["revision"])
        next_event = self.event()
        self.assertIsNone(next_event["agent_job_delivery_id"])
        self.assertFalse(next_event["notifications_muted"])

        with self.assertRaisesRegex(ValidationError, "reload"):
            self.center.set_notification_rule_option(incident_id, "auto_ai", True, 0, "test:operator")
        off = self.center.set_notification_rule_option(incident_id, "notifications", False, 1, "test:operator")
        self.assertFalse(off["notifications_enabled"])
        self.assertFalse(off["auto_ai_enabled"])
        muted_event = self.event()
        self.assertTrue(muted_event["notifications_muted"])
        self.assertIsNone(muted_event["agent_job_delivery_id"])

        ai_on = self.center.set_notification_rule_option(incident_id, "auto_ai", True, off["revision"], "test:operator")
        self.assertFalse(ai_on["notifications_enabled"])
        still_muted = self.event()
        self.assertTrue(still_muted["notifications_muted"])
        self.assertIsNotNone(still_muted["agent_job_delivery_id"])
        different = self.event("ram")
        self.assertFalse(different["notifications_muted"])
        self.assertIsNotNone(different["agent_job_delivery_id"])

    def test_legacy_mutes_migrate_without_changing_existing_choices(self):
        import sqlite3
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "old.sqlite3"
        with sqlite3.connect(db) as connection:
            connection.execute(
                "CREATE TABLE notification_mutes (project TEXT NOT NULL, recipient TEXT NOT NULL, "
                "dedup_key TEXT NOT NULL, muted_at REAL NOT NULL, muted_by TEXT NOT NULL, "
                "unmuted_at REAL, unmuted_by TEXT, PRIMARY KEY(project,recipient,dedup_key))"
            )
            connection.execute(
                "INSERT INTO notification_mutes VALUES ('fleet-health','test-only','monitor:cpu',1,'legacy',NULL,NULL)"
            )
        previous = self.center
        self.center = NotificationCenter(
            db, {"test-token": {"project": "fleet-health", "max_severity": "emergency",
                                "agent_jobs": ["diagnostic"]}}, default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)
        first = self.event("cpu")
        rule = self.center.notification_rule_settings(first["incident_id"])
        self.assertFalse(rule["notifications_enabled"])
        self.assertTrue(rule["auto_ai_enabled"])
        changed = self.center.set_notification_rule_option(
            first["incident_id"], "auto_ai", False, 0, "test:operator"
        )
        self.assertFalse(changed["notifications_enabled"])
        self.assertFalse(changed["auto_ai_enabled"])
        self.center = previous

    def test_other_recipient_does_not_inherit_ai_off(self):
        first = self.event("cpu")
        self.center.set_notification_rule_option(first["incident_id"], "auto_ai", False, 0, "test:operator")
        self.counter += 1
        other = self.center.create_event("test-token", f"recipient-{self.counter}", {
            "schema": "notify.event.v1", "project": "fleet-health", "recipient": "other",
            "kind": "incident", "severity": "critical", "title": "Other scope",
            "dedup_key": "monitor:cpu", "event_type": "monitor.signal", "agent_job": "diagnostic",
        })
        self.assertIsNotNone(other["agent_job_delivery_id"])
        self.assertTrue(self.center.notification_rule_settings(other["incident_id"])["auto_ai_enabled"])

    def test_notification_link_uses_only_incident_id_and_keeps_mute_button(self):
        incident_id = "inc_" + "a" * 32
        rows = controls(_Codec(), {
            "id": incident_id, "kind": "incident", "state": "open",
            "severity": "critical", "notifications_muted": False,
        })
        buttons = [button for row in rows for button in row]
        url = next(b["url"] for b in buttons if b["text"] == "Настроить это уведомление")
        self.assertEqual(f"https://notify.bezrabotnyi.com/admin/#/notification-rule/{incident_id}", url)
        self.assertTrue(any(b.get("callback_data", "").startswith("n:mute:") for b in buttons))
        self.assertNotIn("token=", url)
        self.assertNotIn("bearer=", url)
