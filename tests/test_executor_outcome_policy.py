"""Owner delivery policy 2026-11-06 23:55.

The owner receives only executor outcomes: the hourly «исполнитель
справился» digest (executor.digest → «Уведомления») and the critical
«исполнитель не смог запуститься» escalation. Detection facts stay
internal — the incident and the executor launch happen, no owner card.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, TelegramSender, telegram_mode

ENV = {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}


def _dosor_event(**overrides: object) -> dict[str, object]:
    event = {
        "schema": "notify.event.v1", "kind": "incident", "event_type": "health.degraded",
        "severity": "important", "project": "autotelegramviews", "recipient": "me",
        "title": "ATV просмотры: канал — КЛАСС КАНАЛА (4 постов)", "body": "Дозор",
        "dedup_key": "views-coverage/probe/class/2026-11-06-19",
        "source_id": "views-coverage-watchdog", "host_id": "roomhacker-server-100",
        "signal_type": "views-coverage", "correlation_id": "probe/class",
    }
    event.update(overrides)
    return event


class SuppressedDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.consumer_id = "consumer_probe"
        self.verifier = {
            "project": "autotelegramviews", "incident_role": "verifier",
            "principal_id": "source:views-coverage-watchdog",
            "verification_consumer_ids": [self.consumer_id],
            "verification_sources": ["views-coverage-watchdog"],
            "agent_jobs": ["health-diagnosis"], "max_severity": "important",
            "suppress_owner_delivery": True,
        }
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3", {"verifier-key": self.verifier}, default_quiet_hours=[])
        with self.center._lock, self.center._connection:
            token = "nct_" + self.consumer_id.replace("consumer_", "")
            self.center._connection.execute(
                "INSERT INTO consumers(id, project, name, token_hash, token_fingerprint, max_severity, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.consumer_id, "autotelegramviews", "dosor route", __import__("hashlib").sha256(token.encode()).hexdigest(),
                 "fp", "important", 1.0, 1.0))
        self.intake = token

    def test_detection_stays_internal_but_spawns_the_executor(self) -> None:
        with patch.dict("os.environ", ENV):
            created = self.center.create_event(self.intake, "probe-suppressed", _dosor_event())
        self.assertIsNotNone(created["agent_job_delivery_id"], "executor still launches")
        self.assertIsNone(created["initial_delivery_id"], "no owner card for detections")
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE incident_id = ? AND type = 'notification_suppressed'",
            (created["incident_id"],)).fetchone()
        self.assertIn("internal_detection_scope", audit["payload_json"])

    def test_digest_mode_routes_to_the_notifications_chat(self) -> None:
        self.assertEqual("notify", telegram_mode({"event_type": "executor.digest", "severity": "important"}))
        self.assertEqual("log", telegram_mode({"event_type": "incident", "severity": "important"}))

    def test_launch_failure_escalates_as_critical_owner_incident(self) -> None:
        with patch.dict("os.environ", ENV):
            created = self.center.create_event(self.intake, "probe-failed", _dosor_event())
        now = time.time()
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "UPDATE deliveries SET status = 'claimed', claimed_at = ?, attempt = attempt + 1 WHERE id = ?",
                (now - 30, created["agent_job_delivery_id"]))
            row = self.center._connection.execute(
                "SELECT * FROM deliveries WHERE id = ?", (created["agent_job_delivery_id"],)).fetchone()
            self.center.complete_delivery(row["id"], "failed", error="spawn refused: no worker capacity")
        escalation = self.center._connection.execute(
            "SELECT id, severity, title, state FROM incidents WHERE dedup_key = ?",
            (f"executor-launch-failed/{created['incident_id']}",)).fetchone()
        self.assertIsNotNone(escalation, "launch failure must reach the owner")
        self.assertEqual("critical", escalation["severity"])
        self.assertIn("не смог запуститься", escalation["title"])
        self.assertEqual("notify", telegram_mode({"event_type": "executor.launch_failed", "severity": "critical"}))

    def test_hourly_digest_collects_resolved_executor_work(self) -> None:
        with patch.dict("os.environ", ENV):
            created = self.center.create_event(self.intake, "probe-digest", _dosor_event(dedup_key="views-coverage/digest/1"))
        now = time.time()
        with self.center._lock, self.center._connection:
            self.center._connection.execute("UPDATE incidents SET state = 'resolved', updated_at = ? WHERE id = ?", (now - 60, created["incident_id"]))
            self.center._connection.execute(
                "UPDATE deliveries SET status = 'sent', updated_at = ? WHERE id = ?", (now - 120, created["agent_job_delivery_id"]))
            self.center._connection.execute("DELETE FROM runtime_settings WHERE key = 'executor_digest_last_at'")
        published = self.center.publish_executor_outcome_digest(now=now)
        self.assertIsNotNone(published, "success digest published")
        digest = self.center.get_incident(published)
        self.assertEqual("executor.digest", digest["event_type"])
        self.assertIn("справился", digest["title"])
        # the window advanced: an immediate second run publishes nothing
        self.assertIsNone(self.center.publish_executor_outcome_digest(now=now + 60))


if __name__ == "__main__":
    unittest.main()
