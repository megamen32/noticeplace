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
            if url.endswith("/api/automation/project-launch"):
                return _Response({
                    "outcome": "accepted", "operation_id": json.loads(request.data)["operation_id"],
                    "project": json.loads(request.data)["project"],
                    "selected_harness": json.loads(request.data)["harness"],
                    "model": json.loads(request.data)["model"], "mode": "queue",
                    "session_id": "executor-native", "routing_reason": "declared route",
                    "attempts": [{"harness": "opencode", "outcome": "accepted", "reason": "declared route"}],
                })
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

    def test_repair_first_observation_records_progress_and_never_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            receipt, requests = self._run({"executor": json.dumps({"repair_first": {
                "status": "completed", "plan_id": "repair", "step": "Освободил место и перезапустил юнит",
                "observed_state": "healthy", "source_id": "disk-source", "source_fingerprint": "fp-1",
                "evidence_refs": ["probe:disk"], "trace_refs": ["trace:executor"],
            }})}, root)
        self.assertTrue(receipt["autopilot_armed"])
        self.assertEqual("repair-first", receipt["path"])
        self.assertFalse(receipt["resolved"])
        self.assertTrue(receipt["awaiting_verification"])
        self.assertEqual("disk-source", receipt["verification_required"]["source_id"])
        self.assertEqual("fp-1", receipt["verification_required"]["source_fingerprint"])
        self.assertEqual(["progress"], self._health_actions(requests),
                         "the executor posts progress only: no verification and no resolve")
        self.assertNotIn("plans_count", receipt)
        self.assertFalse(any("plans" in str(getattr(r, "full_url", "")) for r in requests))

    def test_executor_observation_without_a_source_identity_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            receipt, requests = self._run({"executor": json.dumps({"repair_first": {
                "status": "completed", "plan_id": "repair", "step": "Починил",
                "observed_state": "healthy", "source_id": "disk-source", "source_fingerprint": "",
                "evidence_refs": ["probe:disk"], "trace_refs": ["trace:executor"],
            }})}, root)
        self.assertEqual("completed", receipt["status"])
        self.assertFalse(receipt.get("resolved"))
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
        self.assertEqual(1, len([r for r in requests if "project-launch" in str(getattr(r, "full_url", ""))]))

    def test_prompt_states_repair_first_without_forcing_one_final_format(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _, requests = self._run({"executor": json.dumps({"health_question": {"needs_human": True}})}, root)
        message = json.loads([r for r in requests if "project-launch" in str(getattr(r, "full_url", ""))][0].data)["message"]
        self.assertIn("repair-first", message)
        self.assertIn("noticeplace_incident_progress", message)
        self.assertIn("human_request_create", message)
        self.assertNotIn("В итоговый JSON", message)
        self.assertNotIn("оставь в конце один короткий JSON", message)
        self.assertNotIn('"verifier_id"', message)
        self.assertIn("JSON необязателен", message)
        self.assertNotIn("Других финальных форматов нет", message)
        self.assertNotIn("noticeplace_ask_human", message)


class IndependentVerificationTests(unittest.TestCase):
    """Only a separate producer receipt may close a repair-first incident."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.directory.name) / "notice.sqlite3",
                                         {"p": {"project": "fleet-health", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.center._connection.close)
        self.created = self.center.create_event("p", "health", {
            "schema": "notify.event.v1", "project": "fleet-health", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Диск", "body": "Диск заполнен", "dedup_key": "health:disk",
            "event_type": "health.degraded", "source_id": "disk-source", "host_id": "host",
            "signal_type": "disk", "correlation_id": "corr", "source_fingerprint": "fp-1",
        })

    def _executor_progress(self) -> dict[str, object]:
        receipt = {"status": "completed", "agent_receipt": {
            "path": "repair-first", "plan_id": "repair", "step": "Освободил место",
            "observed_state": "healthy", "source_id": "disk-source", "source_fingerprint": "fp-1",
            "evidence_refs": ["probe:disk"], "session_id": "ses-executor", "harness": "opencode",
        }}
        return self.center.record_agent_job_result(
            self.created["incident_id"], "d1", "health-diagnosis", receipt,
            {"source_id": "disk-source", "source_fingerprint": "fp-1"})

    def test_executor_receipt_leaves_the_incident_open(self) -> None:
        outcome = self._executor_progress()
        self.assertTrue(outcome["accepted"])
        self.assertFalse(outcome["resolved"])
        self.assertTrue(outcome["awaiting_verification"])
        self.assertEqual("open", self.center.get_incident(self.created["incident_id"])["state"])

    def test_independent_producer_verification_closes_the_incident(self) -> None:
        self._executor_progress()
        result = self.center.record_health_verification(self.created["incident_id"], "probe:server100", {
            "source_id": "disk-source", "verifier_id": "disk-probe", "verification_id": "probe-1",
            "healthy": True, "fingerprint": "fp-1", "evidence_refs": ["probe:disk"],
        }, "probe-1")
        self.assertTrue(result["healthy"])
        self.assertEqual("resolved", self.center.get_incident(self.created["incident_id"])["state"])

    def test_self_attested_verification_is_refused(self) -> None:
        self._executor_progress()
        # The executor's own actor can never be the verifying authority.
        with self.assertRaises(ValidationError):
            self.center.record_health_verification(self.created["incident_id"], "agent-herder", {
                "source_id": "disk-source", "verifier_id": "disk-probe", "verification_id": "probe-2",
                "healthy": True, "fingerprint": "fp-1", "evidence_refs": ["probe:disk"],
            }, "probe-2")
        self.assertEqual("open", self.center.get_incident(self.created["incident_id"])["state"])

    def test_verification_for_another_fingerprint_does_not_close(self) -> None:
        self._executor_progress()
        self.center.record_health_verification(self.created["incident_id"], "probe:server100", {
            "source_id": "disk-source", "verifier_id": "disk-probe", "verification_id": "probe-3",
            "healthy": True, "fingerprint": "different", "evidence_refs": ["probe:disk"],
        }, "probe-3")
        self.assertEqual("open", self.center.get_incident(self.created["incident_id"])["state"])


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
            if "project-launch" in url:
                body = json.loads(request.data)
                return _Response({
                    "outcome": "accepted", "operation_id": body["operation_id"], "project": body["project"],
                    "selected_harness": body["harness"], "model": body["model"], "mode": body.get("mode", "queue"),
                    "session_id": "route-native", "routing_reason": "declared route",
                    "attempts": [{"harness": body["harness"], "outcome": "accepted", "reason": "declared route"}],
                })
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

    def test_in_flight_reservation_is_terminal_and_never_a_second_executor(self) -> None:
        from notification_center import agent_job_helper as helper
        profile = {"url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "harness": "opencode",
                   "cwd": "/tmp", "mode": "queue", "instruction": "Чини."}
        calls: list[object] = []

        def runner(request, **_kwargs):
            calls.append(request)
            return _Response({"outcome": "in_flight", "operation_id": "op-1", "project": "tg-commentator",
                              "routing_reason": "уже зарезервирован, результат неизвестен",
                              "attempts": [{"harness": "opencode", "outcome": "in_flight", "reason": "reserved"}]})

        result = helper.project_launch(profile, "tg-commentator", "/tmp", "Чини.", runner, operation_id="op-1")
        with self.assertRaisesRegex(RuntimeError, "in_flight"):
            helper._launch_receipt(result, profile, "op-1", "tg-commentator")
        self.assertEqual(1, len(calls), "one reservation attempt, never a second launch")

    def test_unconfigured_project_keeps_the_pinned_subscription_route(self) -> None:
        receipt, requests = self._run("fleet-health", self._policy(), {"opencode": ["minimax-coding-plan/MiniMax-M3.1-Flash-Preview"]})
        self.assertEqual("opencode", receipt["harness"])
        self.assertEqual("minimax-coding-plan/MiniMax-M3.1-Flash-Preview", receipt["model"])

    def test_configured_project_route_applies_and_keeps_its_declared_order(self) -> None:
        policy = self._policy({"TelegramAuto": {"harnesses": ["zcode", "opencode"]}})
        receipt, requests = self._run("TelegramAuto", policy, {"zcode": ["account:zai-individual-coding-plan/GLM-5.3-Flash$high"]})
        # Agent Herder owns admission and fallback, so NoticePlace sends the
        # declared order once instead of probing and looping itself.
        self.assertEqual("zcode", receipt["harness"])
        self.assertEqual("account:zai-individual-coding-plan/GLM-5.3-Flash$high", receipt["model"])
        launch = [json.loads(r.data) for r in requests if "project-launch" in str(r.full_url)][0]
        self.assertEqual("TelegramAuto", launch["project"])
        self.assertEqual("zcode", launch["harness"])
        self.assertEqual(32, len(launch["operation_id"]))
        self.assertEqual("TelegramAuto", launch["project"])

    def test_declared_aliases_resolve_case_insensitively_like_herder(self) -> None:
        policy = self._policy({"TelegramAuto": {"aliases": ["tg-commentator", "autotelegramviews"],
                                                "harnesses": ["zcode"]}})
        for project in ("tg-commentator", "TG-Commentator", "autotelegramviews", "AutoTelegramViews"):
            with self.subTest(project=project):
                receipt, _ = self._run(project, policy, {"zcode": ["account:zai-individual-coding-plan/GLM-5.3-Flash$high"]})
                self.assertEqual("zcode", receipt["harness"])

    def test_undeclared_project_name_is_never_guessed(self) -> None:
        policy = self._policy({"TelegramAuto": {"aliases": ["tg-commentator"], "harnesses": ["zcode"]}})
        receipt, _ = self._run("autosell", policy, {"zcode": ["account:zai-individual-coding-plan/GLM-5.3-Flash$high"]})
        self.assertEqual("opencode", receipt["harness"])

    def test_admission_receipt_is_authoritative_and_never_relaunched(self) -> None:
        policy = self._policy({"TelegramAuto": {"aliases": ["tg-commentator"], "harnesses": ["opencode", "zcode"]}})
        requests: list[object] = []
        profile = {
            "url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "name": "health",
            "harness": "opencode", "cwd": "/tmp", "mode": "queue", "instruction": "Чини.",
            "orchestrator_name": "planner", "orchestrator_model": "configured",
            "orchestrator_requested_model": "configured",
        }
        event = {"schema": "notify.agent-job.v1", "job_id": "health-diagnosis",
                 "incident": {"id": "inc-1", "project": "tg-commentator", "severity": "critical",
                              "title": "t", "body": "b", "dedup_key": "d", "occurrences": 1}}

        def runner(request, **_kwargs):
            url = str(getattr(request, "full_url", ""))
            if url.endswith("/api/automation/launch-policy"):
                return _Response(policy)
            requests.append(request)
            return _Response({"outcome": "accepted", "operation_id": "op-admission",
                              "project": "tg-commentator", "selected_harness": "zcode",
                              "model": "account:zai-individual-coding-plan/GLM-5.3-Flash$high", "mode": "queue",
                              "cwd": "/srv/herder-project-route",
                              "session_id": "zcode-native", "routing_reason": "declared route",
                              "attempts": [{"harness": "zcode", "outcome": "accepted", "reason": "declared route"}]})

        with patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "token")), \
             patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}) as diagnosis:
            receipt = agent_job_helper.run_profile("health-diagnosis", event, Path("unused"), runner=runner, profile_override=profile)
        self.assertEqual("zcode", receipt["harness"], "the launch receipt outranks the requested harness")
        self.assertEqual("/srv/herder-project-route", receipt["cwd"])
        launched_profile = diagnosis.call_args.args[0]
        for key in ("harness", "model", "cwd"):
            self.assertEqual(receipt[key], launched_profile[key], f"polling uses receipt.{key}")
        armed = [r for r in requests if "/api/autopilot/sessions/" in r.full_url]
        self.assertEqual(1, len(armed))
        self.assertEqual(receipt["cwd"], json.loads(armed[0].data)["cwd"])
        self.assertIn("/sessions/zcode/", armed[0].full_url)
        self.assertEqual("accepted", receipt["admission"]["status"])
        self.assertEqual(1, len(receipt["admission"]["attempts"]))
        launches = [r for r in requests if "project-launch" in str(r.full_url)]
        self.assertEqual(1, len(launches), "exactly one admission call, never a second executor")
        # The durable operation id is stable for the same incident and stage, so
        # a repeat is read from Agent Herder's log instead of launching again.
        sent_operation = json.loads(launches[0].data)["operation_id"]
        self.assertEqual(32, len(sent_operation))
        self.assertEqual(sent_operation, agent_job_helper._operation_id(event, "health-diagnosis"))


@patch.dict(os.environ, {"TELEGRAM_CALLBACK_SECRET": "x" * 32})
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

    def test_uuid_request_id_uses_the_compact_signed_callback(self) -> None:
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
            "recipient": "me", "mode": "choice", "message": "Что делать?",
            "choices": [{"label": "Починить", "value": "repair"}, {"label": "Наблюдать", "value": "observe"}],
            "allowed_actors": ["telegram:42"],
        })
        self.center.bind_human_request_message("p", request_id, "-100", 7)
        data = self.codec.encode_human_choice(request_id, "1")
        self.assertLessEqual(len(data.encode()), 64)
        self.assertNotIn(request_id, data)
        self.assertEqual((request_id, "1"), self.codec.decode_human_choice(data, self.center.human_request_ids()))
        self.assertIsNone(self.codec.decode_human_choice(data, ["11111111-1111-4111-8111-111111111111"]))

        edits: list[dict[str, object]] = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "getUpdates":
                return {"ok": True, "result": []}
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        poller = self._poller(api)
        with patch.object(poller, "_api", api):
            poller._handle_callback({
                "id": "cb", "from": {"id": 42}, "data": data,
                "message": {"chat": {"id": -100}, "message_id": 7, "text": "Что делать?"},
            })
        self.assertTrue(any("✅ Выбрано: Наблюдать" in str(edit.get("text", "")) for edit in edits))
        self.assertEqual("observe", self.center.selection_receipt(request_id)["choice_id"])

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


@patch.dict(os.environ, {"TELEGRAM_CALLBACK_SECRET": "x" * 32})
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
        self.assertEqual("sent", repeated["status"])
        self.assertEqual(1, len(self.herder_calls))
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


class CanonicalCompletionTests(unittest.TestCase):
    """HTTP, button, and free text share one completion and one admission."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.directory.name) / "notice.sqlite3",
                                         {"p": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.center._connection.close)
        self.codec = TelegramActionCodec("x" * 32)
        self.herder: list[dict] = []

    def _resume_runner(self, request, **_kwargs):
        body = json.loads(request.data.decode())
        self.herder.append(body)
        return _Response({"request_id": body["request_id"], "status": "resuming", "continuation": "resume"})

    def _bound_request(self, request_id: str = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44") -> None:
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
            "recipient": "me", "mode": "choice", "message": "Починить сейчас?",
            "choices": [{"label": "Да, починить", "value": "repair"},
                        {"label": "Наблюдать", "value": "observe"}],
            "allowed_actors": ["telegram:42"], "kind": "user",
            "native_target": {"harness": "opencode", "session_id": "ses_exec_1", "cwd": "/srv/app"},
        })
        self.center.bind_human_request_message("p", request_id, "-100", 7)

    def test_telegram_button_completes_card_and_native_once(self) -> None:
        from notification_center.http_api import complete_human_request
        self._bound_request()
        edits: list[dict] = []

        def api(method: str, payload: dict) -> dict:
            if method == "getUpdates":
                return {"ok": True, "result": []}
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        poller = TelegramInteractionPoller(self.center, "token", {"42"}, self.codec, api=api)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}), \
             patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            resume.side_effect = lambda request_id, harness, session, cwd, result_ref, runner: {"request_id": request_id, "status": "resuming"}
            poller._handle_callback({
                "id": "cb", "from": {"id": 42},
                "data": self.codec.encode_human_choice("6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44", "0"),
                "message": {"chat": {"id": -100}, "message_id": 7, "text": "Починить сейчас?"},
            })
        self.assertTrue(any("✅ Выбрано: Да, починить" in str(edit.get("text", "")) for edit in edits))
        resume.assert_called_once()
        self.assertEqual("repair", self.center.selection_receipt("6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44")["choice_id"])

    def test_free_text_reply_completes_card_and_native_once(self) -> None:
        from notification_center.http_api import complete_human_request
        request_id = "9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f"
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Что делать с диском?",
            "allowed_actors": ["telegram:42"], "kind": "user",
            "native_target": {"harness": "opencode", "session_id": "ses_exec_1", "cwd": "/srv/app"},
        })
        self.center.bind_human_request_message("p", request_id, "-100", 7)
        self.assertIsNotNone(self.center.human_request_for_reply("-100", 7, "telegram:42"))
        edits: list[dict] = []

        def api(method: str, payload: dict) -> dict:
            if method == "getUpdates":
                return {"ok": True, "result": []}
            edits.append(payload)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100}}}

        poller = TelegramInteractionPoller(self.center, "token", {"42"}, self.codec, api=api)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}), \
             patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            resume.side_effect = lambda request_id, harness, session, cwd, result_ref, runner: {"request_id": request_id, "status": "resuming"}
            poller._handle_message({
                "message_id": 99, "chat": {"id": -100}, "from": {"id": 42},
                "text": "Чинить сейчас",
                "reply_to_message": {"message_id": 7, "chat": {"id": -100}},
            })
        self.assertTrue(any("✅ Выбрано" in str(edit.get("text", "")) for edit in edits),
                        "a free-text answer marks the same card")
        resume.assert_called_once()
        resolved = self.center.get_human_request_from_telegram(request_id)
        self.assertEqual("resolved", resolved["state"])
        self.assertEqual("Чинить сейчас", resolved["response_value"])

    def test_conflicting_repeat_keeps_the_first_value_and_never_resumes_again(self) -> None:
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        self._bound_request(request_id)
        first = self.center.resolve_human_request_for_surface(request_id, "telegram:42", "repair")
        self.assertEqual("repair", first["response_value"])
        repeat = self.center.resolve_human_request_for_surface(request_id, "telegram:42", "observe")
        self.assertEqual("repair", repeat["response_value"], "the first accepted value stays canonical")

    def test_bound_question_is_never_expired_or_cancelled_here(self) -> None:
        import time
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        with self.assertRaises(ValidationError):
            self.center.create_human_request("p", {
                "schema": "ask_human.request.v1", "request_id": "req-secret-bound", "project": "hermes",
                "recipient": "me", "mode": "question", "message": "Пароль?",
                "allowed_actors": ["telegram:42"], "kind": "secret",
                "native_target": {"harness": "opencode", "session_id": "ses_1", "cwd": "/srv/app"},
            })
        self._bound_request(request_id)
        with self.center._lock, self.center._connection:
            self.center._connection.execute("UPDATE human_requests SET expires_at = ? WHERE request_id = ?",
                                            (time.time() - 60, request_id))
        view = self.center.list_human_requests("p")
        self.assertEqual([request_id], [question["request_id"] for question in view],
                         "a bound question ignores its own expiry")
        with self.assertRaises(ValidationError):
            self.center.cancel_human_request("p", request_id, "operator")

    def test_choice_question_needs_two_to_four_options(self) -> None:
        from mcp import notify_mcp
        for options in ([{"label": "Один", "value": "only"}], [{"label": str(i), "value": str(i)} for i in range(5)]):
            with self.subTest(options=len(options)), self.assertRaises(ValueError):
                notify_mcp.tool_noticeplace_ask_human({
                    "request_id": "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44", "harness": "opencode",
                    "session_id": "ses_1", "cwd": "/srv", "message": "Вопрос?", "choices": options,
                })

    def test_herder_emergency_cancel_terminates_a_bound_question_without_resuming(self) -> None:
        request_id = "6f1d4b8e-9a3c-4d21-9f0b-2f6c1d5e7a44"
        self._bound_request(request_id)
        self.assertTrue(self.center.human_request_is_bound(request_id))
        cancelled = self.center.cancel_human_request("p", request_id, "herder", reason="withdrawn")
        self.assertEqual("cancelled", cancelled["state"])
        self.assertIsNone(cancelled["response_value"], "a withdrawn question never carries an answer")

    def test_legacy_unbound_question_still_expires_and_cancels(self) -> None:
        import time
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-plain", "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Уведомление?",
            "allowed_actors": ["telegram:42"],
            "expires_at": time.time() - 60,
        })
        self.assertEqual([], self.center.list_human_requests("p"))
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-cancel", "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Уведомление?",
            "allowed_actors": ["telegram:42"],
        })
        self.assertEqual("cancelled", self.center.cancel_human_request("p", "req-cancel", "operator")["state"])

    def test_secret_result_returns_only_an_opaque_reference(self) -> None:
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-secret", "project": "hermes",
            "recipient": "me", "mode": "question", "message": "Нужен ключ",
            "allowed_actors": ["telegram:42"], "kind": "secret",
        })
        with self.assertRaises(ValidationError):
            self.center.resolve_human_request_for_surface("req-secret", "telegram:42", "the-answer")
        receipt = self.center.human_request_result_receipt("p", "req-secret")
        self.assertEqual("secret", receipt["kind"])
        self.assertIsNone(receipt["answer"])
        self.assertEqual(36, len(receipt["result_ref"]))


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
