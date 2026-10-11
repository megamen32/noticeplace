"""Repair-first incident contract: one executor, one question, one marked card."""

from __future__ import annotations

import json
import os
import urllib.parse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center import agent_job_helper
from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import mark_human_request_resolved_card, record_incident_choice_result
from notification_center.selected_card import canonical_choice_ref
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


@patch.dict(os.environ, {"NOTIFY_MCP_HTTP_URL": "", "NOTIFY_MCP_HTTP_TOKEN": ""}, clear=False)
def local_dispatch(request: dict) -> object:
    """Call the local MCP dispatcher, never the production HTTP relay."""
    from mcp import notify_mcp
    return notify_mcp.dispatch(request)


def fake_telegram_api(calls: list[dict]) -> object:
    def api(method: str, payload: dict) -> dict:
        calls.append({"method": method, **payload})
        return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}
    return api


class _Response:
    status = 200

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return json.dumps(self.payload).encode()


class RepairFirstExecutorTests(unittest.TestCase):
    """The executor session repairs first; no planner and no mandatory plan card."""

    @staticmethod
    def _runner(texts: dict[str, str], requests: list[object]) -> object:
        def runner(request, **_kwargs):
            url = str(getattr(request, "full_url", ""))
            if "/coordination/context?" in url:
                return _Response({"humanStopHeld": False})
            if url.endswith("/api/automation/launch-policy"):
                return _Response({
                    "version": 1, "incidentExecution": dict(agent_job_helper.HEALTH_EXECUTION_PROFILE),
                    "allowedHarnesses": ["opencode"], "preferredHarness": "opencode",
                    "models": {"opencode": "minimax-coding-plan/MiniMax-M3.1-Flash-Preview"},
                })
            if "/api/models?" in url:
                return _Response({"models": ["minimax-coding-plan/MiniMax-M3.1-Flash-Preview"], "stale": False})
            requests.append(request)
            if "/api/autopilot/sessions/" in url:
                return _Response({"enabled": True, "sessionId": "executor-native"})
            if url.endswith("/api/sessions/new-or-resume"):
                return _Response({"ok": True, "created": True, "sessionId": "executor-native", "delivery": "accepted"})
            if "/progress?" in url:
                return _Response({"session": {"status": "idle"}, "fingerprint": "progress:executor"})
            if "/details?" in url:
                return _Response({"messages": [{"role": "assistant", "text": texts["executor"]}]})
            if "/health/" in url:
                return _Response({"event_id": "evt-repair", "incident_id": "inc-repair"})
            raise AssertionError(f"unexpected legacy planner or card request: {url}")

        return runner

    @staticmethod
    def _profile(root: str) -> dict[str, str]:
        return {
            "url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "name": "health",
            "harness": "opencode", "cwd": root, "mode": "queue", "instruction": "Чини до восстановления.",
            "model": "minimax-coding-plan/MiniMax-M3.1-Flash-Preview",
            "orchestrator_name": "planner", "orchestrator_model": "configured",
            "orchestrator_requested_model": "configured",
        }

    @staticmethod
    def _event() -> dict[str, object]:
        return {
            "schema": "notify.agent-job.v1", "job_id": "health-diagnosis",
            "incident": {"id": "inc-repair", "project": "fleet-health", "severity": "critical",
                         "title": "Диск заполнен", "body": "bounded", "dedup_key": "health:disk", "occurrences": 1},
            "health": {"correlation_id": "corr-1", "trace_refs": ["trace:source"], "evidence_refs": ["probe:disk"]},
        }

    def _run(self, texts: dict[str, str], root: str) -> tuple[dict[str, object], list[object]]:
        requests: list[object] = []
        with patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "callback-token")):
            receipt = agent_job_helper.run_profile(
                "health-diagnosis", self._event(), Path("unused"),
                runner=self._runner(texts, requests), profile_override=self._profile(root),
            )
        return receipt, requests

    def _health_actions(self, requests: list[object]) -> list[str]:
        return [str(getattr(r, "full_url", "")).rsplit("/health/", 1)[-1]
                for r in requests if "/health/" in str(getattr(r, "full_url", ""))]

    def test_verified_repair_resolves_without_planner_or_plan_card(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            receipt, requests = self._run({"executor": json.dumps({"repair_first": {
                "status": "completed", "plan_id": "repair", "step": "Освободил место и перезапустил юнит",
                "observed_state": "healthy", "source_id": "disk-source", "source_fingerprint": "fp-1",
                "verifier_id": "disk-verifier", "verification_id": "verify-1",
                "evidence_refs": ["probe:disk"], "trace_refs": ["trace:executor"],
            }})}, root)
        self.assertTrue(receipt["autopilot_armed"])
        self.assertEqual("repair-first", receipt["path"])
        self.assertTrue(receipt["resolved"])
        self.assertNotIn("plans_count", receipt)
        self.assertEqual(["progress", "verification", "resolve"], self._health_actions(requests))
        self.assertFalse(any("plans" in str(getattr(r, "full_url", "")) for r in requests))

    def test_unverified_claim_is_terminal_and_never_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            receipt, requests = self._run({"executor": json.dumps({"repair_first": {
                "status": "completed", "plan_id": "repair", "step": "Починил",
                "observed_state": "healthy", "source_id": "same-source", "source_fingerprint": "fp-1",
                "verifier_id": "same-source", "verification_id": "verify-1",
                "evidence_refs": ["probe:disk"], "trace_refs": ["trace:executor"],
            }})}, root)
        self.assertEqual("completed", receipt["status"])
        self.assertFalse(receipt["resolved"])
        self.assertIn("verifier repeats the source", receipt["receipt_rejected"])
        self.assertEqual([], self._health_actions(requests))

    def test_pending_question_keeps_one_session_and_posts_no_card(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            receipt, requests = self._run({"executor": json.dumps({"health_question": {
                "needs_human": True, "question_ref": "ask-42", "reason": "Два равнозначных пути",
            }})}, root)
        self.assertTrue(receipt["awaiting_human"])
        self.assertEqual("ask-42", receipt["question_ref"])
        self.assertEqual("executor-native", receipt["session_id"])
        self.assertEqual([], self._health_actions(requests))
        self.assertEqual(1, len([r for r in requests if "/api/sessions/new-or-resume" in str(getattr(r, "full_url", ""))]))

    def test_prompt_states_repair_first_without_forcing_one_final_format(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _, requests = self._run({"executor": json.dumps({"health_question": {"needs_human": True}})}, root)
        message = json.loads([r for r in requests if "/api/sessions/new-or-resume" in str(getattr(r, "full_url", ""))][0].data)["message"]
        self.assertIn("repair-first", message)
        self.assertIn("noticeplace_incident_progress", message)
        self.assertIn("human_request_create", message)
        self.assertIn("notify_user", message)
        self.assertNotIn("Других финальных форматов нет", message)
        self.assertNotIn("noticeplace_ask_human", message)


class ProjectRouteTests(unittest.TestCase):
    """Explicit per-project routes are honoured; everything else stays pinned."""

    @staticmethod
    def _runner(policy: dict, models: dict, requests: list[object]) -> object:
        def runner(request, **_kwargs):
            url = str(getattr(request, "full_url", ""))
            if url.endswith("/api/automation/launch-policy"):
                return _Response(policy)
            if "/api/models?" in url:
                harness = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("harness", [""])[0]
                return _Response({"models": models.get(harness, []), "stale": False})
            requests.append(request)
            return _Response({"ok": True, "created": True, "sessionId": "route-native", "delivery": "accepted"})
        return runner

    def _policy(self, project_routes: dict | None = None) -> dict:
        policy = {
            "version": 1, "incidentExecution": dict(agent_job_helper.HEALTH_EXECUTION_PROFILE),
            "allowedHarnesses": ["opencode", "zcode"], "preferredHarness": "opencode",
            "models": {"opencode": "minimax-coding-plan/MiniMax-M3.1-Flash-Preview",
                       "zcode": "account:zai-individual-coding-plan/GLM-5.3-Flash$high"},
        }
        if project_routes is not None:
            policy["projectRoutes"] = project_routes
        return policy

    def _run(self, project: str, policy: dict, models: dict) -> tuple[dict, list[object]]:
        requests: list[object] = []
        profile = {
            "url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "name": "health",
            "harness": "opencode", "cwd": "/tmp", "mode": "queue", "instruction": "Чини.",
            "orchestrator_name": "planner", "orchestrator_model": "configured",
            "orchestrator_requested_model": "configured",
        }
        event = {"schema": "notify.agent-job.v1", "job_id": "health-diagnosis",
                 "incident": {"id": "inc-1", "project": project, "severity": "critical",
                              "title": "t", "body": "b", "dedup_key": "d", "occurrences": 1}}
        with patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "token")), \
             patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}):
            receipt = agent_job_helper.run_profile("health-diagnosis", event, Path("unused"),
                                                   runner=self._runner(policy, models, requests), profile_override=profile)
        return receipt, requests

    def test_unconfigured_project_keeps_the_pinned_subscription_route(self) -> None:
        receipt, requests = self._run("fleet-health", self._policy(), {"opencode": ["minimax-coding-plan/MiniMax-M3.1-Flash-Preview"]})
        self.assertEqual("opencode", receipt["harness"])
        self.assertEqual("minimax-coding-plan/MiniMax-M3.1-Flash-Preview", receipt["model"])

    def test_configured_project_route_applies_its_declared_order(self) -> None:
        policy = self._policy({"TelegramAuto": {"harnesses": ["opencode", "zcode"]}})
        receipt, _ = self._run("TelegramAuto", policy, {"zcode": ["account:zai-individual-coding-plan/GLM-5.3-Flash$high"]})
        self.assertEqual("zcode", receipt["harness"])
        self.assertEqual("account:zai-individual-coding-plan/GLM-5.3-Flash$high", receipt["model"])

    def test_unavailable_first_route_falls_through_to_the_next_configured_one(self) -> None:
        policy = self._policy({"TelegramAuto": {"harnesses": ["zcode", "opencode"]}})
        receipt, _ = self._run("TelegramAuto", policy, {"opencode": ["minimax-coding-plan/MiniMax-M3.1-Flash-Preview"]})
        self.assertEqual("opencode", receipt["harness"])

    def test_no_available_route_refuses_instead_of_guessing(self) -> None:
        policy = self._policy({"TelegramAuto": {"harnesses": ["zcode"]}})
        with self.assertRaisesRegex(RuntimeError, "no available harness"):
            self._run("TelegramAuto", policy, {"opencode": []})


