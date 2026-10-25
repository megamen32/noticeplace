"""A failing dependency must not loop and must not reach the owner channels.

The Matrix transport of Notice Place answers a definitive refusal (the identity
may not use that room) or a transient failure. A definitive refusal is never
retried, and a generic consumer step never exceeds the attempt ceiling its own
policy states. A failed Matrix step of a Matrix only consumer never becomes a
Telegram card and never becomes a phone call.
"""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, MatrixMessageSender


class MatrixDeliveryFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"producer": {"project": "hermes", "max_severity": "emergency"}},
            default_quiet_hours=[],
        )
        self.event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "QA notice",
            "body": "the isolated room must carry the notice",
            "dedup_key": "qa-room-notice-1",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _consumer(self, max_repeats: int = 1):
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix only route",
            policy=[{
                "id": "matrix-root",
                "platform": "matrix",
                "action": "message",
                "target": {"room_id": "!qa:example.org"},
                "retry_interval_seconds": 30,
                "max_repeats": max_repeats,
            }],
        )
        created = self.center.create_event(
            consumer["intake_token"], "qa-room-notice-1", dict(self.event),
        )
        return consumer, created

    def _worker(self, runner) -> DeliveryWorker:
        sender = MatrixMessageSender(
            "https://matrix.example.org", "matrix-token",
            default_room_id="!qa:example.org", runner=runner,
        )
        return DeliveryWorker(
            self.center,
            type("Telegram", (), {"send": lambda *_args: (_ for _ in ()).throw(AssertionError("wrong adapter"))})(),
            message_adapters={"matrix.message": sender},
        )

    def _delivery(self, delivery_id: str):
        return self.center._connection.execute(
            "SELECT id, status, attempt, last_error FROM deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()

    def test_definitive_matrix_refusal_fails_once_and_is_never_retried(self) -> None:
        calls: list[object] = []

        class Response:
            status = 403

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read() -> bytes:
                return b'{"errcode":"M_FORBIDDEN"}'

        def runner(request, *, timeout):
            calls.append(request)
            return Response()

        _consumer, created = self._consumer()
        worker = self._worker(runner)

        self.assertEqual(1, worker.run_once())
        row = self._delivery(created["initial_delivery_id"])
        self.assertEqual("failed", row["status"])
        self.assertEqual(1, row["attempt"])
        self.assertEqual(1, len(calls))

        # A second worker tick must not send again and must not requeue.
        self.assertEqual(0, worker.run_once())
        row = self._delivery(created["initial_delivery_id"])
        self.assertEqual("failed", row["status"])
        self.assertEqual(1, row["attempt"])
        self.assertEqual(1, len(calls))

    def test_transient_matrix_failure_stops_at_the_policy_attempt_ceiling(self) -> None:
        calls: list[object] = []

        def runner(request, *, timeout):
            calls.append(request)
            raise urllib.error.URLError("timed out")

        _consumer, created = self._consumer(max_repeats=2)
        worker = self._worker(runner)

        self.assertEqual(1, worker.run_once())
        row = self._delivery(created["initial_delivery_id"])
        self.assertEqual("queued", row["status"])
        self.assertEqual(1, row["attempt"])

        # The retry becomes due immediately in this test.
        self.center._connection.execute(
            "UPDATE deliveries SET due_at = 0 WHERE id = ?", (created["initial_delivery_id"],))
        self.center._connection.commit()

        self.assertEqual(1, worker.run_once())
        row = self._delivery(created["initial_delivery_id"])
        self.assertEqual("failed", row["status"])
        self.assertEqual(2, row["attempt"])
        self.assertEqual(2, len(calls))

        # The ceiling holds: a third tick sends nothing.
        self.center._connection.execute(
            "UPDATE deliveries SET due_at = 0 WHERE id = ?", (created["initial_delivery_id"],))
        self.center._connection.commit()
        self.assertEqual(0, worker.run_once())
        self.assertEqual(2, len(calls))

    def test_failed_matrix_step_of_a_matrix_only_consumer_never_reaches_the_owner(self) -> None:
        class Response:
            status = 403

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read() -> bytes:
                return b'{"errcode":"M_FORBIDDEN"}'

        def runner(request, *, timeout):
            return Response()

        _consumer, created = self._consumer()
        worker = self._worker(runner)
        self.assertEqual(1, worker.run_once())

        incident_id = created["incident_id"]
        rows = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id = ?", (incident_id,)
        ).fetchall()
        channels = {str(row["channel"]) for row in rows}
        self.assertEqual({"matrix.message"}, channels)
        self.assertNotIn("telegram.main", channels)
        self.assertFalse([name for name in channels if name.endswith(".call")])
        self.assertEqual([], [row for row in rows if row["status"] not in {"failed", "sent", "cancelled"}])

    def test_a_definitive_refusal_is_not_a_transient_retry(self) -> None:
        class Response:
            status = 429

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read() -> bytes:
                return b'{"errcode":"M_LIMIT_EXCEEDED"}'

        def runner(request, *, timeout):
            return Response()

        _consumer, created = self._consumer(max_repeats=3)
        worker = self._worker(runner)
        self.assertEqual(1, worker.run_once())
        row = self._delivery(created["initial_delivery_id"])
        self.assertEqual("queued", row["status"])
        self.assertEqual(1, row["attempt"])


if __name__ == "__main__":
    unittest.main()
