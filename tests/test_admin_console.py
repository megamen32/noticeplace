"""Contract tests for the SSO-gated NoticePlace operator console."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.admin import AdminConfigStore
from notification_center.admin_http import build_admin_handler
from notification_center.core import NotificationCenter


class AdminConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.primary = root / "notification-center.env"
        self.routes = root / "routes.env"
        self.database = root / "notify-center.sqlite3"
        self.primary.write_text(f'NOTIFY_CENTER_DB={self.database}\nNOTIFY_CENTER_TOKENS_JSON={{"old-token":{{"project":"existing","max_severity":"notice"}}}}\nOTHER=unchanged\n', encoding="utf-8")
        self.routes.write_text("TELEGRAM_SEVERITY_ROUTES_JSON={}\n", encoding="utf-8")
        self.restarts = 0
        self.calls_override = root / "notification-center-calls.conf"
        self.store = AdminConfigStore(self.primary, self.routes, root / "state", restart=self._restart, calls_override_path=self.calls_override, daemon_reload=lambda: None)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_admin_handler(self.store, "test-csrf-secret"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tempdir.cleanup()

    def _restart(self) -> None:
        self.restarts += 1

    def _request(self, method: str, path: str, form: dict[str, str] | None = None, *, admin: bool = True) -> tuple[int, bytes]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
        if admin:
            headers["X-Notify-Admin"] = "1"
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_sso_header_gate_csrf_and_one_time_token_display(self) -> None:
        status, _ = self._request("GET", "/admin/", admin=False)
        self.assertEqual(403, status)
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b"adapter-steps", page)
        self.assertIn(b"add-adapter-step", page)
        self.assertNotIn(b"textarea name=\"policy_json\"", page)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

        status, rejected = self._request("POST", "/admin/projects", {"project": "service-a", "max_severity": "important", "csrf": "wrong"})
        self.assertEqual(400, status)
        self.assertIn(b"Configuration rejected", rejected)

        status, token_page = self._request("POST", "/admin/projects", {"project": "service-a", "max_severity": "important", "csrf": csrf})
        self.assertEqual(200, status)
        token = re.search(rb'<code>([^<]+)</code>', token_page).group(1).decode()
        self.assertGreater(len(token), 30)
        self.assertIn(b"curl", token_page)
        self.assertIn(b"EnvironmentFile", token_page)
        self.assertIn(b"notify_center_client", token_page)
        self.assertEqual(1, self.restarts)

        snapshot = self.store.snapshot()
        created = next(item for item in snapshot["projects"] if item["project"] == "service-a")
        self.assertEqual("important", created["max_severity"])
        self.assertNotIn(token.encode(), json.dumps(snapshot).encode())
        self.assertNotIn(token, self.store.audit_path.read_text(encoding="utf-8"))

    def test_routes_are_validated_and_persisted_with_a_restart(self) -> None:
        self.store.set_routes({"critical": {"chat_id": "-100123", "message_thread_id": 42}}, "sso:operator")
        self.assertEqual(1, self.restarts)
        self.assertEqual({"chat_id": "-100123", "message_thread_id": 42}, self.store.snapshot()["routes"]["critical"])

    def test_operator_can_toggle_automatic_call_escalation(self) -> None:
        self.assertTrue(self.store.snapshot()["automatic_calls_enabled"])
        self.store.set_automatic_calls(False, "sso:operator")
        self.assertFalse(self.store.snapshot()["automatic_calls_enabled"])
        self.assertFalse(self.calls_override.exists())
        self.store.set_automatic_calls(True, "sso:operator")
        self.assertTrue(self.store.snapshot()["automatic_calls_enabled"])
        self.assertFalse(self.calls_override.exists())
        self.assertEqual(0, self.restarts)

    def test_operator_can_change_live_delivery_timers_without_restart(self) -> None:
        values = {
            "matrix_call_critical_escalation_seconds": "91",
            "matrix_call_emergency_escalation_seconds": "31",
            "android_phone_call_escalation_seconds": "601",
            "android_telegram_call_escalation_seconds": "41",
            "telegram_critical_repeat_seconds": "121",
        }
        self.store.set_runtime_settings(values, "sso:operator")
        self.assertEqual(values, self.store.snapshot()["runtime_settings"])
        self.assertEqual(0, self.restarts)

    def test_preset_and_custom_topics_share_the_same_live_editor(self) -> None:
        self.store._consumer_notification_center().set_runtime_setting("telegram_topics_json", json.dumps({
            "emergency": {"name": "Emergency", "chat_id": "-1001", "message_thread_id": 7, "enabled": True},
        }))
        updated = self.store.save_topic("emergency", "Critical emergency", "-1001", "8", True, "sso:operator")
        created = self.store.save_topic("new-topic", "Deployments", "-1001", "9", True, "sso:operator")
        self.assertEqual("Critical emergency", next(item for item in self.store.topics() if item["id"] == "emergency")["name"])
        self.assertEqual("deployments", created["id"])
        self.store.delete_topic("emergency", "sso:operator")
        self.assertNotIn("emergency", {item["id"] for item in self.store.topics()})
        self.assertEqual(0, self.restarts)

    def test_consumer_form_reveals_intake_url_and_token_once(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

        status, token_page = self._request("POST", "/admin/consumers", {
            "csrf": csrf,
            "project": "hermes",
            "name": "Gateway producer",
            "max_severity": "critical",
            "chat_id": "-100123",
            "topic_id": "42",
            "matrix_delay_seconds": "120",
            "phone_delay_seconds": "600",
        })
        self.assertEqual(200, status)
        token = re.search(rb'<code>(nct_[^<]+)</code>', token_page).group(1).decode()
        self.assertIn(b"https://notify.bezrabotnyi.com/v1/events", token_page)
        self.assertIn(token.encode(), token_page)

        snapshot = self.store.snapshot()
        consumer = snapshot["consumers"][0]
        self.assertEqual("Gateway producer", consumer["name"])
        self.assertEqual(["telegram", "matrix", "phone"], [stage["kind"] for stage in consumer["policy"] if stage["enabled"]])
        self.assertIn(b"Matrix", page)
        self.assertNotIn(token, json.dumps(snapshot))
        reopened = NotificationCenter(self.database, {"old-token": {"project": "existing", "max_severity": "notice"}})
        self.assertNotIn("intake_token", reopened.get_consumer(consumer["id"]))


if __name__ == "__main__":
    unittest.main()
