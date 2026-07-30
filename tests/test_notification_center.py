"""Regression tests for the durable notification-center MVP."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.core import AuthorizationError, IdempotencyConflict, NotificationCenter, ValidationError
from notification_center.http_api import build_handler


class NotificationCenterTests(unittest.TestCase):
    """Exercise the incident state machine without external network calls."""

    def setUp(self) -> None:
        """Create a fresh durable store for every test."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer-token": {"project": "hermes", "max_severity": "critical"}})

    def tearDown(self) -> None:
        """Remove isolated SQLite data after every test."""
        self.tempdir.cleanup()

    def event(self, **overrides: object) -> dict[str, object]:
        """Return a valid critical event, adjusted by explicit overrides."""
        event: dict[str, object] = {
            "schema": "notify.event.v1",
            "project": "hermes",
            "recipient": "me",
            "kind": "incident",
            "severity": "critical",
            "title": "Hermes unavailable",
            "body": "Health probe failed three times.",
            "dedup_key": "hermes:gateway:100",
            "ack": {"required": True},
        }
        event.update(overrides)
        return event

    def test_rejects_missing_or_unauthorized_event(self) -> None:
        """Reject bad credentials and incomplete contracts before creating state."""
        with self.assertRaises(AuthorizationError):
            self.center.create_event("wrong", "request-1", self.event())
        with self.assertRaises(ValidationError):
            self.center.create_event("producer-token", "request-1", {"project": "hermes"})
        self.assertEqual([], self.center.list_incidents())

    def test_idempotency_and_deduplication_collapse_repeated_events(self) -> None:
        """Keep HTTP retries and repeated failures on one incident and delivery."""
        first = self.center.create_event("producer-token", "request-1", self.event())
        retry = self.center.create_event("producer-token", "request-1", self.event())
        repeated = self.center.create_event("producer-token", "request-2", self.event())

        self.assertEqual(first["event_id"], retry["event_id"])
        self.assertEqual(first["incident_id"], retry["incident_id"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(first["incident_id"], repeated["incident_id"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(repeated["deduplicated"])
        incident = self.center.get_incident(first["incident_id"])
        self.assertEqual(2, incident["occurrences"])
        self.assertEqual(1, len(self.center.claim_due_deliveries(now_epoch=10**12)))

    def test_rejects_idempotency_key_reused_for_different_event(self) -> None:
        """Reject a dangerous retry-key collision rather than replaying stale content."""
        self.center.create_event("producer-token", "request-1", self.event())
        with self.assertRaises(IdempotencyConflict):
            self.center.create_event("producer-token", "request-1", self.event(title="different"))

    def test_ack_stops_pending_escalation_and_resolve_closes_incident(self) -> None:
        """Treat ACK and resolve as different state transitions with cancellation."""
        created = self.center.create_event("producer-token", "request-1", self.event())
        incident_id = created["incident_id"]
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(incident_id, "matrix.call", due_epoch=100)
        acknowledged = self.center.acknowledge(incident_id, actor="telegram:42")

        self.assertEqual("acknowledged", acknowledged["state"])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=1000))
        with self.assertRaisesRegex(ValidationError, "acknowledged"):
            self.center.schedule_escalation(incident_id, "matrix.call", due_epoch=1001)
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=1001))
        resolved = self.center.resolve(incident_id, actor="producer-token")
        self.assertEqual("resolved", resolved["state"])
        self.assertEqual("resolved", self.center.get_incident(incident_id)["state"])

    def test_snooze_defers_and_then_releases_escalation(self) -> None:
        """Allow explicit snooze without losing a durable escalation."""
        created = self.center.create_event("producer-token", "request-1", self.event())
        incident_id = created["incident_id"]
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        baseline = time.time()
        delivery_id = self.center.schedule_escalation(incident_id, "matrix.call", due_epoch=baseline + 10)
        self.center.snooze(incident_id, until_epoch=baseline + 20, actor="telegram:42")

        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=baseline + 19))
        claimed = self.center.claim_due_deliveries(now_epoch=baseline + 20)
        self.assertEqual([delivery_id], [item["id"] for item in claimed])

    def test_expired_worker_lease_is_reclaimed_after_a_crash(self) -> None:
        """Reclaim stale work so a worker crash cannot strand an incident delivery."""
        created = self.center.create_event("producer-token", "request-lease", self.event(dedup_key="lease"))
        first = self.center.claim_due_deliveries(now_epoch=10**12, lease_seconds=30)
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in first])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=10**12 + 29, lease_seconds=30))
        reclaimed = self.center.claim_due_deliveries(now_epoch=10**12 + 30, lease_seconds=30)
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in reclaimed])
        self.assertEqual(2, reclaimed[0]["attempt"])

    def test_health_reports_storage_without_secrets(self) -> None:
        """Expose a minimal readiness payload suitable for external probes."""
        health = self.center.health()
        self.assertEqual("ok", health["status"])
        self.assertTrue(health["storage_ready"])
        self.assertNotIn("producer-token", json.dumps(health))


class HealthEndpointTests(unittest.TestCase):
    """Exercise the externally published readiness contract over real HTTP."""

    def setUp(self) -> None:
        """Start an isolated stdlib HTTP server with fake probe credentials."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"producer-token": {"project": "hermes", "max_severity": "critical"}},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "probe-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/health"

    def tearDown(self) -> None:
        """Stop the isolated server and remove its temporary SQLite database."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def get_health(self, token: str | None = None) -> tuple[int, dict[str, object]]:
        """Fetch health and return both success and HTTP-error JSON responses."""
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_requires_dedicated_bearer_without_leaking_tokens(self) -> None:
        """Reject absent, wrong, and producer credentials; return only safe readiness."""
        with self.assertRaisesRegex(RuntimeError, "NOTIFY_CENTER_HEALTH_TOKEN"):
            build_handler(self.center, "")

        for token in (None, "wrong-token", "producer-token"):
            status, body = self.get_health(token)
            self.assertEqual(401, status)
            self.assertEqual({"error": "unauthorized"}, body)

        status, body = self.get_health("probe-token")
        self.assertEqual(200, status)
        self.assertEqual("ok", body["status"])
        self.assertNotIn("probe-token", json.dumps(body))
        self.assertNotIn("producer-token", json.dumps(body))

    def test_health_returns_safe_503_when_sqlite_is_unavailable(self) -> None:
        """Convert a failed storage dependency probe into safe degraded JSON."""
        self.center._connection.close()

        status, body = self.get_health("probe-token")

        self.assertEqual(503, status)
        self.assertEqual("degraded", body["status"])
        self.assertFalse(body["storage_ready"])
        self.assertNotIn("closed database", json.dumps(body))

    def test_health_returns_503_when_dispatcher_heartbeat_is_stale(self) -> None:
        """Treat a stuck delivery loop as unready even while SQLite still responds."""
        self.center._dispatcher_heartbeat = 0

        status, body = self.get_health("probe-token")

        self.assertEqual(503, status)
        self.assertEqual("degraded", body["status"])
        self.assertTrue(body["storage_ready"])
        self.assertFalse(body["dispatcher_ready"])


if __name__ == "__main__":
    unittest.main()
