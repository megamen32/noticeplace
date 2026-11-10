"""Offline contract for independent NoticePlace notification and AI-start controls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter, ValidationError, notification_rule_projection, notification_rule_source_key
from notification_center.incident_card import controls


class _Codec:
    def encode(self, action, incident_id):
        return f"n:{action}:{incident_id}"


class _CountingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.execute_count = 0

    def execute(self, *args, **kwargs):
        self.execute_count += 1
        return self.connection.execute(*args, **kwargs)


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

    def test_notification_rule_projection_is_pure_and_scopes_source_override(self):
        incident = {
            "id": "inc_fixture",
            "project": "fleet-health",
            "recipient": "test-only",
            "dedup_key": "monitor:cpu",
            "collapse_key": "telegram-family:cpu",
            "notifications_muted": 0,
        }
        mute = {
            "auto_ai_by_source": '{"' + notification_rule_source_key("host:one", "monitor:cpu") + '": false}',
            "revision": 7,
        }
        result = notification_rule_projection(
            incident, mute, "host:one", rule_key="telegram-family:cpu", match_mode="exact",
        )
        self.assertEqual(False, result["auto_ai_enabled"])
        self.assertEqual("telegram-family:cpu", result["dedup_key"])
        self.assertEqual("monitor:cpu", result["ai_dedup_key"])
        self.assertEqual(7, result["revision"])

    def test_notification_rule_read_uses_one_mesh_round_trip(self):
        first = self.event()
        original = self.center._connection
        counted = _CountingConnection(original)
        self.center._connection = counted
        try:
            result = self.center.notification_rule_settings(first["incident_id"])
        finally:
            self.center._connection = original
        self.assertTrue(result["notifications_enabled"])
        self.assertEqual(1, counted.execute_count)

    def test_notification_rule_read_uses_incident_indexed_source_scalar(self):
        first = self.event()
        traces = []
        self.center._connection.set_trace_callback(traces.append)
        try:
            self.center.notification_rule_settings(first["incident_id"])
        finally:
            self.center._connection.set_trace_callback(None)
        select = next(sql for sql in traces if sql.lstrip().upper().startswith("SELECT"))
        self.assertIn("SELECT first.payload_json FROM events first", select)
        self.assertNotIn("LEFT JOIN events e", select)
        plan = self.center._connection.execute("EXPLAIN QUERY PLAN " + select).fetchall()
        details = [str(row[3]) for row in plan]
        self.assertFalse(any("SCAN e" in detail for detail in details), details)
        self.assertTrue(any("SEARCH first USING INDEX" in detail for detail in details), details)

    def test_cold_admin_rule_read_is_read_only_without_schema_initialization(self):
        import sqlite3
        from notification_center.admin import AdminConfigStore

        first = self.event()
        expected = self.center.notification_rule_settings(first["incident_id"])
        database = self.center._connection.execute("PRAGMA database_list").fetchone()[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "primary.env"
            env.write_text(f"NOTIFY_CENTER_DB={database}\n")
            store = AdminConfigStore(env, root / "routes", root)
            statements = []
            connect = sqlite3.connect
            def traced_connect(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            with patch("notification_center.admin.NotificationCenter", side_effect=AssertionError("schema init invoked")), \
                 patch("sqlite3.connect", side_effect=traced_connect):
                actual = store.notification_rule_settings(first["incident_id"])
            self.assertEqual(expected, actual)
            self.assertTrue(any(s.lstrip().upper().startswith("SELECT") for s in statements))
            self.assertFalse(any(s.lstrip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "BEGIN", "REPLACE")) for s in statements), statements)

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
            first["incident_id"], "auto_ai", False, rule["revision"], "test:operator"
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
