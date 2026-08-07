"""Contract tests for the SSO-gated Notify Center operator console."""

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
        self.store = AdminConfigStore(self.primary, self.routes, root / "state", restart=self._restart)
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
