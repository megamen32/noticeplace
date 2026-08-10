"""End-to-end unit contract for the allowlisted GPTAdmin agent-job channel."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError
from notification_center.health_workflow import HealthWorkflow
from notification_center.gptadmin_agent import GptAdminAgentJobAdapter, HealthProgressSupervisor
from notification_center.http_api import DeliveryWorker, gptadmin_agent_jobs_from_environment


class _Response:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return self._body


class GptAdminAgentJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {
                "allowed": {"project": "infra", "max_severity": "critical", "agent_jobs": ["repair_100"]},
                "plain": {"project": "infra", "max_severity": "critical"},
            },
        )
        self.event = {
            "schema": "notify.event.v1",
            "project": "infra",
            "recipient": "ops",
            "kind": "incident",
            "severity": "critical",
            "title": "Disk pressure on server-100",
            "body": "root filesystem 95 percent",
            "dedup_key": "disk-full:server-100:/",
            "agent_job": "repair_100",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_authorized_event_schedules_one_durable_agent_job_delivery(self) -> None:
        created = self.center.create_event("allowed", "disk-event-1", self.event)
        due = self.center.claim_due_deliveries(now_epoch=10**12)

        self.assertTrue(created["agent_job_delivery_id"])
        self.assertEqual(["gptadmin.agent:repair_100", "telegram.main"], sorted(item["channel"] for item in due))

        repeated = self.center.create_event("allowed", "disk-event-1", self.event)
        self.assertTrue(repeated["idempotent"])
        rows = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM deliveries WHERE channel = 'gptadmin.agent:repair_100'"
        ).fetchone()
        self.assertEqual(1, rows["count"])

        next_event = self.center.create_event("allowed", "disk-event-1-next", {**self.event, "body": "root filesystem 96 percent"})
        self.assertNotEqual(created["agent_job_delivery_id"], next_event["agent_job_delivery_id"])
        rows = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM deliveries WHERE channel = 'gptadmin.agent:repair_100'"
        ).fetchone()
        self.assertEqual(2, rows["count"])

    def test_parallel_duplicate_has_one_event_and_one_agent_delivery(self) -> None:
        def create() -> dict[str, object]:
            return self.center.create_event("allowed", "parallel-disk-event", self.event)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _index: create(), range(2)))
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["agent_job_delivery_id"], second["agent_job_delivery_id"])
        rows = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM deliveries WHERE channel = 'gptadmin.agent:repair_100'"
        ).fetchone()
        self.assertEqual(1, rows["count"])

    def test_health_diagnosis_is_once_per_open_deduplicated_incident(self) -> None:
        center = NotificationCenter(
            Path(self.tempdir.name) / "health.sqlite3",
            {"health-token": {"project": "health-monitor", "max_severity": "critical", "agent_jobs": ["health-diagnosis"]}},
        )
        event = {
            "schema": "notify.event.v1",
            "project": "health-monitor",
            "recipient": "health",
            "kind": "incident",
            "severity": "critical",
            "title": "Synthetic health degradation",
            "body": "first observation",
            "dedup_key": "health:host:server-100:cpu:fingerprint",
            "event_type": "health.degraded",
            "source_id": "host:server-100",
            "host_id": "server-100",
            "signal_type": "cpu",
            "agent_job": "health-diagnosis",
        }
        first = center.create_event("health-token", "health-event-1", event)
        second = center.create_event("health-token", "health-event-2", {**event, "body": "repeat observation"})
        self.assertEqual(first["incident_id"], second["incident_id"])
        self.assertEqual(first["agent_job_delivery_id"], second["agent_job_delivery_id"])
        rows = center._connection.execute(
            "SELECT COUNT(*) AS count FROM deliveries WHERE channel = 'gptadmin.agent:health-diagnosis'"
        ).fetchone()
        self.assertEqual(1, rows["count"])

    def test_crash_reopen_reclaims_same_delivery_key_and_same_hub_job(self) -> None:
        created = self.center.create_event("allowed", "crash-disk-event", self.event)
        claimed = self.center.claim_due_deliveries(now_epoch=1_000_000_000_000, lease_seconds=1)
        first_claim = next(item for item in claimed if item["id"] == created["agent_job_delivery_id"])

        reopened = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"allowed": {"project": "infra", "max_severity": "critical", "agent_jobs": ["repair_100"]}},
        )
        reclaimed = reopened.claim_due_deliveries(now_epoch=1_000_000_000_002, lease_seconds=1)
        second_claim = next(item for item in reclaimed if item["id"] == created["agent_job_delivery_id"])
        self.assertEqual(first_claim["delivery_key"], second_claim["delivery_key"])

        hub_jobs: dict[str, str] = {}
        post_count = 0

        def runner(request: object, **_kwargs: object) -> _Response:
            nonlocal post_count
            if request.get_method() == "POST":
                post_count += 1
                key = str(request.get_header("Idempotency-key"))
                hub_jobs.setdefault(key, "hub-job-crash")
                return _Response(202, {"route_id": "notify-repair-100", "job_id": hub_jobs[key], "status": "accepted"})
            return _Response(200, {"route_id": "notify-repair-100", "job_id": "hub-job-crash", "status": "completed", "result": {"session_id": "codex-1"}})

        adapter = GptAdminAgentJobAdapter(
            "repair_100", "https://gptadmin.example/webhooks/v1/notify-repair-100", "route-secret",
            runner=runner, now=lambda: 1_785_640_000, sleeper=lambda _seconds: None, poll_interval_seconds=0,
        )
        payload = reopened.delivery_payload(second_claim)
        first_result = adapter.send(payload, str(first_claim["delivery_key"]))
        second_result = adapter.send(payload, str(second_claim["delivery_key"]))
        self.assertEqual("hub-job-crash", first_result["job_id"])
        self.assertEqual(first_result["job_id"], second_result["job_id"])
        self.assertEqual(2, post_count)
        self.assertEqual(1, len(hub_jobs))

    def test_token_without_agent_job_scope_cannot_start_automation(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.center.create_event("plain", "disk-event-2", self.event)
        rows = self.center._connection.execute("SELECT COUNT(*) AS count FROM deliveries").fetchone()
        self.assertEqual(0, rows["count"])

    def test_agent_job_event_rejects_payload_authority_fields(self) -> None:
        for field in ("target", "command", "url", "harness", "cwd", "prompt", "tool", "mcp", "credential", "token", "secret", "callback_url"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.center.create_event("allowed", f"danger-{field}", {**self.event, field: "attacker-controlled"})
        rows = self.center._connection.execute("SELECT COUNT(*) AS count FROM deliveries").fetchone()
        self.assertEqual(0, rows["count"])

    def test_signed_adapter_reuses_delivery_key_and_polls_terminal_result(self) -> None:
        requests: list[object] = []
        responses = iter([
            _Response(202, {"route_id": "notify-repair-100", "job_id": "hub-job-1", "status": "accepted"}),
            _Response(200, {"route_id": "notify-repair-100", "job_id": "hub-job-1", "status": "completed", "result": {"raw_output": "secret:raw-session", "response": {"structuredContent": {"result": {"stdout": "log line\n{\"ok\":true,\"session_id\":\"codex-1\",\"profile\":\"repair_100\",\"harness\":\"codex\",\"name\":\"repair_100\",\"created\":false,\"delivery\":\"accepted\"}\n"}}}}}),
        ])

        def runner(request: object, **_kwargs: object) -> _Response:
            requests.append(request)
            return next(responses)

        adapter = GptAdminAgentJobAdapter(
            "repair_100",
            "https://gptadmin.example/webhooks/v1/notify-repair-100",
            "route-secret",
            runner=runner,
            now=lambda: 1_785_640_000,
            sleeper=lambda _seconds: None,
            poll_interval_seconds=0,
        )
        result = adapter.send(
            {"incident": {"id": "inc-1", "project": "infra", "severity": "critical", "title": "Disk", "body": "x" * 4000, "dedup_key": "disk-full:server-100:/", "occurrences": 1}},
            "inc-1:gptadmin.agent:repair_100:event-1",
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual("codex-1", result["agent_receipt"]["session_id"])
        self.assertNotIn("raw-session", json.dumps(result))
        self.assertNotIn("raw_output", json.dumps(result))
        self.assertEqual(2, len(requests))
        post, poll = requests
        self.assertEqual("POST", post.get_method())
        self.assertEqual("inc-1:gptadmin.agent:repair_100:event-1", post.get_header("Idempotency-key"))
        timestamp = post.get_header("X-webhook-timestamp")
        signed = "\n".join(("POST", "/webhooks/v1/notify-repair-100", timestamp, "inc-1:gptadmin.agent:repair_100:event-1", hashlib.sha256(post.data).hexdigest())).encode()
        expected = "sha256=" + hmac.new(b"route-secret", signed, hashlib.sha256).hexdigest()
        self.assertEqual(expected, post.get_header("X-webhook-signature"))
        self.assertEqual("GET", poll.get_method())
        self.assertEqual("https://gptadmin.example/webhook-jobs/hub-job-1", poll.full_url)
        poll_signed = "\n".join(("GET", "/webhook-jobs/hub-job-1", poll.get_header("X-webhook-timestamp"), "", hashlib.sha256(b"").hexdigest())).encode()
        poll_expected = "sha256=" + hmac.new(b"route-secret", poll_signed, hashlib.sha256).hexdigest()
        self.assertEqual(poll_expected, poll.get_header("X-webhook-signature"))
        outbound = json.loads(post.data)
        self.assertEqual({"schema", "job_id", "incident"}, set(outbound))
        self.assertEqual(set(("id", "project", "severity", "title", "body", "dedup_key", "occurrences")), set(outbound["incident"]))
        self.assertEqual(3000, len(outbound["incident"]["body"]))

    def test_health_context_reaches_gptadmin_as_bounded_opaque_metadata(self) -> None:
        responses = iter([
            _Response(202, {"route_id": "notify-health", "job_id": "hub-health-1", "status": "accepted"}),
            _Response(200, {"route_id": "notify-health", "job_id": "hub-health-1", "status": "completed", "result": {"session_id": "herder-1"}}),
        ])
        requests: list[object] = []

        def runner(request: object, **_kwargs: object) -> _Response:
            requests.append(request)
            return next(responses)

        adapter = GptAdminAgentJobAdapter(
            "health-diagnosis", "https://gptadmin.example/webhooks/v1/notify-health", "route-secret",
            runner=runner, now=lambda: 1_785_640_000, sleeper=lambda _seconds: None, poll_interval_seconds=0,
        )
        adapter.send(
            {
                "incident": {"id": "inc-health", "project": "infra", "severity": "critical", "title": "Disk", "body": "disk high", "dedup_key": "health:disk", "occurrences": 1},
                "health_context": {"source_id": "host:100", "host_id": "100", "signal_type": "disk", "correlation_id": "corr-100", "trace_refs": ["trace-a", "secret=must-not-leak"]},
                "health_plans": [{"plan_id": "observe", "title": "Observe", "summary": "bounded", "step": "observe"}],
                "health_selection": {
                    "plan_id": "repair",
                    "actor": "telegram:42",
                    "execution": {"runtime": "hermes", "provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning": "high", "topic": "health"},
                },
            },
            "health-delivery-1",
        )
        outbound = json.loads(requests[0].data)
        self.assertEqual("corr-100", outbound["correlation_id"])
        self.assertEqual(["trace-a", "secret=[redacted]"], outbound["trace_refs"])
        self.assertEqual("host:100", outbound["health"]["source_id"])
        self.assertEqual(1, len(outbound["health"]["plans"]))
        self.assertEqual("repair", outbound["health"]["selection"]["plan_id"])
        self.assertEqual("gpt-5.6-luna", outbound["health"]["selection"]["execution"]["model"])
        self.assertEqual("high", outbound["health"]["selection"]["execution"]["reasoning"])
        self.assertNotIn("must-not-leak", json.dumps(outbound))
        self.assertNotIn("route-secret", json.dumps(outbound))

    def test_adapter_sanitizes_secret_prefixes_in_incident_fields(self) -> None:
        responses = iter([
            _Response(202, {"route_id": "notify-repair-100", "job_id": "hub-job-secret", "status": "accepted"}),
            _Response(200, {"route_id": "notify-repair-100", "job_id": "hub-job-secret", "status": "completed", "result": {"session_id": "codex-safe"}}),
        ])
        requests: list[object] = []

        def runner(request: object, **_kwargs: object) -> _Response:
            requests.append(request)
            return next(responses)

        adapter = GptAdminAgentJobAdapter(
            "repair_100", "https://gptadmin.example/webhooks/v1/notify-repair-100", "route-secret",
            runner=runner, now=lambda: 1_785_640_000, sleeper=lambda _seconds: None, poll_interval_seconds=0,
        )
        adapter.send(
            {"incident": {
                "id": "inc-secret",
                "project": "infra",
                "severity": "critical",
                "title": "api-key:raw-title",
                "body": "token:raw-body",
                "dedup_key": "secret:raw-dedup",
                "occurrences": 1,
            }},
            "secret-delivery",
        )

        outbound = json.loads(requests[0].data)
        serialized = json.dumps(outbound)
        self.assertNotIn("raw-title", serialized)
        self.assertNotIn("raw-body", serialized)
        self.assertNotIn("raw-dedup", serialized)
        self.assertIn("api-key=[redacted]", serialized)
        self.assertIn("token=[redacted]", serialized)

    def test_terminal_agent_receipt_sanitizes_audit_fields(self) -> None:
        created = self.center.create_event("allowed", "receipt-secret-event", self.event)
        self.center.record_agent_job_result(
            created["incident_id"],
            "delivery-secret",
            "repair_100",
            {
                "job_id": "api-key:raw-job",
                "route_id": "token:raw-route",
                "status": "completed",
                "elapsed_ms": 1,
                "agent_receipt": {
                    "session_id": "secret:raw-session",
                    "delivery": "Bearer raw-delivery-token",
                },
            },
        )
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE incident_id = ? AND type = 'agent_job_completed'",
            (created["incident_id"],),
        ).fetchone()
        serialized = str(audit["payload_json"])
        self.assertNotIn("raw-job", serialized)
        self.assertNotIn("raw-route", serialized)
        self.assertNotIn("raw-session", serialized)
        self.assertNotIn("raw-delivery-token", serialized)
        self.assertIn("api-key=[redacted]", serialized)
        self.assertIn("Bearer [redacted]", serialized)

    def test_health_terminal_receipt_is_parsed_without_optional_session_id(self) -> None:
        parsed = GptAdminAgentJobAdapter._bounded_agent_receipt({
            "result": {
                "session_id": "token:raw-session",
                "profile": "api-key:raw-profile",
                "trace_refs": ["secret:raw-trace"],
                "plan_id": "repair",
                "observed_state": "healthy",
                "verification_id": "verification-1",
                "source_fingerprint": "source-fp-1",
                "verifier_id": "probe-b",
            },
        })
        self.assertEqual("repair", parsed["plan_id"])
        self.assertEqual("healthy", parsed["observed_state"])
        self.assertEqual("probe-b", parsed["verifier_id"])
        self.assertEqual("token=[redacted]", parsed["session_id"])
        self.assertEqual("api-key=[redacted]", parsed["profile"])
        self.assertEqual(["secret=[redacted]"], parsed["trace_refs"])

    def test_worker_delivers_only_the_configured_agent_job_adapter(self) -> None:
        created = self.center.create_event("allowed", "disk-event-3", self.event)
        due = self.center.claim_due_deliveries(now_epoch=10**12)
        delivery = next(item for item in due if item["id"] == created["agent_job_delivery_id"])
        calls: list[tuple[dict[str, object], str]] = []

        class Adapter:
            def send(self, payload: dict[str, object], idempotency_key: str) -> dict[str, object]:
                calls.append((payload, idempotency_key))
                return {"job_id": "hub-job-2", "status": "completed", "result": {"session_id": "codex-1"}}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("agent delivery must not use Telegram")

        worker = DeliveryWorker(self.center, Telegram(), agent_jobs={"repair_100": Adapter()})
        worker.deliver(delivery)

        self.assertEqual(1, len(calls))
        status = self.center._connection.execute("SELECT status FROM deliveries WHERE id = ?", (delivery["id"],)).fetchone()
        self.assertEqual("sent", status["status"])

    def test_completed_health_remediation_closes_only_after_independent_verification(self) -> None:
        center = NotificationCenter(
            Path(self.tempdir.name) / "health-remediation.sqlite3",
            {"health-token": {"project": "health-monitor", "max_severity": "critical"}},
            default_quiet_hours=[],
        )
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "health-token",
            "health-remediation-intake",
            {
                "project": "health-monitor",
                "recipient": "health",
                "severity": "critical",
                "title": "Synthetic source degradation",
                "body": "The source fingerprint changed.",
                "dedup_key": "health:source-a:remediation",
                "source_id": "source-a",
                "source_fingerprint": "source-fp-1",
                "host_id": "host-a",
                "signal_type": "disk",
            },
        )
        plans = [
            {"plan_id": "observe", "title": "Observe", "summary": "Capture", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
        ]
        workflow.attach_plans(created["incident_id"], "health-remediation-plans", plans, actor="omniroute")
        workflow.select_plan(created["incident_id"], "health-remediation-selection", "repair", "telegram:42")
        workflow.record_verification(
            created["incident_id"],
            "health-independent-verification",
            source_id="source-a",
            verification_id="verification-1",
            observed_state="healthy",
            evidence_refs=["probe:source-a"],
            fingerprint="source-fp-1",
            actor="probe-b",
        )
        due = center.claim_due_deliveries(now_epoch=10**12)
        delivery = next(item for item in due if item["channel"] == "gptadmin.agent:health-remediation")

        class Adapter:
            def send_with_progress(self, _payload: dict[str, object], _idempotency_key: str, progress_callback: object) -> dict[str, object]:
                assert callable(progress_callback)
                progress_callback({
                    "status": "running",
                    "progress": [{
                        "plan_id": "repair",
                        "step": "repair",
                        "fingerprint": "progress-fp-1",
                        "evidence_refs": ["fresh:repair-receipt"],
                        "useful_progress": True,
                    }],
                })
                return {
                    "job_id": "hub-health-remediation-1",
                    "status": "completed",
                    "elapsed_ms": 86_400_001,
                    "agent_receipt": {
                        "plan_id": "repair",
                        "step": "repair",
                        "progress_fingerprint": "progress-fp-1",
                        "evidence_refs": ["fresh:repair-receipt"],
                        "source_id": "source-a",
                        "source_fingerprint": "source-fp-1",
                        "verification_id": "verification-1",
                        "verifier_id": "probe-b",
                        "observed_state": "healthy",
                        "trace_refs": ["trace-remediation-1"],
                        "useful_progress": True,
                    },
                }

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("health remediation must not send Telegram in this test")

        worker = DeliveryWorker(center, Telegram(), agent_jobs={"health-remediation": Adapter()})
        worker.deliver(delivery)

        event_types = [
            row["event_type"]
            for row in center._connection.execute(
                "SELECT event_type FROM events WHERE incident_id = ? ORDER BY created_at, rowid",
                (created["incident_id"],),
            ).fetchall()
        ]
        self.assertIn("health.progress", event_types)
        self.assertEqual(1, event_types.count("health.progress"))
        self.assertEqual(1, event_types.count("health.verification_recorded"))
        self.assertIn("health.resolved", event_types)
        self.assertEqual("resolved", center.get_incident(created["incident_id"])["state"])
        resolved = center.latest_health_event(created["incident_id"], "health.resolved")
        assert resolved is not None
        self.assertEqual(86_400_000, resolved["payload"]["elapsed_ms"])
        self.assertEqual(["trace-remediation-1", "hub-health-remediation-1"], resolved["payload"]["trace_refs"])
        resolved_delivery = center._connection.execute(
            "SELECT status FROM deliveries WHERE delivery_key = ?",
            (f"{created['incident_id']}:telegram.main:health.resolved",),
        ).fetchone()
        self.assertIsNotNone(resolved_delivery)
        self.assertEqual("queued", resolved_delivery["status"])

    def test_healthy_remediation_without_independent_verifier_stays_open(self) -> None:
        center = NotificationCenter(
            Path(self.tempdir.name) / "health-remediation-rejected.sqlite3",
            {"health-token": {"project": "health-monitor", "max_severity": "critical"}},
            default_quiet_hours=[],
        )
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "health-token",
            "health-rejected-intake",
            {
                "project": "health-monitor",
                "recipient": "health",
                "severity": "critical",
                "title": "Synthetic source degradation",
                "body": "The source fingerprint changed.",
                "dedup_key": "health:source-a:rejected",
                "source_id": "source-a",
                "source_fingerprint": "source-fp-1",
                "host_id": "host-a",
                "signal_type": "disk",
            },
        )
        workflow.attach_plans(created["incident_id"], "health-rejected-plans", [
            {"plan_id": "observe", "title": "Observe", "summary": "Capture", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
        ], actor="omniroute")
        workflow.select_plan(created["incident_id"], "health-rejected-selection", "repair", "telegram:42")
        delivery = next(item for item in center.claim_due_deliveries(now_epoch=10**12) if item["channel"] == "gptadmin.agent:health-remediation")
        payload = center.delivery_payload(delivery)
        result = center.record_agent_job_result(
            created["incident_id"],
            delivery["id"],
            "health-remediation",
            {
                "job_id": "hub-health-rejected-1",
                "status": "completed",
                "elapsed_ms": 1200,
                "agent_receipt": {
                    "plan_id": "repair",
                    "step": "repair",
                    "progress_fingerprint": "progress-fp-rejected",
                    "evidence_refs": ["fresh:repair-receipt"],
                    "source_id": "source-a",
                    "source_fingerprint": "source-fp-1",
                    "verification_id": "verification-missing-verifier",
                    "verifier_id": "probe-b",
                    "observed_state": "healthy",
                    "trace_refs": ["trace-rejected"],
                },
            },
            payload["health_context"],
        )
        self.assertFalse(result["accepted"])
        self.assertEqual("open", center.get_incident(created["incident_id"])["state"])

    def test_heartbeat_only_terminal_receipt_cannot_resolve_incident(self) -> None:
        center = NotificationCenter(
            Path(self.tempdir.name) / "health-remediation-heartbeat.sqlite3",
            {"health-token": {"project": "health-monitor", "max_severity": "critical"}},
            default_quiet_hours=[],
        )
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "health-token",
            "health-heartbeat-intake",
            {
                "project": "health-monitor", "recipient": "health", "severity": "critical",
                "title": "Synthetic source degradation", "body": "The source fingerprint changed.",
                "dedup_key": "health:source-a:heartbeat", "source_id": "source-a",
                "source_fingerprint": "source-fp-1", "host_id": "host-a", "signal_type": "disk",
            },
        )
        workflow.attach_plans(created["incident_id"], "health-heartbeat-plans", [
            {"plan_id": "observe", "title": "Observe", "summary": "Capture", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
        ], actor="omniroute")
        workflow.select_plan(created["incident_id"], "health-heartbeat-selection", "repair", "telegram:42")

        result = center.record_agent_job_result(
            created["incident_id"],
            "health-heartbeat-delivery",
            "health-remediation",
            {
                "job_id": "hub-health-heartbeat",
                "status": "completed",
                "elapsed_ms": 100,
                "agent_receipt": {
                    "plan_id": "repair", "step": "heartbeat", "progress_fingerprint": "heartbeat-only",
                    "evidence_refs": [], "source_id": "source-a", "source_fingerprint": "source-fp-1",
                    "verification_id": "verification-heartbeat", "verifier_id": "probe-b",
                    "observed_state": "healthy",
                },
            },
            {"selection": {"plan_id": "repair"}, "source_id": "source-a", "source_fingerprint": "source-fp-1"},
        )
        self.assertFalse(result["accepted"])
        self.assertEqual("open", center.get_incident(created["incident_id"])["state"])
        self.assertIsNone(center.latest_health_event(created["incident_id"], "health.resolved"))

    def test_heartbeat_only_running_progress_is_not_recorded(self) -> None:
        center = NotificationCenter(
            Path(self.tempdir.name) / "health-running-heartbeat.sqlite3",
            {"health-token": {"project": "health-monitor", "max_severity": "critical"}},
            default_quiet_hours=[],
        )
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "health-token",
            "health-running-heartbeat-intake",
            {
                "project": "health-monitor", "recipient": "health", "severity": "critical",
                "title": "Synthetic source degradation", "body": "The source fingerprint changed.",
                "dedup_key": "health:source-a:running-heartbeat", "source_id": "source-a",
                "source_fingerprint": "source-fp-1", "host_id": "host-a", "signal_type": "disk",
            },
        )
        workflow.attach_plans(created["incident_id"], "health-running-heartbeat-plans", [
            {"plan_id": "observe", "title": "Observe", "summary": "Capture", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
        ], actor="omniroute")
        workflow.select_plan(created["incident_id"], "health-running-heartbeat-selection", "repair", "telegram:42")

        recorded = center.record_health_agent_progress(
            created["incident_id"],
            "health-running-heartbeat-delivery",
            {"health_selection": {"plan_id": "repair"}},
            {"progress": [{"plan_id": "repair", "step": "heartbeat", "fingerprint": "heartbeat-only", "evidence_refs": [], "useful_progress": True}]},
        )
        self.assertEqual([], recorded)
        self.assertIsNone(center.latest_health_event(created["incident_id"], "health.progress"))
        self.assertIsNone(center.latest_health_event(created["incident_id"], "health.resolved"))

    def test_terminal_failed_job_is_recorded_once_without_retry(self) -> None:
        created = self.center.create_event("allowed", "failed-agent-job", self.event)
        due = self.center.claim_due_deliveries(now_epoch=10**12)
        delivery = next(item for item in due if item["id"] == created["agent_job_delivery_id"])

        class Adapter:
            def send(self, _payload: dict[str, object], _idempotency_key: str) -> dict[str, object]:
                return {"job_id": "hub-job-failed", "route_id": "notify-repair-100", "status": "failed", "error": "profile rejected secret=must-not-persist"}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("agent delivery must not use Telegram")

        worker = DeliveryWorker(self.center, Telegram(), agent_jobs={"repair_100": Adapter()})
        worker.deliver(delivery)
        row = self.center._connection.execute("SELECT status, last_error FROM deliveries WHERE id = ?", (delivery["id"],)).fetchone()
        self.assertEqual("failed", row["status"])
        self.assertEqual("GPTAdmin agent job reported terminal failure", row["last_error"])
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE incident_id = ? ORDER BY created_at",
            (delivery["incident_id"],),
        ).fetchall()
        self.assertNotIn("must-not-persist", "\n".join(str(item["payload_json"]) for item in audit))
        reclaimed = self.center.claim_due_deliveries(now_epoch=10**12 + 10_000)
        self.assertNotIn(delivery["id"], {item["id"] for item in reclaimed})

    def test_acknowledge_wins_over_late_terminal_agent_failure(self) -> None:
        created = self.center.create_event("allowed", "late-failed-agent-job", self.event)
        due = self.center.claim_due_deliveries(now_epoch=10**12)
        delivery = next(item for item in due if item["id"] == created["agent_job_delivery_id"])
        self.center.acknowledge(str(delivery["incident_id"]), "operator:test")

        class Adapter:
            def send(self, _payload: dict[str, object], _idempotency_key: str) -> dict[str, object]:
                return {"job_id": "hub-job-late-failed", "status": "failed", "error": "late remote failure"}

        worker = DeliveryWorker(self.center, object(), agent_jobs={"repair_100": Adapter()})
        worker.deliver(delivery)

        row = self.center._connection.execute("SELECT status FROM deliveries WHERE id = ?", (delivery["id"],)).fetchone()
        self.assertEqual("cancelled", row["status"])

    def test_adapter_bounds_hub_response_body(self) -> None:
        class OversizedResponse(_Response):
            def read(self, *_args: object) -> bytes:
                return b"x" * 70_000

        adapter = GptAdminAgentJobAdapter(
            "repair_100", "https://gptadmin.example/webhooks/v1/notify-repair-100", "route-secret",
            runner=lambda *_args, **_kwargs: OversizedResponse(202, {}),
        )
        with self.assertRaisesRegex(RuntimeError, "response exceeds"):
            adapter.send({"incident": {"id": "inc-1"}}, "delivery-1")

    def test_environment_builds_only_named_fixed_agent_routes(self) -> None:
        configured = {"repair_100": {
            "url": "https://gptadmin.example/webhooks/v1/notify-repair-100",
            "hmac_secret": "route-secret",
            "timeout_seconds": 60,
            "poll_interval_seconds": 0.5,
        }}
        with mock.patch.dict(os.environ, {"NOTIFY_GPTADMIN_AGENT_JOBS_JSON": json.dumps(configured)}, clear=True):
            jobs = gptadmin_agent_jobs_from_environment()
        self.assertEqual(["repair_100"], list(jobs))
        self.assertEqual("repair_100", jobs["repair_100"].job_id)

    def test_supervisor_classifies_useful_progress_and_ignores_heartbeat_only_updates(self) -> None:
        supervisor = HealthProgressSupervisor(stale_after_seconds=10, now=lambda: 110)
        progressing = supervisor.observe({
            "status": "running",
            "progress": [
                {"received_at": 100, "step": "repair", "fingerprint": "fp-1", "evidence_refs": ["trace-1"], "useful_progress": True},
                {"received_at": 105, "step": "repair", "fingerprint": "fp-1", "evidence_refs": ["trace-1"], "useful_progress": False},
            ],
        })
        self.assertEqual("progressing", progressing["state"])
        self.assertTrue(progressing["useful_progress"])
        heartbeat_only = supervisor.observe({
            "status": "running",
            "progress": [{
                "received_at": 109,
                "step": "heartbeat",
                "fingerprint": "heartbeat-only",
                "evidence_refs": [],
                "useful_progress": True,
            }],
        })
        self.assertEqual("waiting", heartbeat_only["state"])
        self.assertFalse(heartbeat_only["useful_progress"])
        stalled = HealthProgressSupervisor(stale_after_seconds=10, now=lambda: 120).observe({
            "status": "running",
            "progress": [{"received_at": 100, "fingerprint": "fp-1", "evidence_refs": ["trace-1"], "useful_progress": True}],
        })
        self.assertEqual("stalled", stalled["state"])

    def test_health_adapter_forwards_useful_progress_and_stops_when_it_stalls(self) -> None:
        responses = iter([
            _Response(202, {"route_id": "notify-health-remediation", "job_id": "hub-health-2", "status": "accepted"}),
            _Response(200, {
                "route_id": "notify-health-remediation",
                "job_id": "hub-health-2",
                "status": "running",
                "progress": [{"received_at": 100, "step": "repair", "fingerprint": "fp-1", "evidence_refs": ["snapshot"], "useful_progress": True}],
            }),
            _Response(200, {
                "route_id": "notify-health-remediation",
                "job_id": "hub-health-2",
                "status": "completed",
                "result": {"session_id": "herder-health-2"},
            }),
        ])
        observed: list[dict[str, object]] = []
        adapter = GptAdminAgentJobAdapter(
            "health-remediation",
            "https://gptadmin.example/webhooks/v1/notify-health-remediation",
            "route-secret",
            runner=lambda *_args, **_kwargs: next(responses),
            now=lambda: 100,
            sleeper=lambda _seconds: None,
            poll_interval_seconds=0,
        )
        adapter.send_with_progress(
            {"incident": {"id": "inc-health", "project": "health-monitor", "severity": "critical", "title": "Disk", "body": "x", "dedup_key": "disk", "occurrences": 1}},
            "health-delivery-2",
            lambda job: observed.append(dict(job)),
        )
        self.assertTrue(any(item.get("progress") for item in observed))

        stalled_responses = iter([
            _Response(202, {"route_id": "notify-health-remediation", "job_id": "hub-health-3", "status": "accepted"}),
            _Response(200, {
                "route_id": "notify-health-remediation",
                "job_id": "hub-health-3",
                "status": "running",
                "progress": [{"received_at": 100, "step": "repair", "fingerprint": "fp-old", "evidence_refs": ["snapshot"], "useful_progress": True}],
            }),
        ])
        clock = iter([100])
        def stalled_now() -> int:
            return next(clock, 120)
        stalled = GptAdminAgentJobAdapter(
            "health-remediation",
            "https://gptadmin.example/webhooks/v1/notify-health-remediation",
            "route-secret",
            stale_progress_seconds=10,
            runner=lambda *_args, **_kwargs: next(stalled_responses),
            now=stalled_now,
            sleeper=lambda _seconds: None,
            poll_interval_seconds=0,
        )
        with self.assertRaisesRegex(RuntimeError, "stalled without useful progress"):
            stalled.send(
                {"incident": {"id": "inc-health", "project": "health-monitor", "severity": "critical", "title": "Disk", "body": "x", "dedup_key": "disk", "occurrences": 1}},
                "health-delivery-3",
            )


if __name__ == "__main__":
    unittest.main()
