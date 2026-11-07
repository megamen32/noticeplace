"""Dosor executor launch: consumer intake tokens must inherit the diagnosis policy.

Owner acceptance (2026-11-06): a dosor incident must confirm its executor
within 10 minutes. The ATV container pins NOTIFY_CENTER_TOKEN to the consumer
intake token (nct_...), whose scope had no agent jobs, so no executor ever
spawned (inc_4ac91bd000cc4aa8, 19:09 2026-11-06). A server-owned verifier
grant that names the consumer (verification_consumer_ids) now also carries
the deterministic diagnosis policy to that consumer's events.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter


def _health_event(project: str = "autotelegramviews") -> dict[str, object]:
    return {
        "schema": "notify.event.v1", "kind": "incident", "event_type": "health.degraded",
        "severity": "important", "project": project, "recipient": "me",
        "title": "ATV просмотры: канал — КЛАСС КАНАЛА (4 постов)", "body": "Дозор",
        "dedup_key": "views-coverage/probe/class/2026-11-06-19",
        "source_id": "views-coverage-watchdog", "host_id": "roomhacker-server-100",
        "signal_type": "views-coverage", "correlation_id": "probe/class",
    }


class ConsumerIntakeDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.consumer_id = "consumer_probe"
        self.verifier = {
            "project": "autotelegramviews", "incident_role": "verifier",
            "principal_id": "source:views-coverage-watchdog",
            "verification_sources": ["views-coverage-watchdog"],
            "verification_consumer_ids": [self.consumer_id],
            "agent_jobs": ["health-diagnosis"], "max_severity": "important",
        }
        self.tokens = {"verifier-key": self.verifier}
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3", self.tokens, default_quiet_hours=[])

    def _create_consumer_intake_token(self, consumer_id: str | None = None) -> str:
        consumer_id = consumer_id or self.consumer_id
        with self.center._lock, self.center._connection:
            token = "nct_" + hashlib.sha256(consumer_id.encode()).hexdigest()[:32]
            self.center._connection.execute(
                "INSERT INTO consumers(id, project, name, token_hash, token_fingerprint, max_severity, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (consumer_id, "autotelegramviews", "dosor route", hashlib.sha256(token.encode()).hexdigest(),
                 "fp-" + consumer_id, "important", 1.0, 1.0))
        return token

    def test_intake_token_schedules_the_diagnosis_job(self) -> None:
        intake = self._create_consumer_intake_token()
        with patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}):
            created = self.center.create_event(intake, "probe-dosor", _health_event())
        self.assertIsNotNone(created["agent_job_delivery_id"],
                             "consumer intake token must inherit the verifier diagnosis policy")

    def test_consumer_without_a_grant_stays_without_the_job(self) -> None:
        token = self._create_consumer_intake_token("consumer_other")
        with patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}):
            created = self.center.create_event(token, "probe-other", _health_event())
        self.assertIsNone(created["agent_job_delivery_id"],
                          "a consumer outside the grant keeps the plain delivery scope")


if __name__ == "__main__":
    unittest.main()
