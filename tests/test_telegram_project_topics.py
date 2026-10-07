"""Project-owned forum topics must isolate notifications without changing modes."""
import unittest
import io
import json
from unittest.mock import patch
from urllib.parse import parse_qs

from notification_center.http_api import TelegramSender, telegram_destination


class TelegramProjectTopicTests(unittest.TestCase):
    def setUp(self):
        self.routes = {
            "health": {"chat_id": "-1001", "message_thread_id": 324},
            "log": {"chat_id": "-1001", "message_thread_id": 122},
            "agent-herder": {"chat_id": "-1001", "message_thread_id": 900, "enabled": True},
        }
        self.incident = {"project": "agent-herder", "kind": "incident", "severity": "critical", "event_type": "health.degraded"}

    def test_herder_health_and_log_use_project_topic(self):
        for incident in (self.incident, {"project": "agent-herder", "kind": "log", "severity": "info"}):
            self.assertEqual({"chat_id": "-1001", "message_thread_id": "900"}, telegram_destination("-1001", self.routes, incident, {"health", "log"}))

    def test_other_project_keeps_health_topic(self):
        incident = {**self.incident, "project": "fleet-health"}
        self.assertEqual("324", telegram_destination("-1001", self.routes, incident, {"health"})["message_thread_id"])

    def test_inactive_mode_stays_disabled(self):
        self.assertEqual({}, telegram_destination("-1001", self.routes, self.incident, {"log"}))

    def test_disabled_or_incomplete_project_topic_never_falls_back(self):
        for route in ({"enabled": False}, {"chat_id": "-1001"}, {"message_thread_id": 900}):
            self.assertEqual({}, telegram_destination("-1001", {**self.routes, "agent-herder": route}, self.incident, {"health"}))

    def test_mode_named_project_does_not_override_severity(self):
        incident = {**self.incident, "project": "log"}
        self.assertEqual("324", telegram_destination("-1001", self.routes, incident, {"health"})["message_thread_id"])

    def test_delivery_sends_project_topic_and_retains_actual_telegram_topic(self):
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 901, "chat": {"id": -1001}, "message_thread_id": 900}}).encode())
        response.status = 200
        sender = TelegramSender("fixture", "-1001", severity_routes=self.routes, active_modes={"log"})
        with patch("urllib.request.urlopen", return_value=response) as send:
            receipt = sender.send({"incident": {"id": "test", "project": "agent-herder", "kind": "log", "severity": "info", "title": "Проверка", "body": "Топик"}})
        fields = parse_qs(send.call_args.args[0].data.decode())
        self.assertEqual(["900"], fields["message_thread_id"])
        self.assertEqual(900, receipt["message_thread_id"])


if __name__ == "__main__":
    unittest.main()
