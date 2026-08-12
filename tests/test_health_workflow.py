from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller

from notification_center import health_workflow
from notification_center.health_workflow import HealthWorkflow, TelegramHealthPlanCodec


class HealthWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "notify.sqlite3"
        self.center = NotificationCenter(self.database, {"producer-token": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        self.workflow = HealthWorkflow(self.center, callback_secret="x" * 32)
        self.created = self.workflow.intake_signal(
            "producer-token",
            "health-create",
            {
                "project": "hermes",
                "recipient": "me",
                "severity": "critical",
                "title": "Primary source is unhealthy",
                "body": "The source returned a stale fingerprint.",
                "dedup_key": "health:source-a",
                "source_id": "source-a",
                "host_id": "host-a",
                "signal_type": "intake",
                "summary": "The source returned a stale fingerprint.",
                "ack": {"required": True},
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _incident(self) -> dict[str, object]:
        incident = self.center.get_incident(self.created["incident_id"])
        assert incident is not None
        return incident

    @staticmethod
    def _plans() -> list[dict[str, str]]:
        return [
            {"plan_id": "observe", "title": "Observe", "summary": "Confirm the source and capture a fresh fingerprint.", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the smallest reversible change and keep the rollback path open.", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Request an independent healthy receipt before resolve.", "step": "verify"},
        ]

    def test_post_diagnosis_plan_attachment_contains_exactly_three_plans(self) -> None:
        orchestration = {
            "diagnosis_session_id": "diagnosis-session",
            "diagnosis_model": "omniroute/subagent",
            "orchestrator_session_id": "orchestrator-session",
            "orchestrator_requested_model": "omniroute/orchestrator",
            "orchestrator_effective_model": "omniroute/free-stack",
            "harness": "opencode",
            "diagnosis_elapsed_ms": 1200,
            "orchestrator_elapsed_ms": 2300,
            "total_elapsed_ms": 3500,
        }
        self.workflow.attach_plans(self.created["incident_id"], "plans-intake", self._plans(), actor="omniroute", orchestration=orchestration)
        plans = self.center.latest_health_plans(self.created["incident_id"])
        self.assertEqual(3, len(plans))
        self.assertEqual(["observe", "repair", "verify"], [plan["plan_id"] for plan in plans])
        attached = self.center.latest_health_event(self.created["incident_id"], "health.plans_attached")
        self.assertIsNotNone(attached)
        self.assertEqual(orchestration, attached["payload"]["orchestration"])

    def test_health_plan_keyboard_has_exactly_three_signed_choices(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-1", self._plans(), actor="gptadmin")
        keyboard = health_workflow.health_plan_keyboard(self.workflow.codec, self.created["incident_id"], self.center.latest_health_plans(self.created["incident_id"]))
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]

        self.assertEqual(3, len(buttons))
        self.assertEqual(["Observe", "Repair", "Verify"], [button["text"] for button in buttons])
        decoded = [self.workflow.codec.decode(button["callback_data"]) for button in buttons]
        self.assertEqual({(self.created["incident_id"], "observe"), (self.created["incident_id"], "repair"), (self.created["incident_id"], "verify")}, set(decoded))

    def test_telegram_sender_attaches_the_three_health_plan_buttons(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        sender = TelegramSender("bot-token", "-100123", action_codec=codec, center=self.center)
        self.workflow.attach_plans(self.created["incident_id"], "plans-2", self._plans(), actor="gptadmin")
        self.center.set_runtime_setting("telegram_topics_json", json.dumps({"health": {"chat_id": "-100123", "message_thread_id": 77}}))
        captured: dict[str, str] = {}

        class FakeResponse:
            status = 200

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()

        def fake_urlopen(request: object, timeout: float = 0) -> FakeResponse:
            data = urllib.parse.parse_qs(getattr(request, "data").decode())
            captured.update({key: values[0] for key, values in data.items()})
            return FakeResponse()

        with patch("notification_center.http_api.urllib.request.urlopen", fake_urlopen):
            receipt = sender.send({"incident": self._incident(), "delivery": {"delivery_key": "dlv-1"}, "health_plans": self.center.latest_health_plans(self.created["incident_id"])})

        reply_markup = json.loads(captured["reply_markup"])
        buttons = [button for row in reply_markup["inline_keyboard"] for button in row]
        self.assertEqual(3, len(buttons))
        self.assertEqual({"observe", "repair", "verify"}, {self.workflow.codec.decode(button["callback_data"])[1] for button in buttons})
        self.assertEqual("77", captured["message_thread_id"])
        self.assertEqual({"observe", "repair", "verify"}, set(receipt["health_plan_ids"]))
        self.assertEqual(3, receipt["health_button_count"])
        self.assertEqual(3, receipt["health_signed_callback_count"])

    def test_telegram_sender_reports_completed_plan_that_remains_degraded(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        sender = TelegramSender("bot-token", "-100123", action_codec=codec, center=self.center)
        self.center.set_runtime_setting("telegram_topics_json", json.dumps({"health": {"chat_id": "-100123", "message_thread_id": 77}}))
        captured: dict[str, str] = {}

        class FakeResponse:
            status = 200
            def __enter__(self) -> "FakeResponse": return self
            def __exit__(self, *_exc: object) -> None: return None
            def read(self) -> bytes: return json.dumps({"ok": True, "result": {"message_id": 2}}).encode()

        def fake_urlopen(request: object, timeout: float = 0) -> FakeResponse:
            data = urllib.parse.parse_qs(getattr(request, "data").decode())
            captured.update({key: values[0] for key, values in data.items()})
            return FakeResponse()

        with patch("notification_center.http_api.urllib.request.urlopen", fake_urlopen):
            sender.send({
                "incident": self._incident(),
                "delivery": {"delivery_key": "degraded-result"},
                "health_outcome": {"plan_id": "plan-003", "observed_state": "degraded", "step": "validate keywords"},
            })

        self.assertIn("Plan plan-003 completed", captured["text"])
        self.assertIn("still degraded", captured["text"])
        self.assertEqual("77", captured["message_thread_id"])
        self.assertNotIn("reply_markup", captured)

    def test_telegram_sender_fails_closed_without_health_callback_codec(self) -> None:
        sender = TelegramSender(
            "bot-token",
            "-100123",
            severity_routes={"health": {"chat_id": "-100123", "message_thread_id": 77}},
            active_modes={"health"},
        )
        with self.assertRaisesRegex(RuntimeError, "signed plan callback"):
            sender.send({
                "incident": self._incident(),
                "delivery": {"delivery_key": "dlv-no-codec"},
                "health_plans": self.center.latest_health_plans(self.created["incident_id"]),
            })

    def test_telegram_sender_edits_legacy_health_card_in_place(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        sender = TelegramSender("bot-token", "-100123", action_codec=codec, active_modes={"health"})
        captured: dict[str, str] = {}

        class FakeResponse:
            status = 200

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"ok": True, "result": {"message_id": 88, "chat": {"id": -100123}}}).encode()

        def fake_urlopen(request: object, timeout: float = 0) -> FakeResponse:
            captured["url"] = getattr(request, "full_url")
            captured.update({key: values[0] for key, values in urllib.parse.parse_qs(getattr(request, "data").decode()).items()})
            return FakeResponse()

        self.workflow.attach_plans(self.created["incident_id"], "plans-edit", self._plans(), actor="gptadmin")
        with patch("notification_center.http_api.urllib.request.urlopen", fake_urlopen):
            receipt = sender.edit_health_card(
                {"incident": self._incident(), "health_plans": self.center.latest_health_plans(self.created["incident_id"])},
                88,
                "-100123",
            )
        self.assertTrue(captured["url"].endswith("/editMessageText"))
        buttons = [button for row in json.loads(captured["reply_markup"])["inline_keyboard"] for button in row]
        self.assertEqual(3, len(buttons))
        self.assertEqual(3, receipt["health_signed_callback_count"])

    def test_plan_selection_is_signed_and_idempotent(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        calls: list[tuple[str, dict[str, object]]] = []
        health_codec = TelegramHealthPlanCodec(codec.secret)
        self.workflow.attach_plans(self.created["incident_id"], "plans-3", self._plans(), actor="gptadmin")
        update = {"update_id": 10, "callback_query": {"id": "cb-1", "from": {"id": 42}, "data": health_codec.encode(self.created["incident_id"], "verify")}}

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, health_plan_codec=health_codec, api=api)
        self.assertEqual(1, poller.poll_once())

        event_rows = self.center._connection.execute(
            "SELECT event_type, payload_json FROM events WHERE incident_id = ? AND event_type = 'health.plan_selected' ORDER BY created_at",
            (self.created["incident_id"],),
        ).fetchall()
        self.assertEqual(1, len(event_rows))
        self.assertIn('"plan_id": "verify"', event_rows[0]["payload_json"])
        self.assertEqual(1, len([call for call in calls if call[0] == "answerCallbackQuery"]))

        duplicate = self.center.select_health_plan(self.created["incident_id"], "cb-1", "verify", "telegram:42")
        self.assertTrue(duplicate["idempotent"])
        with self.assertRaises(ValidationError):
            self.center.select_health_plan(self.created["incident_id"], "cb-2", "bogus", "telegram:42")

    def test_malformed_non_health_plan_callback_is_answered_without_stopping_poller(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        update = {"update_id": 11, "callback_query": {"id": "cb-malformed", "from": {"id": 42}, "data": "n:ack:incident:unexpected:signature"}}

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            TelegramActionCodec("x" * 32),
            api=api,
        )
        self.assertEqual(1, poller.poll_once())
        self.assertIn(("answerCallbackQuery", {"callback_query_id": "cb-malformed", "text": "Invalid action"}), calls)

    def test_malformed_health_plan_callback_without_plan_is_answered_without_stopping_poller(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        update = {"update_id": 12, "callback_query": {"id": "cb-health-malformed", "from": {"id": 42}, "data": "n:health_plan:incident:signature"}}

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            TelegramActionCodec("x" * 32),
            api=api,
        )
        self.assertEqual(1, poller.poll_once())
        self.assertIn(("answerCallbackQuery", {"callback_query_id": "cb-health-malformed", "text": "Invalid action"}), calls)

    def test_selected_plan_creates_one_remediation_request_with_execution_profile(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-remediation", self._plans(), actor="omniroute")
        selected = self.workflow.select_plan(self.created["incident_id"], "selection-remediation", "repair", "telegram:42")

        expected_execution = {
            "runtime": "hermes",
            "provider": "openai-codex",
            "model": "gpt-5.6-luna",
            "reasoning": "high",
            "topic": "health",
        }
        self.assertEqual(expected_execution, self.center.latest_health_selection(self.created["incident_id"])["execution"])
        self.assertTrue(selected["remediation_delivery_id"])
        self.assertTrue(selected["remediation_event_id"])

        due = self.center.claim_due_deliveries(now_epoch=10**12)
        remediation = [item for item in due if item["channel"] == "gptadmin.agent:health-remediation"]
        self.assertEqual(1, len(remediation))
        payload = self.center.delivery_payload(remediation[0])
        self.assertEqual("repair", payload["health_selection"]["plan_id"])
        self.assertEqual(expected_execution, payload["health_selection"]["execution"])

        duplicate = self.workflow.select_plan(self.created["incident_id"], "selection-remediation-duplicate", "repair", "telegram:42")
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(selected["remediation_delivery_id"], duplicate["remediation_delivery_id"])
        self.assertEqual(1, len([item for item in self.center._health_events(self.created["incident_id"]) if item["event_type"] == "health.remediation_requested"]))

    def test_concurrent_different_plan_callbacks_have_one_atomic_winner(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-concurrent", self._plans(), actor="omniroute")

        def choose(plan_id: str) -> str:
            try:
                result = self.workflow.select_plan(self.created["incident_id"], f"selection-{plan_id}", plan_id, "telegram:42")
                return f"accepted:{result['plan_id']}"
            except ValidationError as error:
                return f"rejected:{error}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(choose, ["observe", "repair"]))
        accepted = [result for result in results if result.startswith("accepted:")]
        self.assertEqual(1, len(accepted))
        rows = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM events WHERE incident_id = ? AND event_type = 'health.plan_selected'",
            (self.created["incident_id"],),
        ).fetchone()
        self.assertEqual(1, rows["count"])

    def test_separate_notification_center_instances_have_one_atomic_winner(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-multi-instance", self._plans(), actor="omniroute")
        second_center = NotificationCenter(self.database, {"producer-token": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        second_workflow = HealthWorkflow(second_center, callback_secret="x" * 32)

        def choose(item: tuple[HealthWorkflow, str]) -> str:
            workflow, plan_id = item
            try:
                return f"accepted:{workflow.select_plan(self.created['incident_id'], f'multi-{plan_id}', plan_id, 'telegram:42')['plan_id']}"
            except ValidationError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(choose, [(self.workflow, "observe"), (second_workflow, "repair")]))
        self.assertEqual(1, len([result for result in results if result.startswith("accepted:")]))
        rows = self.center._connection.execute(
            "SELECT plan_id FROM health_plan_selections WHERE incident_id = ?",
            (self.created["incident_id"],),
        ).fetchall()
        self.assertEqual(1, len(rows))

    def test_heartbeat_only_progress_is_not_useful(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-heartbeat-only", self._plans(), actor="gptadmin")
        self.workflow.select_plan(self.created["incident_id"], "selection-heartbeat-only", "observe", "telegram:42")
        with self.assertRaisesRegex(ValidationError, "heartbeat-only"):
            self.workflow.record_progress(
                self.created["incident_id"],
                "progress-heartbeat-only",
                plan_id="observe",
                step="heartbeat",
                evidence_refs=[],
                progress_fingerprint="",
                heartbeat_at=123.0,
                actor="worker",
            )
        progress_rows = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM events WHERE incident_id = ? AND event_type = 'health.progress'",
            (self.created["incident_id"],),
        ).fetchone()
        self.assertEqual(0, progress_rows["count"])
        with self.assertRaisesRegex(ValidationError, "useful progress"):
            self.center.resolve(self.created["incident_id"], "api")

    def test_selection_requires_attached_plans(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown health plan"):
            self.workflow.select_plan(self.created["incident_id"], "selection-before-plans", "repair", "telegram:42")

    def test_resolution_rejects_a_mismatched_source_fingerprint(self) -> None:
        created = self.workflow.intake_signal(
            "producer-token",
            "health-fingerprint-create",
            {
                "project": "hermes",
                "recipient": "me",
                "severity": "critical",
                "title": "Fingerprint source degraded",
                "body": "A bounded source fingerprint changed.",
                "dedup_key": "health:fingerprint-source",
                "source_id": "source-fingerprint",
                "host_id": "host-fingerprint",
                "signal_type": "disk",
                "health": {"fingerprint": "fp-bad"},
            },
        )
        incident_id = created["incident_id"]
        self.workflow.attach_plans(incident_id, "fingerprint-plans", self._plans(), actor="omniroute")
        self.workflow.select_plan(incident_id, "fingerprint-selection", "repair", "telegram:42")
        self.workflow.record_progress(incident_id, "fingerprint-progress", plan_id="repair", step="repair", evidence_refs=["snapshot"], progress_fingerprint="fp-progress", actor="herder")
        self.workflow.record_verification(incident_id, "fingerprint-verification-bad", source_id="source-fingerprint", verification_id="verify-bad", observed_state="healthy", fingerprint="fp-other", evidence_refs=["probe"], actor="probe-b")
        with self.assertRaisesRegex(ValidationError, "fingerprint"):
            self.workflow.resolve(incident_id, "source-fingerprint", "verify-bad", "api")

    def test_useful_progress_requires_step_evidence_or_fingerprint_change(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-4", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-progress")
        first = self.center.record_health_progress(self.created["incident_id"], "worker", {"plan_id": "observe", "step": "capture", "evidence_refs": ["fresh probe snapshot"], "progress_fingerprint": "fp-1", "heartbeat_at": 123.0}, "progress-1")
        repeat = self.center.record_health_progress(self.created["incident_id"], "worker", {"plan_id": "observe", "step": "capture", "evidence_refs": ["fresh probe snapshot"], "progress_fingerprint": "fp-1", "heartbeat_at": 124.0}, "progress-2")
        changed = self.center.record_health_progress(self.created["incident_id"], "worker", {"plan_id": "observe", "step": "verify", "evidence_refs": ["independent healthy receipt"], "progress_fingerprint": "fp-2", "heartbeat_at": 125.0}, "progress-3")

        self.assertTrue(first["useful_progress"])
        self.assertFalse(repeat["useful_progress"])
        self.assertTrue(changed["useful_progress"])
        rows = self.center._connection.execute(
            "SELECT payload_json FROM events WHERE incident_id = ? AND event_type = 'health.progress' ORDER BY created_at",
            (self.created["incident_id"],),
        ).fetchall()
        self.assertEqual(3, len(rows))

    def test_heartbeat_with_fingerprint_without_timestamp_is_rejected(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-heartbeat-fingerprint", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-heartbeat-fingerprint")
        with self.assertRaisesRegex(ValidationError, "heartbeat-only"):
            self.workflow.record_progress(
                self.created["incident_id"],
                "heartbeat-fingerprint-without-timestamp",
                plan_id="observe",
                step="heartbeat",
                evidence_refs=[],
                progress_fingerprint="heartbeat-only",
                actor="herder",
            )
        self.assertIsNone(self.center.latest_health_event(self.created["incident_id"], "health.progress"))

    def test_repeated_progress_with_the_same_idempotency_key_is_idempotent(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-progress-retry", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-progress-retry")
        progress = {"plan_id": "observe", "step": "capture", "evidence_refs": ["fresh snapshot"], "progress_fingerprint": "fp-retry"}
        first = self.center.record_health_progress(self.created["incident_id"], "worker", progress, "progress-retry")
        second = self.center.record_health_progress(self.created["incident_id"], "worker", progress, "progress-retry")
        self.assertTrue(first["useful_progress"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["event_id"], second["event_id"])

    def test_health_resolution_requires_independent_healthy_verification(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-5", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-resolution")
        self.center.record_health_progress(self.created["incident_id"], "worker", {"plan_id": "observe", "step": "capture", "evidence_refs": ["snapshot"], "progress_fingerprint": "fp-resolution"}, "progress-resolution")
        with self.assertRaisesRegex(ValidationError, "verification"):
            self.center.resolve(self.created["incident_id"], "api")

        with self.assertRaisesRegex(ValidationError, "independent"):
            self.center.record_health_verification(
                self.created["incident_id"],
                "worker",
                {"source_id": "source-a", "verifier_id": "source-a", "healthy": True, "fingerprint": "src-fp-1", "evidence": "self-check"},
                "verif-1",
            )

        verification = self.center.record_health_verification(
            self.created["incident_id"],
            "worker",
            {"source_id": "source-a", "verifier_id": "probe-b", "healthy": True, "fingerprint": "src-fp-1", "evidence": "independent healthy receipt"},
            "verif-2",
        )
        self.assertTrue(verification["healthy"])
        resolved = self.center.resolve(self.created["incident_id"], "api")
        self.assertEqual("resolved", resolved["state"])

    def test_remediation_actor_cannot_record_independent_verification(self) -> None:
        with self.assertRaisesRegex(ValidationError, "independent"):
            self.center.record_health_verification(
                self.created["incident_id"],
                "agent-herder",
                {
                    "source_id": "source-a",
                    "verifier_id": "agent-herder",
                    "healthy": True,
                    "fingerprint": "src-fp-1",
                    "evidence": "self-check",
                },
                "agent-herder-verification",
            )

    def test_legacy_verification_without_identity_cannot_resolve(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-legacy-verification", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-legacy-verification")
        self.center.record_health_progress(
            self.created["incident_id"],
            "worker",
            {"plan_id": "observe", "step": "capture", "evidence_refs": ["snapshot"], "progress_fingerprint": "fp-legacy"},
            "progress-legacy-verification",
        )
        self.center.record_health_update(
            self.created["incident_id"],
            "legacy-verification",
            "health.verification_recorded",
            {"source_id": "source-a", "verifier_id": "probe-b", "actor": "probe-b", "healthy": True, "evidence_refs": ["probe"]},
            actor="probe-b",
        )
        with self.assertRaisesRegex(ValidationError, "verification"):
            self.workflow.resolve(self.created["incident_id"], "source-a", "", "api")

    def test_workflow_verification_receipt_resolves_only_after_matching_source(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-workflow", self._plans(), actor="omniroute")
        self.workflow.select_plan(self.created["incident_id"], "selection-workflow", "verify", "telegram:42")
        self.workflow.record_progress(self.created["incident_id"], "progress-workflow", plan_id="verify", step="verify", evidence_refs=["fresh snapshot"], progress_fingerprint="fp-workflow", actor="herder")
        with self.assertRaisesRegex(ValidationError, "verification"):
            self.workflow.resolve(self.created["incident_id"], "source-a", "verify-1", "api")
        self.workflow.record_verification(
            self.created["incident_id"],
            "verification-wrong-source",
            source_id="other-source",
            verification_id="verify-1",
            observed_state="healthy",
            evidence_refs=["independent probe"],
            actor="probe-b",
        )
        with self.assertRaisesRegex(ValidationError, "original source"):
            self.workflow.resolve(self.created["incident_id"], "other-source", "verify-1", "api")
        self.workflow.record_verification(
            self.created["incident_id"],
            "verification-right-source",
            source_id="source-a",
            verification_id="verify-1",
            observed_state="healthy",
            evidence_refs=["independent probe"],
            actor="probe-b",
        )
        resolved = self.workflow.resolve(self.created["incident_id"], "source-a", "verify-1", "api", elapsed_ms=4200, trace_refs=["trace-health-1"])
        self.assertEqual("resolved", resolved["state"])
        self.assertEqual(1, len(self.center.list_incidents()))
        resolution = self.center.latest_health_event(self.created["incident_id"], "health.resolved")
        assert resolution is not None
        self.assertEqual(4200, resolution["payload"]["elapsed_ms"])
        self.assertEqual(["trace-health-1", "independent probe"], resolution["payload"]["trace_refs"])
        self.assertEqual("source-a", resolution["payload"]["source_id"])

    def test_bounded_receipts_do_not_persist_raw_logs_or_secrets(self) -> None:
        self.workflow.attach_plans(self.created["incident_id"], "plans-6", self._plans(), actor="gptadmin")
        self.center.record_health_plan_selection(self.created["incident_id"], "observe", "telegram:42", "selection-boundary")
        evidence = "api-key:super-secret secret:another-secret Bearer bearer-secret token=third-secret authorization: Bearer auth-secret " + ("x" * 5000)
        receipt = self.center.record_health_progress(self.created["incident_id"], "worker", {"plan_id": "observe", "step": "capture", "evidence_refs": [evidence], "progress_fingerprint": "fp-boundary-1"}, "progress-boundary")
        self.assertTrue(receipt["useful"])
        row = self.center._connection.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ?",
            ("progress-boundary",),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        self.assertLessEqual(len(payload["evidence_refs"][0]), 128)
        self.assertNotIn("super-secret", row["payload_json"])
        self.assertNotIn("another-secret", row["payload_json"])
        self.assertNotIn("bearer-secret", row["payload_json"])
        self.assertNotIn("third-secret", row["payload_json"])
        self.assertNotIn("auth-secret", row["payload_json"])


if __name__ == "__main__":
    unittest.main()
