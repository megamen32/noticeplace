from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


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
