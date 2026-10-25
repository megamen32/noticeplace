"""The read-only NoticePlace context API for a person-facing caller.

A caller that answers a person about incidents reads the context over the
authenticated API of NoticePlace, never over its storage. The bearer bounds
the projects; the audiences are the owner's own and a caller never widens them.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from notification_center.core import NotificationCenter


class ContextApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"owner-token": {"project": "*", "max_severity": "emergency"},
             "one-project": {"project": "hermes", "max_severity": "emergency"}},
            default_quiet_hours=[])
        self.event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Gateway unavailable",
            "body": "three checks failed", "dedup_key": "gateway:100",
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()

    def _handler(self):
        center = self.center

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                from notification_center.http_api import _bearer
                path, _, query = self.path.partition("?")
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                token = _bearer(self)
                try:
                    if path == "/v1/incidents":
                        project = params.get("project", "*")
                        center.authorize_project(token, project)
                        payload = center.list_incidents_for_context(
                            project, ("me", "health"), params.get("query", ""),
                            include_resolved=params.get("include_resolved") == "1",
                            limit=int(params.get("limit", "20")),
                        )
                    elif path.startswith("/v1/incidents/"):
                        incident_id = path.rsplit("/", 1)[-1]
                        center.authorize_incident(token, incident_id)
                        incident = center.get_incident(incident_id)
                        if incident is None:
                            self.send_response(404); self.end_headers(); self.wfile.write(b'{"error":"not found"}'); return
                        incident.update(center.incident_session_binding(incident_id))
                        payload = incident
                    else:
                        self.send_response(404); self.end_headers(); return
                except Exception as error:  # noqa: BLE001 - test double
                    self.send_response(401); self.end_headers(); self.wfile.write(json.dumps({"error": str(error)}).encode()); return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def _get(self, path: str, token: str = "owner-token"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}{path}",
            headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_the_context_list_serves_the_owner_audiences(self) -> None:
        self.center.create_event("one-project", "key-1", dict(self.event))
        self.center.create_event("one-project", "key-2", {
            **self.event, "recipient": "health", "dedup_key": "gateway:health"})

        status, payload = self._get("/v1/incidents?project=*")
        self.assertEqual(200, status)
        self.assertEqual("noticeplace.incident.context.v1", payload["schema"])
        self.assertEqual(["me", "health"], payload["audiences"])
        self.assertEqual(2, payload["returned"])
        self.assertEqual(2, payload["total_in_scope"])
        self.assertEqual({"Gateway unavailable"}, {item["title"] for item in payload["incidents"]})

    def test_a_foreign_project_token_cannot_read_another_project(self) -> None:
        self.center.create_event("one-project", "key-1", dict(self.event))
        status, payload = self._get("/v1/incidents?project=*", token="one-project")
        self.assertEqual(401, status)
        self.assertIn("error", payload)

    def test_the_query_filter_matches_title_and_body(self) -> None:
        self.center.create_event("one-project", "key-1", dict(self.event))
        self.center.create_event("one-project", "key-2", {
            **self.event, "title": "Disk almost full", "body": "free space left", "dedup_key": "disk:100"})

        status, payload = self._get("/v1/incidents?project=*&query=disk")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["returned"])
        self.assertEqual("Disk almost full", payload["incidents"][0]["title"])

    def test_a_resolved_incident_is_only_listed_when_asked(self) -> None:
        created = self.center.create_event("one-project", "key-1", dict(self.event))
        self.center.resolve_event("one-project", "key-1", {
            "schema": "notify.event.v1", "action": "resolve", "project": "hermes",
            "recipient": "me", "kind": "incident", "severity": "critical",
            "title": "Gateway unavailable", "body": "closed", "dedup_key": "gateway:100"})
        self.assertTrue(created.get("incident_id"))
        status, payload = self._get("/v1/incidents?project=*")
        self.assertEqual(200, status)
        self.assertEqual(0, payload["returned"])

    def test_the_incident_context_names_only_a_linked_session(self) -> None:
        created = self.center.create_event("one-project", "key-1", dict(self.event))
        incident_id = created["incident_id"]
        status, payload = self._get(f"/v1/incidents/{incident_id}")
        self.assertEqual(200, status)
        self.assertEqual(incident_id, payload["id"])
        self.assertEqual(False, payload["has_session"])
        self.assertEqual("", payload["session_id"])

        self.center._connection.execute(
            "INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at, event_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("session-key", "evt-session", incident_id,
             json.dumps({"harness": "codex", "session_id": "01a1653b-d774"}), 1.0,
             "health.diagnosis_session_started"))
        self.center._connection.commit()
        status, payload = self._get(f"/v1/incidents/{incident_id}")
        self.assertEqual(200, status)
        self.assertEqual(True, payload["has_session"])
        self.assertEqual("01a1653b-d774", payload["session_id"])
        self.assertEqual("codex", payload["session_harness"])


if __name__ == "__main__":
    unittest.main()
