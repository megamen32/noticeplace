"""HTTP contract tests for the dependency-free notification-center API."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import build_handler


class HttpApiTests(unittest.TestCase):
    """Run real loopback requests without a reverse proxy or Telegram credentials."""

    def setUp(self) -> None:
        """Start an isolated API server with separate producer and probe tokens."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"secret-token": {"project": "hermes", "max_severity": "critical"}})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        """Stop the loopback server and its temporary state."""
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.tempdir.cleanup()

    def request(self, method: str, path: str, body: dict[str, object] | None = None, **headers: str) -> tuple[int, dict[str, object]]:
        """Issue one local JSON request, preserving expected HTTP error statuses."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_and_event_require_separate_bearer_credentials(self) -> None:
        """Protect readiness separately while rejecting an unauthenticated producer."""
        status, response = self.request("GET", "/health")
        self.assertEqual(401, status)
        self.assertEqual({"error": "unauthorized"}, response)

        status, health = self.request("GET", "/health", Authorization="Bearer health-token")
        self.assertEqual(200, status)
        self.assertEqual("notify.health.v1", health["schema"])
        self.assertEqual("notification-center", health["service"])
        self.assertTrue(health["dispatcher_ready"])
        self.assertNotIn("secret-token", json.dumps(health))
        self.assertNotIn("health-token", json.dumps(health))

        status, response = self.request("POST", "/v1/events", {"project": "hermes"})
        self.assertEqual(401, status)
        self.assertIn("error", response)

    def test_event_ack_and_resolve_follow_the_public_contract(self) -> None:
        """Create a durable incident, ACK it, and resolve it through HTTP."""
        event = {"schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident", "severity": "critical", "title": "Hermes unavailable", "dedup_key": "hermes:gateway", "ack": {"required": True}}
        headers = {"Authorization": "Bearer secret-token", "Idempotency-Key": "http-event-1"}
        status, created = self.request("POST", "/v1/events", event, **headers)
        self.assertEqual(202, status)
        incident_id = str(created["incident_id"])
        status, acknowledged = self.request("POST", f"/v1/incidents/{incident_id}/ack", {"actor": "telegram:42"}, Authorization="Bearer secret-token")
        self.assertEqual(200, status)
        self.assertEqual("acknowledged", acknowledged["state"])
        status, resolved = self.request("POST", f"/v1/incidents/{incident_id}/resolve", {"actor": "producer"}, Authorization="Bearer secret-token")
        self.assertEqual(200, status)
        self.assertEqual("resolved", resolved["state"])

        status, _ = self.request("POST", f"/v1/incidents/{incident_id}/ack", {"actor": "attacker"}, Authorization="Bearer wrong-token")
        self.assertEqual(401, status)

    def test_reused_idempotency_key_with_different_payload_returns_conflict(self) -> None:
        """Expose accidental producer key reuse as an actionable HTTP 409."""
        event = {"schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident", "severity": "critical", "title": "Hermes unavailable", "dedup_key": "hermes:gateway"}
        headers = {"Authorization": "Bearer secret-token", "Idempotency-Key": "http-event-1"}
        self.assertEqual(202, self.request("POST", "/v1/events", event, **headers)[0])
        changed = {**event, "title": "Different incident content"}
        status, response = self.request("POST", "/v1/events", changed, **headers)
        self.assertEqual(409, status)
        self.assertIn("Idempotency-Key", str(response["error"]))


if __name__ == "__main__":
    unittest.main()
