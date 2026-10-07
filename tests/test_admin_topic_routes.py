"""The live route EnvironmentFile owns topic auto-creation settings."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from notification_center.admin import AdminConfigStore
from notification_center.core import ValidationError


class AdminTopicRouteTests(unittest.TestCase):
    def exercise(self, primary_flag, routes_flag):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "main.env"
            routes = root / "routes.env"
            primary.write_text("TELEGRAM_BOT_TOKEN=fixture\n" + primary_flag)
            routes.write_text(routes_flag)
            store = AdminConfigStore(primary, routes, root / "admin")
            values = {}
            center = MagicMock()
            center.get_runtime_setting.side_effect = values.get
            center.set_runtime_setting.side_effect = values.__setitem__
            with patch.object(store, "_consumer_notification_center", return_value=center), patch.object(store, "_audit"), patch("notification_center.admin.telegram_create_forum_topic", return_value=900) as create:
                result = store.save_topic("agent-herder", "Agent Herder", "-1001", "", True, "test")
                create.assert_called_once_with("fixture", "-1001", "Agent Herder")
                self.assertEqual(900, result["message_thread_id"])
                self.assertIn("agent-herder", json.loads(values["telegram_topics_json"]))

    def test_routes_file_enables_creation_without_primary_flag(self):
        self.exercise("", "TELEGRAM_AUTO_CREATE_TOPICS=true\n")

    def test_explicit_routes_disable_overrides_primary_enable(self):
        with self.assertRaises(ValidationError):
            self.exercise("TELEGRAM_AUTO_CREATE_TOPICS=true\n", "TELEGRAM_AUTO_CREATE_TOPICS=false\n")


if __name__ == "__main__":
    unittest.main()
