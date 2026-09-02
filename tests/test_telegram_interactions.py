from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller
from notification_center.http_api import telegram_inbox_sink_from_environment, telegram_inline_keyboard


class TelegramInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "critical"}})
        self.created = self.center.create_event("producer", "create", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident", "severity": "critical", "title": "Outage", "body": "details", "dedup_key": "telegram-controls",
        })

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_signed_callback_acknowledges_once_and_rejects_a_tampered_action(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        calls: list[tuple[str, dict[str, object]]] = []
        callback = {"id": "cb_1", "from": {"id": 42}, "data": codec.encode("ack", self.created["incident_id"])}
        updates = [{"update_id": 10, "callback_query": callback}, {"update_id": 11, "callback_query": {**callback, "id": "cb_2", "data": codec.encode("ack", self.created["incident_id"]).replace("ack", "ask", 1)}}]

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api)
        self.assertEqual(2, poller.poll_once())
        self.assertEqual("acknowledged", self.center.get_incident(self.created["incident_id"])["state"])
        self.assertEqual(2, len([method for method, _payload in calls if method == "answerCallbackQuery"]))
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([0, 12], [payload["offset"] for method, payload in calls if method == "getUpdates"])

    def test_ask_command_is_audited_but_not_executed(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        question = "Should I restart the worker?"
        update = {"update_id": 20, "message": {"from": {"id": 42}, "chat": {"id": 42}, "text": f"/ask {self.created['incident_id']} {question}"}}

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api).poll_once()
        events = self.center._connection.execute("SELECT type, payload_json FROM audit_events WHERE incident_id = ? ORDER BY created_at", (self.created["incident_id"],)).fetchall()
        self.assertIn("telegram_ask_recorded", [row["type"] for row in events])
        self.assertEqual("open", self.center.get_incident(self.created["incident_id"])["state"])

    def test_ai_button_schedules_one_manual_diagnosis_job(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        keyboard = telegram_inline_keyboard(codec, self.center.get_incident(self.created["incident_id"]))
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        ai_button = next(button for button in buttons if button["text"] == "AI")
        updates = [{
            "update_id": 21,
            "callback_query": {"id": "cb_ai", "from": {"id": 42}, "data": ai_button["callback_data"]},
        }]

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api).poll_once()
        repeated = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        rows = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis'",
            (self.created["incident_id"],),
        ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertTrue(repeated["idempotent"])

    def test_ai_button_restarts_a_failed_diagnosis_delivery(self) -> None:
        first = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        self.center.complete_delivery(first["agent_job_delivery_id"], "failed", "temporary downstream failure")

        restarted = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        rows = self.center._connection.execute(
            "SELECT id, status, last_error FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis' ORDER BY created_at, id",
            (self.created["incident_id"],),
        ).fetchall()

        self.assertFalse(restarted["idempotent"])
        self.assertTrue(restarted["restarted"])
        self.assertNotEqual(first["agent_job_delivery_id"], restarted["agent_job_delivery_id"])
        self.assertEqual(["failed", "queued"], [row["status"] for row in rows])
        self.assertIsNone(rows[1]["last_error"])

    def test_allowed_regular_message_is_forwarded_to_universal_inbox_once(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 30,
            "message": {
                "message_id": 50,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "route me",
            },
        }
        forwarded = []

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        def inbox_sink(payload: dict[str, object]) -> dict[str, object]:
            forwarded.append(payload)
            return {"event_id": "evt_1", "delivery_id": "dlv_1"}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=api,
            inbox_sink=inbox_sink,
            inbox_chat_ids={"-1001"},
        )

        self.assertEqual(1, poller.poll_once())
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([{
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "-1001:50",
            "sender": "42",
            "body": "route me",
        }], forwarded)

    def test_inbox_sender_allowlist_is_independent_from_callback_allowlist(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 33,
            "message": {
                "message_id": 53,
                "from": {"id": 99, "is_bot": False},
                "chat": {"id": -1001},
                "text": "relay me",
            },
        }
        forwarded = []

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=lambda method, _payload: {"ok": True, "result": [update] if method == "getUpdates" else True},
            inbox_sink=lambda payload: forwarded.append(payload),
            inbox_chat_ids={"-1001"},
            inbox_sender_ids={"99"},
        )

        self.assertEqual(1, poller.poll_once())
        self.assertEqual("99", forwarded[0]["sender"])

    def test_bot_or_unallowlisted_message_is_not_forwarded_to_universal_inbox(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        updates = [
            {"update_id": 31, "message": {"message_id": 51, "from": {"id": 42, "is_bot": True}, "chat": {"id": -1001}, "text": "bot"}},
            {"update_id": 32, "message": {"message_id": 52, "from": {"id": 42}, "chat": {"id": -1002}, "text": "other"}},
        ]
        forwarded = []

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=lambda method, _payload: {"ok": True, "result": updates if method == "getUpdates" else True},
            inbox_sink=lambda payload: forwarded.append(payload),
            inbox_chat_ids={"-1001"},
        )

        self.assertEqual(2, poller.poll_once())
        self.assertEqual([], forwarded)

    def test_configured_inbox_sink_uses_fixed_operator_url_and_bearer(self) -> None:
        requests = []

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read() -> bytes:
                return b'{"event_id":"evt_1","delivery_id":"dlv_1"}'

        def runner(request, *, timeout):
            requests.append((request, timeout))
            return Response()

        sink = telegram_inbox_sink_from_environment({
            "UNIVERSAL_INBOX_INGRESS_URL": "http://127.0.0.1:8092/v1/inbox/messages",
            "UNIVERSAL_INBOX_INGRESS_TOKEN": "inbox-token",
        }, runner=runner)

        self.assertIsNotNone(sink)
        result = sink({
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "-1001:50",
            "sender": "42",
            "body": "route me",
        })

        self.assertEqual("evt_1", result["event_id"])
        self.assertEqual("Bearer inbox-token", requests[0][0].get_header("Authorization"))
        self.assertEqual("http://127.0.0.1:8092/v1/inbox/messages", requests[0][0].full_url)
        self.assertEqual("route me", json.loads(requests[0][0].data)["body"])

    def test_failed_inbox_sink_retries_same_update_before_advancing_offset(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 40,
            "message": {
                "message_id": 60,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "retry me",
            },
        }
        offsets = []
        deliveries = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method != "getUpdates":
                return {"ok": True, "result": True}
            offsets.append(payload["offset"])
            return {"ok": True, "result": [] if int(payload["offset"]) > 40 else [update]}

        def inbox_sink(payload: dict[str, object]) -> dict[str, object]:
            if not deliveries:
                deliveries.append("failed")
                raise RuntimeError("Inbox temporarily unavailable")
            deliveries.append(payload)
            return {"event_id": "evt_1", "delivery_id": "dlv_1"}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=api,
            inbox_sink=inbox_sink,
            inbox_chat_ids={"-1001"},
        )

        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            poller.poll_once()
        self.assertEqual(1, poller.poll_once())
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([0, 0, 41], offsets)
        self.assertEqual(2, len(deliveries))
        self.assertEqual("retry me", deliveries[1]["body"])