class SelectedCardUpdaterTests(unittest.TestCase):
    """Telegram, voice, and API share one canonical receipt and one updater."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.directory.name) / "notice.sqlite3",
                                         {"p": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.center._connection.close)
        self.codec = TelegramActionCodec("x" * 32)

    def _human_request(self, mode: str = "choice") -> dict[str, object]:
        payload = {
            "schema": "ask_human.request.v1", "request_id": "req-1", "project": "hermes",
            "recipient": "me", "mode": mode, "message": "Что делать?",
            "choices": [{"label": "Починить сейчас", "value": "repair"},
                        {"label": "Только наблюдать", "value": "observe"}] if mode == "choice" else None,
            "allowed_actors": ["telegram:42"],
        }
        return self.center.create_human_request("p", payload)

    def _poller(self, api: object) -> TelegramInteractionPoller:
        return TelegramInteractionPoller(self.center, "token", {"42"}, self.codec, api=api)

    def test_free_form_answer_gets_a_stable_opaque_reference(self) -> None:
        self._human_request("question")
        self.center.bind_human_request_message("p", "req-1", "-100", 7)
        self.assertEqual("answer-1", canonical_choice_ref("answer-1"))
        value = "Да, перезапускай сейчас"
        self.center.resolve_human_request_from_telegram("req-1", "telegram:42", value)
        calls: list[dict] = []
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            marked = mark_human_request_resolved_card(
                self.center, self.center.get_human_request_from_telegram("req-1"), "api", fake_telegram_api(calls))
        receipt = self.center.selection_receipt("req-1")
        self.assertEqual(canonical_choice_ref(value), receipt["choice_id"])
        self.assertEqual(value, receipt["label"])
        self.assertEqual("marked", marked["status"])

    def test_first_answer_wins_and_a_conflicting_repeat_keeps_the_canonical_option(self) -> None:
        self._human_request()
        self.center.bind_human_request_message("p", "req-1", "-100", 7)
        self.center.resolve_human_request_from_telegram("req-1", "telegram:42", "repair")
        edits: list[dict[str, object]] = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            first = mark_human_request_resolved_card(self.center, self.center.get_human_request_from_telegram("req-1"), "api", api)
            repeated = mark_human_request_resolved_card(self.center, self.center.get_human_request_from_telegram("req-1"), "api", api)
        self.assertEqual("marked", first["status"])
        self.assertTrue(repeated["selection"]["accepted"] is False)
        self.assertFalse(repeated["selection"]["conflict"])
        self.assertEqual("repair", self.center.selection_receipt("req-1")["choice_id"])
        self.assertEqual("Починить сейчас", self.center.selection_receipt("req-1")["label"])

    def test_second_resolver_cannot_replace_the_accepted_answer(self) -> None:
        self._human_request()
        self.center.bind_human_request_message("p", "req-1", "-100", 7)
        self.center.resolve_human_request_from_telegram("req-1", "telegram:42", "observe")
        calls: list[dict] = []
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            mark_human_request_resolved_card(
                self.center, self.center.get_human_request_from_telegram("req-1"), "telegram:42", fake_telegram_api(calls))
            # A later resolver may repeat the accepted answer, never replace it.
            repeated = self.center.record_selection_receipt(
                request_id="req-1", incident_id="req-1", choice_id="repair",
                label="Починить сейчас", actor="voice", source="voice")
        self.assertFalse(repeated["accepted"])
        self.assertTrue(repeated["conflict"])
        self.assertEqual("observe", self.center.selection_receipt("req-1")["choice_id"])
        self.assertEqual("Только наблюдать", self.center.selection_receipt("req-1")["label"])
        with self.assertRaises(ValidationError):
            self.center.resolve_human_request_from_telegram("req-1", "telegram:42", "repair")

    def test_one_marker_renderer_for_every_resolver(self) -> None:
        from notification_center.selected_card import selected_card_text
        text = selected_card_text("Что делать?", "Починить сейчас")
        self.assertEqual("Что делать?\n\n✅ Выбрано: Починить сейчас", text)
        self.assertEqual(text, selected_card_text(text, "Починить сейчас"))

    def test_telegram_choice_button_marks_the_same_bound_card(self) -> None:
        self._human_request()
        self.center.bind_human_request_message("p", "req-1", "-100", 7)
        edits: list[dict[str, object]] = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "getUpdates":
                return {"ok": True, "result": []}
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        poller = self._poller(api)
        self.assertEqual(0, poller.poll_once())
        data = self.codec.encode("human_choice", "req-1", choice_id="1")
        with patch.object(poller, "_api", api):
            poller._handle_callback({
                "id": "cb", "from": {"id": 42}, "data": data,
                "message": {"chat": {"id": -100}, "message_id": 7, "text": "Что делать?"},
            })
        self.assertTrue(any("✅ Выбрано: Только наблюдать" in str(edit.get("text", "")) for edit in edits))
        receipt = self.center.selection_receipt("req-1")
        self.assertEqual("observe", receipt["choice_id"])
        self.assertEqual("telegram", receipt["source"])

    def test_incident_choice_result_is_canonical_and_edits_the_incident_card(self) -> None:
        created = self.center.create_event("p", "choice", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "notification",
            "severity": "important", "title": "Выбери путь", "body": "Нужен выбор",
            "dedup_key": "herder:choice:1", "choice_request_id": "11111111-1111-4111-8111-111111111111",
            "choices": [{"choice_id": "inspect", "label": "Проверить юнит"},
                        {"choice_id": "repair", "label": "Починить юнит"}],
        })
        self._human_request()
        edits: list[dict[str, object]] = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        first = record_incident_choice_result(self.center, "p", created["incident_id"], {
            "request_id": "11111111-1111-4111-8111-111111111111", "choice_id": "inspect",
            "actor": "voice:1", "source": "voice"}, api)
        repeat = record_incident_choice_result(self.center, "p", created["incident_id"], {
            "request_id": "11111111-1111-4111-8111-111111111111", "choice_id": "repair",
            "actor": "telegram:42", "source": "telegram"}, api)
        self.assertEqual("inspect", first["choice_id"])
        self.assertTrue(first["accepted"])
        self.assertFalse(repeat["accepted"])
        self.assertTrue(repeat["conflict"])
        self.assertEqual("inspect", repeat["choice_id"])
        self.assertEqual("Проверить юнит", self.center.selection_receipt("11111111-1111-4111-8111-111111111111")["label"])
        with self.assertRaises(ValidationError):
            record_incident_choice_result(self.center, "p", created["incident_id"], {
                "request_id": "11111111-1111-4111-8111-111111111111", "choice_id": "unknown-choice"}, api)

    def test_pending_question_carries_bounded_text_choices_and_native_routing(self) -> None:
        created = self.center.create_event("p", "health", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Диск", "body": "Диск заполнен", "dedup_key": "health:disk",
            "event_type": "health.degraded", "source_id": "disk", "host_id": "host",
            "signal_type": "disk", "correlation_id": "corr",
        })
        self.center.record_health_agent_session(created["incident_id"], "native-executor", "", "opencode",
                                                 "sess-executor", "https://agent.bezrabotnyi.com", stage="health-diagnosis")
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-2", "project": "hermes", "recipient": "me",
            "mode": "choice", "message": "Диск заполнен на 98%. token=super-secret-value",
            "choices": [{"label": "Починить", "value": "repair"}, {"label": "Наблюдать", "value": "observe"}],
            "allowed_actors": ["telegram:42"], "incident_id": created["incident_id"],
        })
        pending = self.center.list_human_requests("p")
        self.assertEqual(1, len(pending))
        question = pending[0]
        self.assertEqual("req-2", question["request_id"])
        self.assertEqual(created["incident_id"], question["incident_id"])
        self.assertNotIn("super-secret-value", question["message"])
        self.assertIn("Диск заполнен на 98%", question["message"])
        self.assertEqual(["repair", "observe"], [choice["value"] for choice in question["choices"]])
        self.assertEqual("Починить", question["choices"][0]["label"])
        self.assertEqual("opencode", question["native"]["harness"])
        self.assertEqual("sess-executor", question["native"]["session_id"])
        self.assertNotIn("response_value", question)


class BoundQuestionTests(unittest.TestCase):
    """The bound question really persists, renders, and resumes one native session."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.directory.name) / "notice.sqlite3",
                                         {"p": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.center._connection.close)
        self.herder_calls: list[dict] = []

    def _resume_runner(self, request, **_kwargs):
        self.herder_calls.append(json.loads(request.data.decode()))
        return _Response({"request_id": self.herder_calls[-1]["request_id"], "status": "resuming", "continuation": "resume"})

    def _native_resume(self, request: dict, actor: str = "api") -> dict:
        from notification_center.http_api import resume_human_request_native_session
        return resume_human_request_native_session(self.center, request, actor, self._resume_runner)

    def test_bound_question_persists_the_native_target_and_is_not_polled(self) -> None:
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        created = self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
            "recipient": "me", "mode": "choice", "message": "Диск заполнен. Чинить сейчас?",
            "choices": [{"label": "Да, чинить", "value": "repair"}, {"label": "Наблюдать", "value": "observe"}],
            "allowed_actors": ["telegram:42"],
            "native_target": {"harness": "opencode", "session_id": "ses_exec_1", "cwd": "/srv/app"},
        })
        self.assertEqual("pending", created["state"])
        pending = self.center.list_human_requests("p")[0]
        self.assertEqual("opencode", pending["native"]["harness"])
        self.assertEqual("ses_exec_1", pending["native"]["session_id"])
        self.assertEqual("/srv/app", pending["native"]["cwd"])
        self.assertEqual(["repair", "observe"], [choice["value"] for choice in pending["choices"]])
        self.assertEqual([], self.herder_calls)

    def test_incomplete_native_target_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.center.create_human_request("p", {
                "schema": "ask_human.request.v1", "request_id": "req-partial", "project": "hermes",
                "recipient": "me", "mode": "question", "message": "Вопрос?",
                "allowed_actors": ["telegram:42"],
                "native_target": {"harness": "opencode", "session_id": "ses_1"},
            })

    def test_answer_resumes_the_bound_session_once_through_the_existing_herder_seam(self) -> None:
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
            "recipient": "me", "mode": "choice", "message": "Чинить сейчас?",
            "choices": [{"label": "Да, чинить", "value": "repair"}, {"label": "Наблюдать", "value": "observe"}],
            "allowed_actors": ["telegram:42"],
            "native_target": {"harness": "opencode", "session_id": "ses_exec_1", "cwd": "/srv/app"},
        })
        self.center.bind_human_request_message("p", request_id, "-100", 7)
        resolved = self.center.resolve_human_request_from_telegram(request_id, "telegram:42", "repair")
        calls: list[dict] = []
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            mark_human_request_resolved_card(self.center, resolved, "telegram:42", fake_telegram_api(calls))
            first = self._native_resume(resolved)
            repeated = self._native_resume(resolved)
        self.assertEqual("resuming", first["status"])
        self.assertEqual("resuming", repeated["status"])
        self.assertEqual(2, len(self.herder_calls))
        self.assertEqual("ask.user.completed", self.herder_calls[0]["event"])
        self.assertEqual(request_id, self.herder_calls[0]["request_id"])
        self.assertNotEqual(self.herder_calls[0]["result_ref"], "")
        self.assertNotIn("repair", json.dumps(self.herder_calls[0]))
        self.assertTrue(any("✅ Выбрано: Да, чинить" in str(call.get("text", "")) for call in calls))

    def test_unbound_question_never_resumes_anything(self) -> None:
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-plain", "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Просто уведомление?",
            "allowed_actors": ["telegram:42"],
        })
        resolved = self.center.resolve_human_request_from_telegram("req-plain", "telegram:42", "да")
        self.assertEqual({}, self._native_resume(resolved))
        self.assertEqual([], self.herder_calls)

    def test_card_status_reports_a_real_edit_instead_of_always_marked(self) -> None:
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-card", "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Текст вопроса?",
            "allowed_actors": ["telegram:42"],
        })
        self.center.bind_human_request_message("p", "req-card", "-100", 7)
        resolved = self.center.resolve_human_request_from_telegram("req-card", "telegram:42", "ответ")

        def rejecting_api(method: str, payload: dict) -> dict:
            if method == "editMessageText":
                raise RuntimeError("Bad Request: message is not modified")
            return {"ok": True, "result": payload}

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            unchanged = mark_human_request_resolved_card(self.center, resolved, "api", rejecting_api)
            self.assertEqual("already_marked", unchanged["status"])
            self.assertTrue(unchanged["card"]["edited"])

            def failing_api(method: str, _payload: dict) -> dict:
                if method == "editMessageText":
                    raise RuntimeError("Bad Request: can't parse message text")
                return {"ok": True}

            keyboard_only = mark_human_request_resolved_card(self.center, resolved, "api", failing_api)
        self.assertEqual("keyboard_only_retry", keyboard_only["status"])
        self.assertFalse(keyboard_only["card"]["edited"])


