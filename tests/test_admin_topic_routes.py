"""The live route EnvironmentFile owns topic auto-creation settings."""
import json
import importlib.util
import io
from importlib.machinery import SourceFileLoader
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from contextlib import redirect_stdout
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from notification_center.admin import AdminConfigStore
from notification_center.admin_http import _csrf, build_admin_handler
from notification_center.core import ValidationError
from notification_center.http_api import telegram_destination

CLI_PATH = Path(__file__).resolve().parents[1] / "bin" / "notice-human-request-route"
CLI_SPEC = importlib.util.spec_from_loader("notice_human_request_route", SourceFileLoader("notice_human_request_route", str(CLI_PATH)))
CLI = importlib.util.module_from_spec(CLI_SPEC)
assert CLI_SPEC.loader is not None
CLI_SPEC.loader.exec_module(CLI)


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

    def test_human_request_chat_only_route_is_separate_from_project_event_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "main.env"
            routes = root / "routes.env"
            primary.write_text("TELEGRAM_BOT_TOKEN=fixture\n")
            routes.write_text("")
            store = AdminConfigStore(primary, routes, root / "admin")
            values = {}
            center = MagicMock()
            center.get_runtime_setting.side_effect = values.get
            center.set_runtime_setting.side_effect = values.__setitem__
            values["telegram_topics_json"] = json.dumps({
                "agent-herder": {"name": "Agent Herder", "chat_id": "-1001", "message_thread_id": 900, "enabled": True},
            })
            with patch.object(store, "_consumer_notification_center", return_value=center), patch.object(store, "_audit"):
                result = store.save_human_request_route("noticeplace-qa-stop-20261023b", "-1004308060380", "", True, True, "test")
            self.assertTrue(result["chat_only"])
            self.assertEqual("-1004308060380", result["chat_id"])
            self.assertEqual({
                "noticeplace-qa-stop-20261023b": {
                    "chat_id": "-1004308060380", "chat_only": True, "enabled": True,
                },
            }, json.loads(values["human_request_telegram_routes_json"]))
            original_topics = json.loads(values["telegram_topics_json"])
            self.assertEqual(900, original_topics["agent-herder"]["message_thread_id"])
            self.assertEqual(
                {"chat_id": "-1001", "message_thread_id": "900"},
                telegram_destination("42", original_topics, {"project": "agent-herder", "kind": "incident", "severity": "critical", "event_type": "health.degraded"}, {"health"}),
            )

    def test_chat_only_human_request_route_rejects_a_topic_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "main.env"
            routes = root / "routes.env"
            primary.write_text("")
            routes.write_text("")
            store = AdminConfigStore(primary, routes, root / "admin")
            with patch.object(store, "_consumer_notification_center", return_value=MagicMock()):
                with self.assertRaisesRegex(ValidationError, "cannot include a topic id"):
                    store.save_human_request_route("noticeplace-qa-stop-20261023b", "-1004308060380", "900", True, True, "test")

    def test_unchecked_admin_enabled_checkbox_saves_disabled_human_request_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "main.env"
            routes = root / "routes.env"
            primary.write_text("")
            routes.write_text("")
            store = AdminConfigStore(primary, routes, root / "admin")
            values = {}
            center = MagicMock()
            center.get_runtime_setting.side_effect = values.get
            center.set_runtime_setting.side_effect = values.__setitem__
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_admin_handler(store, "test-csrf-secret"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with patch.object(store, "_consumer_notification_center", return_value=center), patch.object(store, "_audit"):
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/admin/human-request-routes/save",
                    data=urlencode({
                        "csrf": _csrf("test-csrf-secret"),
                        "project": "noticeplace-qa-stop-20261023b",
                        "chat_id": "-1004308060380",
                        "chat_only": "true",
                    }).encode(),
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded", "X-Notify-Admin": "1"},
                )
                try:
                    with urlopen(request, timeout=2) as response:
                        self.assertEqual(200, response.status)
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()
            self.assertFalse(json.loads(values["human_request_telegram_routes_json"])["noticeplace-qa-stop-20261023b"]["enabled"])

    def test_cli_saves_chat_only_destination_through_admin_config_store(self):
        store = MagicMock()
        store.save_human_request_route.return_value = {
            "project": "noticeplace-qa-stop-20261023b", "chat_id": "-1004308060380",
            "chat_only": True, "enabled": True,
        }
        output = io.StringIO()
        with patch.object(CLI, "_store", return_value=store), redirect_stdout(output):
            result = CLI.main([
                "save", "--project", "noticeplace-qa-stop-20261023b",
                "--chat-id=-1004308060380", "--chat-only",
            ])
        self.assertEqual(0, result)
        call = store.save_human_request_route.call_args.args
        self.assertEqual(("noticeplace-qa-stop-20261023b", "-1004308060380", "", True, True), call[:5])
        self.assertTrue(call[5].startswith("cli:"))
        receipt = json.loads(output.getvalue())
        self.assertEqual("saved", receipt["status"])
        self.assertEqual("-1004308060380", receipt["chat_id"])

    def test_cli_delete_uses_audited_admin_config_store(self):
        store = MagicMock()
        output = io.StringIO()
        with patch.object(CLI, "_store", return_value=store), redirect_stdout(output):
            result = CLI.main(["delete", "--project", "noticeplace-qa-stop-20261023b"])
        self.assertEqual(0, result)
        self.assertEqual("noticeplace-qa-stop-20261023b", store.delete_human_request_route.call_args.args[0])
        self.assertTrue(store.delete_human_request_route.call_args.args[1].startswith("cli:"))
        self.assertEqual({"project": "noticeplace-qa-stop-20261023b", "status": "deleted"}, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
