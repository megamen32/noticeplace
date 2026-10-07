"""Project-owned forum topics must isolate notifications without changing modes."""
import unittest

from notification_center.http_api import telegram_destination


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


if __name__ == "__main__":
    unittest.main()