class McpNegotiationTests(unittest.TestCase):
    """The installed SDK's handshake contract must be satisfied, not guessed."""

    def test_initialize_never_answers_with_the_modern_envelope_version(self) -> None:
        # mcp_types.version, as installed for the phone's Fast Agent consumer.
        handshake_versions = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
        for requested in (*handshake_versions, "2026-07-28", "unknown-future", ""):
            with self.subTest(requested=requested):
                answered = local_dispatch({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": requested, "capabilities": {}, "clientInfo": {"name": "canary", "version": "1"}},
                })
                self.assertIn(answered["result"]["protocolVersion"], handshake_versions)
        from mcp import notify_mcp
        self.assertEqual("2025-11-25", notify_mcp.negotiated_protocol_version({"params": {"protocolVersion": "2025-11-25"}}))
        self.assertEqual("2025-11-25", notify_mcp.negotiated_protocol_version({"params": {"protocolVersion": "2026-07-28"}}))

    def test_initialize_then_tools_list_round_trip(self) -> None:
        initialized = local_dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "canary", "version": "1"}},
        })
        self.assertEqual("notify-mcp", initialized["result"]["serverInfo"]["name"])
        self.assertIsNone(local_dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        listed = local_dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        for required in ("ask_human", "noticeplace_pending_questions", "noticeplace_answer", "noticeplace_incident_progress"):
            self.assertIn(required, names)


if __name__ == "__main__":
    unittest.main()