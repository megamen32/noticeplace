"""A failing dependency must not loop and must not reach the owner channels.

The Matrix transport of Notice Place answers a definitive refusal (the identity
may not use that room) or a transient failure. A definitive refusal is never
retried, and a generic consumer step never exceeds the attempt ceiling its own
policy states. A failed Matrix step of a Matrix only consumer never becomes a
Telegram card and never becomes a phone call.
"""

from __future__ import annotations

import tempfile
import json
import time
import unittest
import urllib.error
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, MatrixMessageSender


class MatrixSelectedAccountTests(unittest.TestCase):
    def test_selected_account_without_gateway_never_uses_global_identity(self):
        def forbidden(*args, **kwargs):
            self.fail('global Matrix service identity used for selected account')
        sender = MatrixMessageSender('https://service.test', 'service-token', runner=forbidden)
        from notification_center.http_api import MatrixDeliveryRejected
        with self.assertRaises(MatrixDeliveryRejected):
            sender.send({'target':{'account_ref':'matrix:qa-a','room_id':'!qa:test'},
                         'incident':{'title':'QA'}}, 'selected-no-gateway')

    def test_selected_account_uses_userio_exact_draft_instead_of_service_token(self):
        calls = []
        class Response:
            status = 200
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return json.dumps(self.value).encode()
        def runner(request, *, timeout):
            calls.append(request)
            self.assertEqual('http://userio.test/mcp', request.full_url)
            self.assertEqual('Bearer scoped-userio-token', request.get_header('Authorization'))
            params = json.loads(request.data)['params']
            if params['name'] == 'userio.messages.prepare':
                self.assertEqual('matrix:qa-a', params['arguments']['account_ref'])
                self.assertEqual('!qa:test', params['arguments']['identifier'])
                data = {'ok':True,'draft_id':'draft-qa','conversation_id':'conv-qa',
                        'body':params['arguments']['text'],'status':'proposed'}
            else:
                self.assertEqual('userio.messages.send', params['name'])
                args = params['arguments']
                self.assertEqual('draft-qa',args['draft_id'])
                self.assertEqual('conv-qa',args['expected_chat_id'])
                self.assertEqual([],args['expected_attachments'])
                self.assertIs(args['confirm'],True)
                self.assertEqual('QA title\n\nQA body',args['expected_text'])
                data = {'ok':True,'delivery_confirmed':True,'receipt':'matrix:$qa-event'}
            return Response({'result':{'content':[{'type':'text','text':json.dumps(data)}]}})
        sender = MatrixMessageSender('https://service-matrix.test','service-token',runner=runner)
        sender._userio_url = 'http://userio.test/mcp'
        sender._userio_token = 'scoped-userio-token'
        result = sender.send({'target':{'account_ref':'matrix:qa-a','room_id':'!qa:test'},
                              'incident':{'title':'QA title','body':'QA body'}},'stable-qa-delivery')
        self.assertEqual('$qa-event',result['event_id'])
        self.assertEqual('matrix:qa-a',result['account_ref'])
        self.assertEqual(2,len(calls))


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

        def runner(request, *, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

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
        def runner(request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

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

    def test_the_stored_attempt_count_decides_the_ceiling(self) -> None:
        """The stored count decides, not a counter the caller brings along.

        A claim may hand the worker an older copy of its own row, so the
        ceiling is read from the stored row of the delivery.
        """
        _consumer, created = self._consumer(max_repeats=2)
        delivery_id = created["initial_delivery_id"]
        claimed_at = time.time()
        self.center._connection.execute(
            "UPDATE deliveries SET status = 'claimed', claimed_at = ?, attempt = 2 WHERE id = ?",
            (claimed_at, delivery_id))
        self.center._connection.commit()

        self.center.complete_delivery(
            delivery_id, "retry", "dependency refused", 30,
            claimed_at=claimed_at, attempt=2,
        )
        stored = self.center._connection.execute(
            "SELECT status, attempt FROM deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        self.assertEqual("failed", stored["status"])
        self.assertEqual(2, stored["attempt"])

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
