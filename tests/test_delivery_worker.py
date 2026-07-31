"""Delivery adapter tests, including the MatrixRTC ACK boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, MatrixCallSender


class DeliveryWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "critical"}})
        self.event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Gateway unavailable", "body": "three checks failed", "dedup_key": "gateway:100",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

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
