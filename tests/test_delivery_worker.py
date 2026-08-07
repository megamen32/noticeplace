"""Delivery adapter tests, including the MatrixRTC ACK boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, MatrixCallSender


class DeliveryWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "emergency"}})
        self.event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Gateway unavailable", "body": "three checks failed", "dedup_key": "gateway:100",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_inactive_standard_mode_is_cancelled_without_telegram_send(self) -> None:
        created = self.center.create_event("producer", "inactive-critical", {**self.event, "severity": "debug"})

        class Telegram:
            active_modes = {"important"}

            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("inactive mode must not reach Telegram")

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        status = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"]
        self.assertEqual("cancelled", status)

    def test_confirmed_matrix_answer_acknowledges_only_that_incident(self) -> None:
        created = self.center.create_event("producer", "create", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)
        calls: list[dict[str, object]] = []

        class Matrix:
            def send(self, payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {"answered": True, "actor": "matrix:@bezrabotnyi:chat.example"}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix escalation adapter")

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix()).run_once())
        self.assertEqual("acknowledged", self.center.get_incident(created["incident_id"])["state"])
        self.assertEqual(1, len(calls))

    def test_initial_critical_telegram_delivery_schedules_one_matrix_call(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=object(), call_escalation_seconds=60)
        self.assertEqual(1, worker.run_once())
        queued = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual(["matrix.call"], [item["channel"] for item in queued])
        self.assertEqual(created["incident_id"], queued[0]["incident_id"])

    def test_critical_repeats_while_open_and_calls_matrix_after_its_deadline(self) -> None:
        created = self.center.create_event("producer", "critical-repeat", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(
            self.center,
            Telegram(),
            matrix_call=object(),
            critical_repeat_seconds=600,
            critical_call_escalation_seconds=3600,
        )
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel, delivery_key FROM deliveries WHERE incident_id = ? AND status = 'queued' ORDER BY delivery_key",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(
            [("matrix.call", f"{created['incident_id']}:matrix.call:escalation"), ("telegram.main", f"{created['incident_id']}:telegram.main:repeat:1")],
            [(row["channel"], row["delivery_key"]) for row in rows],
        )

    def test_emergency_schedules_matrix_without_repeat(self) -> None:
        emergency = {**self.event, "severity": "emergency", "dedup_key": "gateway:emergency"}
        created = self.center.create_event("producer", "emergency", emergency)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=object(), emergency_call_escalation_seconds=600)
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id = ? AND status = 'queued' ORDER BY channel",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(["matrix.call"], [row["channel"] for row in rows])

    def test_important_is_single_shot_without_repeat_or_call(self) -> None:
        important = {**self.event, "severity": "important", "dedup_key": "gateway:important"}
        created = self.center.create_event("producer", "important", important)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(
            self.center,
            Telegram(),
            matrix_call=object(),
            critical_repeat_seconds=600,
            critical_call_escalation_seconds=3600,
            emergency_call_escalation_seconds=600,
        )
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id = ? AND status = 'queued'",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], rows)

    def test_unanswered_matrix_call_falls_back_to_s21(self) -> None:
        created = self.center.create_event("producer", "matrix-fallback", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)

        class Matrix:
            def send(self, _payload: dict[str, object]) -> dict[str, object]:
                return {"answered": False, "actor": None}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix fallback adapter")

        class Android:
            can_phone_call = True

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix(), android_phone=Android()).run_once())
        queued = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual(["android.phone.call"], [item["channel"] for item in queued])

    def test_initial_critical_delivery_schedules_one_phone_call_after_configured_delay(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            can_phone_call = True

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def phone_call(self, payload: dict[str, object]) -> None:
                self.calls.append(payload)

        android = Android()
        worker = DeliveryWorker(
            self.center,
            Telegram(),
            android_phone=android,
            android_phone_call_escalation_seconds=600,
        )
        with mock.patch("notification_center.http_api.time.time", return_value=10**12):
            self.assertEqual(1, worker.run_once())

        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=(10**12) + 599.0))
        due = self.center.claim_due_deliveries(now_epoch=(10**12) + 600.0)
        self.assertEqual(["android.phone.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(android.calls))
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=10**12))

    def test_consumer_telegram_uses_policy_target_and_keeps_existing_phone_deadline(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Gateway producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123, "topic_id": 42},
                {"kind": "phone", "delay_seconds": 600},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "consumer-dispatch", self.event)
        sent: list[dict[str, object]] = []

        class Telegram:
            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        class Android:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def phone_call(self, payload: dict[str, object]) -> None:
                self.calls.append(payload)

        android = Android()
        worker = DeliveryWorker(self.center, Telegram(), android_phone=android)
        deadlines = self.center._connection.execute(
            "SELECT channel, due_at FROM deliveries WHERE incident_id = ? ORDER BY due_at", (created["incident_id"],)
        ).fetchall()
        initial = self.center.claim_due_deliveries(now_epoch=deadlines[0]["due_at"])
        self.assertEqual(1, len(initial))
        worker.deliver(initial[0])
        self.assertEqual({"chat_id": -100123, "topic_id": 42}, sent[0]["target"])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=deadlines[1]["due_at"] - 0.1))
        due = self.center.claim_due_deliveries(now_epoch=deadlines[1]["due_at"])
        self.assertEqual(["android.phone.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(android.calls))

    def test_consumer_matrix_is_not_delivered_before_its_policy_deadline(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123},
                {"kind": "matrix", "delay_seconds": 120},
                {"kind": "phone", "delay_seconds": 600},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "consumer-matrix", self.event)
        calls: list[dict[str, object]] = []

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Matrix:
            def send(self, payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {"answered": False, "actor": None}

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=Matrix())
        rows = self.center._connection.execute(
            "SELECT channel, due_at FROM deliveries WHERE incident_id = ? ORDER BY due_at",
            (created["incident_id"],),
        ).fetchall()
        initial = self.center.claim_due_deliveries(now_epoch=rows[0]["due_at"])
        worker.deliver(initial[0])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"] - 0.01))
        due = self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"])
        self.assertEqual(["matrix.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(calls))

    def test_resolved_critical_incident_cancels_phone_call_before_deadline(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            can_phone_call = True

            def phone_call(self, _payload: dict[str, object]) -> None:
                raise AssertionError("resolved incident must not call the phone")

        worker = DeliveryWorker(self.center, Telegram(), android_phone=Android(), android_phone_call_escalation_seconds=600)
        with mock.patch("notification_center.http_api.time.time", return_value=10**12):
            self.assertEqual(1, worker.run_once())
        self.center.resolve(created["incident_id"], "test:reply")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=(10**12) + 600.0))

    def test_matrix_answer_after_resolution_does_not_resurrect_the_incident(self) -> None:
        created = self.center.create_event("producer", "create", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)

        class Matrix:
            def send(_self, _payload: dict[str, object]) -> dict[str, object]:
                self.center.resolve(created["incident_id"], "producer")
                return {"answered": True, "actor": "matrix:@bezrabotnyi:chat.example"}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix escalation adapter")

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix()).run_once())
        self.assertEqual("resolved", self.center.get_incident(created["incident_id"])["state"])
        status = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE incident_id = ? AND channel = 'matrix.call'", (created["incident_id"],)
        ).fetchone()["status"]
        self.assertEqual("cancelled", status)

    def test_matrix_sender_posts_only_safe_incident_fields_and_requires_answer(self) -> None:
        received: dict[str, object] = {}

        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok":true,"answered":true,"target":"@bezrabotnyi:chat.example"}'

        def opener(request: object, timeout: float) -> Response:
            received["url"] = request.full_url
            received["body"] = request.data.decode()
            received["authorization"] = request.get_header("Authorization")
            received["timeout"] = timeout
            return Response()

        sender = MatrixCallSender("http://matrix-bridge.test/v1/calls", "bridge-token", runner=opener)
        result = sender.send({"incident": {"id": "inc_123", "title": "Outage", "body": "details", "project": "hermes", "severity": "critical"}})
        self.assertTrue(result["answered"])
        self.assertEqual("matrix:@bezrabotnyi:chat.example", result["actor"])
        self.assertEqual("http://matrix-bridge.test/v1/calls", received["url"])
        self.assertEqual("Bearer bridge-token", received["authorization"])
        self.assertIn('"incident_id": "inc_123"', str(received["body"]))
        self.assertNotIn("access_token", str(received["body"]))

        class Unanswered(Response):
            def read(self) -> bytes:
                return b'{"ok":true,"answered":false}'

        self.assertFalse(MatrixCallSender("http://bridge.test/v1/calls", "bridge-token", runner=lambda *_args, **_kwargs: Unanswered()).send({"incident": {"id": "inc_123", "title": "Outage", "body": "details", "project": "hermes", "severity": "critical"}})["answered"])


if __name__ == "__main__":
    unittest.main()
