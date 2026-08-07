"""Focused durable delivery-policy coverage for scoped consumers."""

from __future__ import annotations

import tempfile
import time
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

    def test_matrix_stage_is_scheduled_after_its_operator_deadline_and_ack_cancels_it(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix escalation",
            policy=[
                {"kind": "telegram", "chat_id": -100123},
                {"kind": "matrix", "delay_seconds": 120},
                {"kind": "phone", "delay_seconds": 600},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "matrix-event", self.event())
        rows = self.center._connection.execute(
            "SELECT channel, due_at, target_json FROM deliveries WHERE incident_id = ? ORDER BY due_at",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(
            ["telegram.consumer:" + consumer["id"], "matrix.call", "android.phone.call"],
            [row["channel"] for row in rows],
        )
        self.assertLess(rows[0]["due_at"], rows[1]["due_at"])
        self.assertLess(rows[1]["due_at"], rows[2]["due_at"])
        self.assertEqual("{}", rows[1]["target_json"])
        initial = self.center.claim_due_deliveries(now_epoch=rows[0]["due_at"])
        self.assertEqual(1, len(initial))
        self.center.complete_delivery(initial[0]["id"], "sent")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"] - 0.01))
        self.center.acknowledge(created["incident_id"], "telegram:operator")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[2]["due_at"]))

    def test_generic_policy_accepts_one_root_step_without_fixed_platform_order(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Generic root",
            policy=[
                {
                    "id": "matrix-root",
                    "platform": "matrix",
                    "action": "call",
                    "target": {"room_id": "!ops:example.org"},
                    "retry_interval_seconds": 30,
                    "max_repeats": 1,
                }
            ],
        )

        stored = self.center.get_consumer(consumer["id"])
        self.assertEqual("matrix", stored["policy"][0]["platform"])
        self.assertEqual("call", stored["policy"][0]["action"])
        self.assertNotIn("previous_step_id", stored["policy"][0])

    def test_generic_policy_schedules_only_root_step_initially(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Root first",
            policy=[
                {
                    "id": "matrix-root",
                    "platform": "matrix",
                    "action": "message",
                    "target": {"room_id": "!ops:example.org"},
                    "retry_interval_seconds": 60,
                    "max_repeats": 2,
                },
                {
                    "id": "telegram-successor",
                    "platform": "telegram",
                    "action": "message",
                    "target": {"chat_id": -100123},
                    "retry_interval_seconds": 15,
                    "max_repeats": 1,
                    "previous_step_id": "matrix-root",
                },
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "root-only", self.event())
        rows = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id = ? ORDER BY due_at", (created["incident_id"],)
        ).fetchall()
        self.assertEqual(["matrix.message"], [row["channel"] for row in rows])

    def test_generic_policy_repeat_budget_then_schedules_successor(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Budgeted chain",
            policy=[
                {
                    "id": "telegram-root",
                    "platform": "telegram",
                    "action": "message",
                    "target": {"chat_id": -100123},
                    "retry_interval_seconds": 60,
                    "max_repeats": 2,
                },
                {
                    "id": "phone-next",
                    "platform": "phone",
                    "action": "call",
                    "target": {"device": "operator"},
                    "retry_interval_seconds": 120,
                    "max_repeats": 1,
                    "previous_step_id": "telegram-root",
                },
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "budgeted-chain", self.event())
        first = self.center.claim_due_deliveries(now_epoch=time.time() + 1)
        self.assertEqual(1, len(first))
        self.center.complete_delivery(first[0]["id"], "sent")
        rows = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id = ? ORDER BY created_at", (created["incident_id"],)
        ).fetchall()
        self.assertEqual(["telegram.message", "telegram.message"], [row["channel"] for row in rows])
        self.assertEqual(["sent", "queued"], [row["status"] for row in rows])

        next_due = self.center._connection.execute(
            "SELECT due_at FROM deliveries WHERE incident_id = ? AND status = 'queued'", (created["incident_id"],)
        ).fetchone()["due_at"]
        second = self.center.claim_due_deliveries(now_epoch=next_due)
        self.assertEqual(1, len(second))
        self.center.complete_delivery(second[0]["id"], "sent")
        rows = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id = ? ORDER BY created_at", (created["incident_id"],)
        ).fetchall()
        self.assertEqual(["telegram.message", "telegram.message", "phone.call"], [row["channel"] for row in rows])
        self.assertEqual("queued", rows[2]["status"])


if __name__ == "__main__":
    unittest.main()
