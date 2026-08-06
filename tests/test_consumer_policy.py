"""Focused durable delivery-policy coverage for scoped consumers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError


class ConsumerPolicyTests(unittest.TestCase):
    """Prove consumer policy, not producer payload, owns delivery targets."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"legacy": {"project": "hermes", "max_severity": "critical"}},
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def event(**overrides: object) -> dict[str, object]:
        event: dict[str, object] = {
            "schema": "notify.event.v1",
            "project": "hermes",
            "recipient": "operator",
            "kind": "incident",
            "severity": "critical",
            "title": "Gateway unavailable",
            "dedup_key": "gateway:consumer",
        }
        event.update(overrides)
        return event

    def test_consumer_token_schedules_operator_policy_and_ack_cancels_phone(self) -> None:
        created_consumer = self.center.create_consumer(
            project="hermes",
            name="Gateway producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123, "topic_id": 42},
                {"kind": "phone", "delay_seconds": 600},
                {"kind": "matrix", "enabled": False},
                {"kind": "whatsapp", "enabled": False},
            ],
        )

        self.assertTrue(created_consumer["intake_token"])
        stored = self.center.get_consumer(created_consumer["id"])
        self.assertEqual(created_consumer["token_fingerprint"], stored["token_fingerprint"])
        self.assertNotIn("intake_token", stored)
        self.assertEqual(["telegram", "phone", "matrix", "whatsapp"], [stage["kind"] for stage in stored["policy"]])

        created = self.center.create_event(created_consumer["intake_token"], "consumer-event", self.event())
        rows = self.center._connection.execute(
            "SELECT channel, due_at, target_json FROM deliveries WHERE incident_id = ? ORDER BY due_at, channel",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(["telegram.consumer:" + created_consumer["id"], "android.phone.call"], [row["channel"] for row in rows])
        self.assertLess(rows[0]["due_at"], rows[1]["due_at"])
        self.assertIn('"chat_id": -100123', rows[0]["target_json"])
        claimed = self.center.claim_due_deliveries(now_epoch=rows[0]["due_at"])
        self.assertEqual({"chat_id": -100123, "topic_id": 42}, self.center.delivery_payload(claimed[0])["target"])
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"] - 0.1))

        self.center.acknowledge(created["incident_id"], "telegram:operator")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"]))

    def test_producer_cannot_override_consumer_policy_or_legacy_route(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Scoped producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123},
                {"kind": "phone", "delay_seconds": 30},
            ],
        )

        for authority_field in ("target", "platform", "phone_number", "command", "delay_seconds", "retry", "stage"):
            with self.subTest(authority_field=authority_field), self.assertRaises(ValidationError):
                self.center.create_event(consumer["intake_token"], f"forbidden-{authority_field}", self.event(**{authority_field: "override"}))

        legacy = self.center.create_event("legacy", "legacy-event", self.event(dedup_key="gateway:legacy"))
        channel = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE id = ?", (legacy["initial_delivery_id"],)
        ).fetchone()["channel"]
        self.assertEqual("telegram.main", channel)


if __name__ == "__main__":
    unittest.main()
