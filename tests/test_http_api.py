"""HTTP contract tests for the dependency-free notification-center API."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import os
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import build_handler
from mcp.notify_mcp import TOOLS as STDIO_TOOLS


class HttpApiTests(unittest.TestCase):
    """Run real loopback requests without a reverse proxy or Telegram credentials."""

    def setUp(self) -> None:
        """Start an isolated API server with separate producer and probe tokens."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {
                "secret-token": {"project": "hermes", "max_severity": "critical", "agent_jobs": ["repair_100", "health-diagnosis"]},
                "notice-token": {"project": "hermes", "max_severity": "notice"},
            },
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health-token", "mcp-token"))
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

    def request_raw(self, path: str) -> tuple[int, str, bytes]:
        """Read a public representation without assuming the JSON API."""
        with urllib.request.urlopen(self.base_url + path, timeout=2) as response:
            return response.status, str(response.headers["Content-Type"]), response.read()

    def test_public_root_redirects_to_protected_admin(self) -> None:
        """Make the hostname useful while keeping the operator UI protected by nginx."""
        request = urllib.request.Request(self.base_url + "/")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args: object, **_kwargs: object) -> None:
                return None

        opener = urllib.request.build_opener(NoRedirect)
        with self.assertRaises(urllib.error.HTTPError) as context:
            opener.open(request, timeout=2)
        self.assertEqual(303, context.exception.code)
        self.assertEqual("/admin/", context.exception.headers.get("Location"))

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

    def test_health_readiness_blocks_reconciliation_states(self) -> None:
        created = self.center.create_event(
            "secret-token",
            "health-readiness-reconciliation",
            {
                "schema": "notify.event.v1",
                "project": "hermes",
                "recipient": "operator",
                "kind": "incident",
                "severity": "critical",
                "event_type": "health.degraded",
                "title": "Health readiness test",
                "body": "send boundary",
                "dedup_key": "health:readiness-reconciliation",
            },
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.reserve_delivery_send(delivery_id, claimed[0]["claimed_at"], claimed[0]["attempt"])
        health = self.center.health()
        self.assertEqual("degraded", health["status"])
        self.assertEqual(1, health["sending_deliveries"])
        self.assertEqual(1, health["reconciliation_required"])

    def test_health_readiness_rejects_sent_card_without_exact_attached_plan_ids(self) -> None:
        created = self.center.create_event(
            "secret-token",
            "health-readiness-invalid-plan-receipt",
            {
                "schema": "notify.event.v1",
                "project": "hermes",
                "recipient": "operator",
                "kind": "incident",
                "severity": "critical",
                "event_type": "health.degraded",
                "title": "Invalid health receipt",
                "body": "plan-set proof is required",
                "dedup_key": "health:readiness-invalid-plan-receipt",
            },
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.complete_delivery(
            delivery_id,
            "sent",
            result={
                "message_id": 91,
                "chat_id": "-100123",
                "health_plan_ids": ["stale-a", "stale-b", "stale-c"],
                "health_button_count": 3,
                "health_signed_callback_count": 3,
            },
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-readiness-invalid-plan-receipt-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        health = self.center.health()
        self.assertEqual("degraded", health["status"])
        self.assertEqual(1, health["reconciliation_required"])

    def test_health_readiness_ignores_non_user_facing_synthetic_legacy_card(self) -> None:
        created = self.center.create_event(
            "secret-token",
            "health-readiness-synthetic-legacy",
            {
                "schema": "notify.event.v1",
                "project": "hermes",
                "recipient": "operator",
                "kind": "incident",
                "severity": "critical",
                "event_type": "health.degraded",
                "title": "Synthetic canary",
                "body": "not user-facing",
                "dedup_key": "health:synthetic:readiness",
                "correlation_id": "corr:live-health-canary:readiness",
            },
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.complete_delivery(delivery_id, "sent")
        health = self.center.health()
        self.assertEqual("ok", health["status"])
        self.assertEqual(0, health["reconciliation_required"])

    def test_health_readiness_blocks_late_queued_telegram_duplicate_after_sent_card(self) -> None:
        created = self.center.create_event(
            "secret-token",
            "health-readiness-late-duplicate",
            {
                "schema": "notify.event.v1",
                "project": "hermes",
                "recipient": "operator",
                "kind": "incident",
                "severity": "critical",
                "event_type": "health.degraded",
                "title": "Late duplicate health card",
                "body": "a second card must block readiness",
                "dedup_key": "health:readiness-late-duplicate",
            },
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.complete_delivery(
            delivery_id,
            "sent",
            result={
                "message_id": 92,
                "chat_id": "-100123",
                "health_plan_ids": ["observe", "repair", "verify"],
                "health_button_count": 3,
                "health_signed_callback_count": 3,
            },
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-readiness-late-duplicate-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        duplicate_id = self.center._schedule_delivery(created["incident_id"], "telegram.consumer:legacy", "late-duplicate", 0)
        self.assertEqual("queued", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (duplicate_id,)
        ).fetchone()["status"])
        health = self.center.health()
        self.assertEqual("degraded", health["status"])
        self.assertEqual(1, health["queued_deliveries"])
        self.assertEqual(1, health["reconciliation_required"])

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

    def test_health_http_vertical_contract_has_one_incident_three_plans_useful_progress_and_verified_resolution(self) -> None:
        signal = {
            "schema": "notify.event.v1",
            "project": "hermes",
            "recipient": "me",
            "kind": "incident",
            "severity": "critical",
            "title": "Disk pressure on host-a",
            "body": "root filesystem is degraded",
            "dedup_key": "health:host-a:disk:fp-1",
            "event_type": "health.degraded",
            "producer": "health-monitor",
            "plugin": "health-incident-monitor",
            "agent_job": "health-diagnosis",
            "correlation_id": "health:host-a:disk:1",
            "source_id": "host:host-a",
            "host_id": "host-a",
            "signal_type": "disk",
            "evidence_refs": ["metric:disk"],
            "health": {"source_id": "host:host-a", "host_id": "host-a", "signal_type": "disk"},
        }
        auth = {"Authorization": "Bearer secret-token"}
        status, created = self.request("POST", "/v1/health/signals", signal, **auth, **{"Idempotency-Key": "health-signal-1"})
        self.assertEqual(202, status)
        incident_id = str(created["incident_id"])
        self.assertEqual(1, len(self.center.list_incidents()))
        self.assertEqual([], self.center.latest_health_plans(incident_id))

        plans = [
            {"plan_id": "observe", "title": "Observe", "summary": "Capture a fresh source snapshot", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the selected reversible repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Independently confirm health", "step": "verify"},
        ]
        status, attached = self.request("POST", f"/v1/incidents/{incident_id}/health/plans", {"plans": plans, "actor": "omniroute"}, **auth, **{"Idempotency-Key": "health-plans-1"})
        self.assertEqual(200, status)
        self.assertEqual(3, len(self.center.latest_health_plans(incident_id)))
        self.assertEqual(incident_id, attached["incident_id"])
        self.assertEqual(1, len(self.center.list_incidents()))
        self.center.record_health_plan_selection(incident_id, "repair", "telegram:42", "health-selection-1")

        status, heartbeat_only = self.request(
            "POST",
            f"/v1/incidents/{incident_id}/health/progress",
            {"plan_id": "repair", "step": "heartbeat", "evidence_refs": [], "progress_fingerprint": "", "heartbeat_at": 123},
            **auth,
            **{"Idempotency-Key": "health-heartbeat-only-1"},
        )
        self.assertEqual(400, status)
        self.assertIn("heartbeat-only", str(heartbeat_only["error"]))
        self.assertEqual(0, self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM events WHERE incident_id = ? AND event_type = 'health.progress'",
            (incident_id,),
        ).fetchone()["count"])

        status, heartbeat_without_timestamp = self.request(
            "POST",
            f"/v1/incidents/{incident_id}/health/progress",
            {"plan_id": "repair", "step": "heartbeat", "evidence_refs": [], "progress_fingerprint": "heartbeat-only"},
            **auth,
            **{"Idempotency-Key": "health-heartbeat-fingerprint-without-timestamp-1"},
        )
        self.assertEqual(400, status)
        self.assertIn("heartbeat-only", str(heartbeat_without_timestamp["error"]))
        self.assertEqual(0, self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM events WHERE incident_id = ? AND event_type = 'health.progress'",
            (incident_id,),
        ).fetchone()["count"])

        status, heartbeat_with_fingerprint = self.request(
            "POST",
            f"/v1/incidents/{incident_id}/health/progress",
            {"plan_id": "repair", "step": "heartbeat", "evidence_refs": [], "progress_fingerprint": "heartbeat-only", "heartbeat_at": 123},
            **auth,
            **{"Idempotency-Key": "health-heartbeat-fingerprint-only-1"},
        )
        self.assertEqual(400, status)
        self.assertIn("heartbeat-only", str(heartbeat_with_fingerprint["error"]))
        self.assertEqual(0, self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM events WHERE incident_id = ? AND event_type = 'health.progress'",
            (incident_id,),
        ).fetchone()["count"])

        progress = {"plan_id": "repair", "step": "capture", "evidence_refs": ["fresh:snapshot"], "progress_fingerprint": "fp-progress", "actor": "herder"}
        status, first_progress = self.request("POST", f"/v1/incidents/{incident_id}/health/progress", progress, **auth, **{"Idempotency-Key": "health-progress-1"})
        self.assertEqual(200, status)
        self.assertTrue(first_progress["useful_progress"])
        status, heartbeat = self.request("POST", f"/v1/incidents/{incident_id}/health/progress", {**progress, "heartbeat_at": 123}, **auth, **{"Idempotency-Key": "health-progress-2"})
        self.assertEqual(200, status)
        self.assertFalse(heartbeat["useful_progress"])

        status, blocked = self.request("POST", f"/v1/incidents/{incident_id}/health/resolve", {"source_id": "host:host-a", "verification_id": "verify-1", "actor": "api"}, **auth, **{"Idempotency-Key": "health-resolve-early"})
        self.assertEqual(400, status)
        self.assertIn("verification", str(blocked["error"]))
        status, verification = self.request("POST", f"/v1/incidents/{incident_id}/health/verification", {"source_id": "host:host-a", "verification_id": "verify-1", "observed_state": "healthy", "evidence_refs": ["independent:probe"], "actor": "probe-b"}, **auth, **{"Idempotency-Key": "health-verification-1"})
        self.assertEqual(200, status)
        self.assertEqual("healthy", verification["observed_state"])
        status, resolved = self.request("POST", f"/v1/incidents/{incident_id}/health/resolve", {"source_id": "host:host-a", "verification_id": "verify-1", "actor": "api", "elapsed_ms": 3210, "trace_refs": ["trace-http-1"]}, **auth, **{"Idempotency-Key": "health-resolve-1"})
        self.assertEqual(200, status)
        self.assertEqual("resolved", resolved["state"])
        self.assertEqual(1, len(self.center.list_incidents()))
        resolution = self.center.latest_health_event(incident_id, "health.resolved")
        self.assertIsNotNone(resolution)
        self.assertEqual("health:host-a:disk:1", resolution["payload"]["correlation_id"])
        self.assertEqual(3210, resolution["payload"]["elapsed_ms"])
        self.assertEqual(["trace-http-1", "metric:disk", "independent:probe"], resolution["payload"]["trace_refs"])

    def test_event_audit_records_profile_and_trusted_ingress_without_auth_header(self) -> None:
        event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Disk warning",
            "body": "root usage high", "dedup_key": "disk:audit", "operator_note": "server-100 / vpn2",
        }
        status, created = self.request(
            "POST", "/v1/events", event,
            Authorization="Bearer secret-token", **{
                "Idempotency-Key": "audit-event",
                "X-Real-IP": "192.0.2.44",
                "X-Forwarded-For": "192.0.2.44, 198.51.100.7",
            },
        )
        self.assertEqual(202, status)
        incident = self.center.get_incident(str(created["incident_id"]))
        self.assertEqual("server-100 / vpn2", incident["operator_note"])
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE incident_id = ? AND type = 'event_ingress'",
            (created["incident_id"],),
        ).fetchone()
        metadata = json.loads(audit["payload_json"])
        self.assertEqual("192.0.2.44", metadata["source_ip"])
        self.assertEqual("127.0.0.1", metadata["proxy_ip"])
        self.assertEqual("192.0.2.44, 198.51.100.7", metadata["forwarded_for"])
        self.assertEqual("profile_log", metadata["profile_id"])
        self.assertNotIn("secret-token", audit["payload_json"])

    def test_reused_idempotency_key_with_different_payload_returns_conflict(self) -> None:
        """Expose accidental producer key reuse as an actionable HTTP 409."""
        event = {"schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident", "severity": "critical", "title": "Hermes unavailable", "dedup_key": "hermes:gateway"}
        headers = {"Authorization": "Bearer secret-token", "Idempotency-Key": "http-event-1"}
        self.assertEqual(202, self.request("POST", "/v1/events", event, **headers)[0])
        changed = {**event, "title": "Different incident content"}
        status, response = self.request("POST", "/v1/events", changed, **headers)
        self.assertEqual(409, status)
        self.assertIn("Idempotency-Key", str(response["error"]))

    def test_producer_can_resolve_an_incident_by_its_stable_dedup_key(self) -> None:
        """Let stateful sources such as Fail2ban close their own earlier alert."""
        event = {
            "schema": "notify.event.v1",
            "project": "hermes",
            "recipient": "me",
            "kind": "incident",
            "severity": "important",
            "title": "Gateway unavailable",
            "dedup_key": "hermes:gateway",
        }
        headers = {"Authorization": "Bearer secret-token", "Idempotency-Key": "http-event-create"}
        self.assertEqual(202, self.request("POST", "/v1/events", event, **headers)[0])

        resolution = {
            "schema": "notify.event.v1",
            "action": "resolve",
            "project": "hermes",
            "recipient": "me",
            "dedup_key": "hermes:gateway",
        }
        headers["Idempotency-Key"] = "http-event-resolve"
        status, response = self.request("POST", "/v1/events", resolution, **headers)
        self.assertEqual(200, status)
        self.assertTrue(response["resolved"])
        self.assertEqual("resolved", response["state"])

        status, repeated = self.request("POST", "/v1/events", resolution, **headers)
        self.assertEqual(200, status)
        self.assertTrue(repeated["idempotent"])

    def test_low_severity_token_cannot_resolve_a_critical_incident(self) -> None:
        """A limited producer must not silence an escalation it could not create."""
        event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Critical outage", "dedup_key": "hermes:critical",
        }
        self.assertEqual(202, self.request("POST", "/v1/events", event, Authorization="Bearer secret-token", **{"Idempotency-Key": "create-critical"})[0])
        resolution = {"schema": "notify.event.v1", "action": "resolve", "project": "hermes", "recipient": "me", "dedup_key": "hermes:critical"}
        status, _ = self.request("POST", "/v1/events", resolution, Authorization="Bearer notice-token", **{"Idempotency-Key": "resolve-critical"})
        self.assertEqual(401, status)

    def test_low_severity_token_cannot_ack_by_incident_id(self) -> None:
        """The incident action API has exactly the same severity boundary."""
        event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Critical outage", "dedup_key": "hermes:critical-action",
        }
        status, created = self.request("POST", "/v1/events", event, Authorization="Bearer secret-token", **{"Idempotency-Key": "create-critical-action"})
        self.assertEqual(202, status)
        status, _ = self.request("POST", f"/v1/incidents/{created['incident_id']}/ack", {"actor": "limited"}, Authorization="Bearer notice-token")
        self.assertEqual(401, status)

    def test_http_agent_job_requires_scope_and_rejects_payload_authority(self) -> None:
        event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Disk pressure",
            "body": "95 percent", "dedup_key": "disk-full:server-100:/", "agent_job": "repair_100",
        }
        status, created = self.request("POST", "/v1/events", event, Authorization="Bearer secret-token", **{"Idempotency-Key": "agent-job-http"})
        self.assertEqual(202, status)
        self.assertTrue(created["agent_job_delivery_id"])

        status, denied = self.request("POST", "/v1/events", {**event, "target": "shell:attacker"}, Authorization="Bearer secret-token", **{"Idempotency-Key": "agent-job-danger"})
        self.assertEqual(400, status)
        self.assertIn("forbidden authority fields", str(denied["error"]))

        status, _ = self.request("POST", "/v1/events", {**event, "dedup_key": "disk-full:unauthorized"}, Authorization="Bearer notice-token", **{"Idempotency-Key": "agent-job-unscoped"})
        self.assertEqual(401, status)

    def test_remote_mcp_requires_bearer_and_matches_stdio_registry(self) -> None:
        """Expose the exact stdio MCP registry over authenticated HTTP on /mcp."""
        status, body = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, Authorization="Bearer wrong-token")
        self.assertEqual(401, status)
        self.assertEqual({"error": "unauthorized"}, body)

        expected_tools = [{"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]} for name, spec in STDIO_TOOLS.items()]
        status, initialize = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, Authorization="Bearer mcp-token")
        self.assertEqual(200, status)
        self.assertEqual("notify-mcp", initialize["result"]["serverInfo"]["name"])
        self.assertEqual("1.2.0", initialize["result"]["serverInfo"]["version"])

        status, listed = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, Authorization="Bearer mcp-token")
        self.assertEqual(200, status)
        self.assertEqual(expected_tools, listed["result"]["tools"])

        status, called = self.request(
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {"limit": 1}}},
            Authorization="Bearer mcp-token",
        )
        self.assertEqual(200, status)
        self.assertEqual(called["result"]["structuredContent"], STDIO_TOOLS["list_jobs"]["handler"]({"limit": 1}))

    def test_remote_mcp_initializes_shared_runtime_before_job_tools(self) -> None:
        """Ensure the shared dispatcher prepares job state before a real job tool runs."""
        fake_notify = Path(self.tempdir.name) / "notify"
        fake_notify.write_text("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(fake_notify, 0o755)

        job_state_dir = Path(self.tempdir.name) / "job-state"
        with mock.patch("mcp.notify_mcp.NOTIFY_BIN", fake_notify), mock.patch("mcp.notify_mcp.STATE_DIR", job_state_dir), mock.patch("mcp.notify_mcp.JOBS_DIR", job_state_dir / "jobs"):
            status, response = self.request(
                "POST",
                "/mcp",
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "run_and_notify", "arguments": {"command": "true", "cwd": self.tempdir.name, "log_mode": "none", "wait_seconds": 0, "hard_timeout": "1s"}}},
                Authorization="Bearer mcp-token",
            )
            local = STDIO_TOOLS["run_and_notify"]["handler"]({"command": "true", "cwd": self.tempdir.name, "log_mode": "none", "wait_seconds": 0, "hard_timeout": "1s"})

        self.assertEqual(200, status)
        self.assertTrue(response["result"]["structuredContent"]["ok"])
        self.assertTrue(response["result"]["structuredContent"]["job_id"])
        jobs_dir = job_state_dir / "jobs"
        self.assertTrue(jobs_dir.exists())
        self.assertTrue(local["ok"])
        self.assertTrue(local["job_id"])
        self.assertEqual(response["result"]["structuredContent"]["ok"], local["ok"])
        self.assertEqual(response["result"]["structuredContent"]["cwd"], local["cwd"])
        self.assertEqual(response["result"]["structuredContent"]["notify_attached"], local["notify_attached"])
        self.assertEqual(response["result"]["structuredContent"]["hard_timeout"], local["hard_timeout"])
        self.assertEqual(response["result"]["structuredContent"]["wait_seconds"], local["wait_seconds"])

    def test_remote_mcp_initialize_then_tool_call_only_effectively_initializes_once(self) -> None:
        """Keep the shared runtime bootstrap process-once across initialize and tool calls."""
        with mock.patch("mcp.notify_mcp._RUNTIME_READY", False), mock.patch("mcp.notify_mcp.ensure_dirs") as ensure_dirs:
            status, initialize = self.request(
                "POST",
                "/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                Authorization="Bearer mcp-token",
            )
            self.assertEqual(200, status)
            self.assertEqual("notify-mcp", initialize["result"]["serverInfo"]["name"])

            status, listed = self.request(
                "POST",
                "/mcp",
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {"limit": 1}}},
                Authorization="Bearer mcp-token",
            )
            self.assertEqual(200, status)
            self.assertEqual(1, len(listed["result"]["structuredContent"]["jobs"]))

        self.assertEqual(1, ensure_dirs.call_count)


if __name__ == "__main__":
    unittest.main()
