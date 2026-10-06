"""Manual ordinary-incident AI flow through the durable queue and real sender."""
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter, ValidationError
from notification_center.health_workflow import HealthWorkflow
from notification_center.http_api import TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec


class ManualAiCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name) / "state.db",
            {"producer": {"project": "nginx-dev", "max_severity": "important"}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.created = self.center.create_event("producer", "create", {
            "schema": "notify.event.v1", "project": "nginx-dev", "recipient": "me",
            "kind": "incident", "severity": "important", "title": "Синхронизация остановилась",
            "body": "Работающий nginx продолжает обслуживать сайты.", "dedup_key": "sync"})
        self.incident = self.created["incident_id"]
        self.card = {"message_id": 12, "chat": {"id": -100123}, "text": "⚠️ Синхронизация остановилась",
                     "entities": [{"type": "bold", "offset": 3, "length": 26}]}
        self.center.complete_delivery(self.created["initial_delivery_id"], "sent",
            result={"chat_id": "-100123", "message_id": 12})

    def request_ai(self):
        return self.center.apply_telegram_action(self.incident, "ai", "telegram:42", self.card)

    def session(self, key="session", stage="health-diagnosis"):
        result = self.center.record_health_agent_session(self.incident, key, "", "codex", key,
            "https://agent.bezrabotnyi.com", stage=stage)
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE id=?", (result["session_delivery_id"],)).fetchone()
        return self.center.delivery_payload(dict(row))

    def send(self, payload, edit_plans=False):
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok":true,"result":{"message_id":12,"chat":{"id":-100123}}}'
        def open_request(request, **kwargs):
            requests.append((request.full_url, urllib.parse.parse_qs(request.data.decode())))
            return Response()
        sender = TelegramSender("test", "-100123", action_codec=TelegramActionCodec("x" * 32))
        with patch("notification_center.http_api.urllib.request.urlopen", side_effect=open_request):
            receipt = sender.edit_health_card(payload, 12, "-100123") if edit_plans else sender.send(payload)
        self.assertTrue(receipt["edited_in_place"])
        self.assertEqual(12, receipt["message_id"])
        self.assertTrue(requests[0][0].endswith("/editMessageText"))
        return requests[0][1]

    def test_ordinary_incident_session_edits_clicked_card_and_preserves_entities(self):
        self.request_ai()
        payload = self.session()
        request = self.send(payload)
        self.assertEqual(["12"], request["message_id"])
        self.assertIn("✅ Выбрано: решение через AI", request["text"][0])
        self.assertIn(payload["health_session"]["session_url"], request["text"][0])
        entities = json.loads(request["entities"][0])
        self.assertEqual(self.card["entities"][0], entities[0])
        url_entity = entities[-1]
        text_bytes = request["text"][0].encode("utf-16-le")
        linked = text_bytes[url_entity["offset"] * 2:(url_entity["offset"] + url_entity["length"]) * 2].decode("utf-16-le")
        self.assertEqual(payload["health_session"]["session_url"], linked)

    def test_plan_migration_retains_checkmark_and_latest_session_url(self):
        self.request_ai()
        self.session("diagnosis")
        self.session("planner", "health-orchestrator")
        plans = [{"plan_id": plan, "title": plan, "summary": "Проверить состояние", "step": "Проверить"}
                 for plan in ("observe", "repair", "verify")]
        HealthWorkflow(self.center, "x" * 32).attach_plans(self.incident, "plans", plans)
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE channel='telegram.edit' AND status='queued'").fetchone()
        self.assertIsNotNone(row)
        payload = self.center.delivery_payload(dict(row))
        request = self.send(payload, edit_plans=True)
        self.assertIn("✅ Выбрано: решение через AI", request["text"][0])
        self.assertIn("codex%3Aplanner", request["text"][0])
        buttons = json.loads(request["reply_markup"][0])["inline_keyboard"]
        self.assertIn("codex%3Aplanner", buttons[-1][0]["url"])
        self.assertNotIn('AI', [button['text'] for row in buttons for button in row])

    def test_receipt_fallback_registers_manual_session_after_ack(self):
        job = self.request_ai()
        self.center.acknowledge(self.incident, "telegram:42")
        self.center.record_agent_job_result(self.incident, job["agent_job_delivery_id"], "health-diagnosis", {
            "status": "completed", "agent_receipt": {"harness": "codex", "session_id": "fallback-session", "model": "approved-model"}})
        session = self.center.latest_agent_session(self.incident)
        self.assertIn("fallback-session", session["session_url"])
        rows = self.center.claim_due_deliveries(channel_group="message")
        self.assertEqual(1, len(rows))
        self.assertIn("fallback-session", self.send(self.center.delivery_payload(rows[0]))["text"][0])

    def test_plain_incident_without_manual_request_rejects_session(self):
        with self.assertRaises(ValidationError):
            self.session()
