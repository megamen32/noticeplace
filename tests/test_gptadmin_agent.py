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
from notification_center.gptadmin_agent import GptAdminAgentJobAdapter
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
            _Response(200, {"route_id": "notify-repair-100", "job_id": "hub-job-1", "status": "completed", "result": {"response": {"structuredContent": {"result": {"stdout": "log line\n{\"ok\":true,\"session_id\":\"codex-1\",\"profile\":\"repair_100\",\"harness\":\"codex\",\"name\":\"repair_100\",\"created\":false,\"delivery\":\"accepted\"}\n"}}}}}),
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


if __name__ == "__main__":
    unittest.main()
