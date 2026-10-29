"""Focused local contract tests for Notice-owned task decisions N61-N63."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from notification_center.core import AuthorizationError, IdempotencyConflict, NotificationCenter, ValidationError
from notification_center.http_api import DeliveryWorker, deliver_human_completion, send_human_request_telegram


class _TrustedTestAuthority:
    """Test-only injected issuer; production has no configured issuer today."""

    def __init__(self) -> None:
        self.actors = {"telegram:42"}
        self.timeout_enabled = True
        self.board_answer = None

    def __call__(self, mandate: dict[str, object]) -> dict[str, object]:
        return mandate

    def is_actor_allowed(self, _context: dict[str, object], actor: str) -> bool:
        return actor in self.actors

    def timeout_allowed_now(self, _context: dict[str, object]) -> bool:
        return self.timeout_enabled

    def verify_board_answer(self, _context, request_ref, proposal_ref):
        proof = self.board_answer
        if not isinstance(proof, dict) or proof.get("request_id") != request_ref or proof.get("proposal_ref") != proposal_ref:
            return None
        return proof


def _race_task_answer_or_timeout(database: str, role: str, gate: Any, output: Any) -> None:
    """Run one independent process against the same file-backed CAS row."""
    center = NotificationCenter(
        database, {"token": {"project": "airlock", "max_severity": "critical"}},
        task_decision_verifier=_TrustedTestAuthority(),
    )
    gate.wait(5)
    try:
        if role == "answer":
            center.resolve_human_request("token", _request_id(), "telegram:42", "continue")
        else:
            center.task_decisions.resolve_due(now_epoch=1900)
        output.put((role, "ok"))
    except ValidationError as error:
        output.put((role, str(error)))
    finally:
        center._connection.close()


def _request_id() -> str:
    # Identity is derived from the fixed fixture below, independent of process.
    import hashlib
    import json
    identity = json.dumps(["board/task-17", "choose-next-step", "v3"], separators=(",", ":"))
    return "td-" + hashlib.sha256(identity.encode()).hexdigest()[:40]


def _read_deadline_after_restart(database: str, request_id: str, output: Any) -> None:
    center = NotificationCenter(database, {"token": {"project": "airlock"}})
    output.put(center.task_decisions.notification_state(request_id))
    center._connection.close()


def _reconcile_control_after_restart(database: str, request_id: str, output: Any) -> None:
    """A fresh process queries the stable control ID before any retry."""
    center = NotificationCenter(
        database, {"token": {"project": "airlock", "max_severity": "critical"}},
        task_decision_verifier=_TrustedTestAuthority(),
    )

    class ActionOwner:
        owner_id = "userio"

        def query_control(self, control_id: str) -> dict[str, object]:
            command = center.task_decisions.control_command(request_id)
            return {"status": "not_found", "retry_safe": True, "control_id": control_id,
                    "execution": command["execution"]}

        def revoke(self, command: dict[str, object], idempotency_key: str) -> dict[str, object]:
            return {"status": "acknowledged", "receipt_ref": "reconciled-revoke",
                    "effective_epoch": command["epoch"], "control_id": idempotency_key,
                    "execution": command["execution"]}

    prior_id = center.task_decisions.control_command(request_id)["control_id"]
    worker = DeliveryWorker(center, object(), task_action_owner=ActionOwner())
    processed = worker.run_once()
    current = center.get_human_request("token", request_id)["task_decision"]
    output.put((processed, prior_id, current["control_id"], current["control_state"], current["control_ack_ref"]))
    center._connection.close()


def _mandate(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "task_ref": "board/task-17", "purpose": "choose-next-step", "version": "v3",
        "mandate_ref": "grant:task-17:v3", "project": "airlock", "recipient": "operator",
        "message": "Choose the permitted next step.",
        "allowed_actors": ["telegram:42"],
        "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
        "timeout_value": "continue", "continuation": {
            "task_ref": "board/task-17", "operation_id": "grant:task-17:v3", "board_version": 23,
            "started_by": "nikita", "host_id": "roomhacker-server-100", "harness": "codex",
            "session_id": "session-123", "turn_id": "turn-456",
        },
    }
    value.update(changes)
    if "continuation" not in changes:
        continuation = dict(value["continuation"])
        continuation["task_ref"] = value["task_ref"]
        continuation["operation_id"] = value["mandate_ref"]
        value["continuation"] = continuation
    return value


class TaskDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "notice.sqlite3"
        self.center = NotificationCenter(
            self.path, {"token": {"project": "airlock", "max_severity": "critical"}},
            task_decision_verifier=_TrustedTestAuthority(),
        )

    def tearDown(self) -> None:
        self.center._connection.close()
        self.tempdir.cleanup()

    def create(self, **changes: object) -> dict[str, object]:
        return self.center.create_task_decision_request("token", _mandate(**changes))

    def sent(self, request: dict[str, object], at: float = 100.0) -> None:
        request_id = str(request["request_id"])
        self.assertTrue(self.center.task_decisions.reserve_notification(request_id))
        with mock.patch("notification_center.task_decisions.time.time", return_value=at):
            self.center.bind_human_request_message("token", request_id, "42", 91)

    def test_default_denies_and_canonical_identity_reuses_without_conflicting_payloads(self) -> None:
        denied = NotificationCenter(self.path.parent / "denied.sqlite3", {"token": {"project": "airlock"}})
        with self.assertRaises(AuthorizationError):
            denied.create_task_decision_request("token", _mandate())
        with self.assertRaises(AuthorizationError):
            denied.create_human_request("token", {}, _task_context={"task_ref": "caller-supplied"},
                                        _task_input_sha256="a" * 64)
        request = self.create()
        replay = self.create()
        self.assertEqual(request["request_id"], replay["request_id"])
        self.assertFalse(request["idempotent"])
        self.assertTrue(replay["idempotent"])
        with self.assertRaisesRegex(ValidationError, "idempotency conflict"):
            self.create(message="A different question under the same task purpose/version.")
        with self.assertRaisesRegex(ValidationError, "not allowed"):
            self.center.resolve_human_request("token", str(request["request_id"]), "telegram:7", "continue")
        denied._connection.close()

    def test_opencode_task_decision_alias_reuses_the_herder_uuid_notice_row(self):
        continuation = {
            "task_ref": "board/task-17",
            "operation_id": "grant:task-17:v3",
            "board_version": 23,
            "started_by": "nikita",
            "host_id": "qa-board",
            "harness": "opencode",
            "session_id": "opencode-session-17",
            "project_id": "qa-project-1",
            "cwd": "/srv/qa/project",
            "input_id": "input-message-18",
            "job_id": "dispatch-job-19",
            "human_request_id": "d920a01e-a5f3-4dce-a640-b65b8e312433",
        }

        request = self.create(continuation=continuation)

        rows = self.center._connection.execute("SELECT request_id,task_decision_ref,task_context_json FROM human_requests").fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("d920a01e-a5f3-4dce-a640-b65b8e312433", rows[0]["request_id"])
        self.assertEqual(_request_id(), rows[0]["task_decision_ref"])
        self.assertEqual(_request_id(), request["request_id"])
        self.assertEqual("d920a01e-a5f3-4dce-a640-b65b8e312433", request["canonical_request_id"])

    def test_board_answer_and_herder_uuid_result_share_one_cas_row_and_outbox(self):
        herder_request_id = "d920a01e-a5f3-4dce-a640-b65b8e312433"
        continuation = {
            "task_ref": "board/task-17", "operation_id": "grant:task-17:v3", "board_version": 23,
            "started_by": "nikita", "host_id": "qa-board", "harness": "opencode",
            "session_id": "opencode-session-17", "project_id": "qa-project-1",
            "cwd": "/srv/qa/project", "input_id": "input-message-18", "job_id": "dispatch-job-19",
            "human_request_id": herder_request_id,
        }
        request = self.create(
            continuation=continuation,
            choices=[{"label": "CSV", "value": "csv"}, {"label": "Markdown", "value": "markdown"}],
            timeout_value="csv",
        )
        task_decision_ref = str(request["request_id"])
        self.sent(request, at=100.0)
        self.center._task_decision_verifier.board_answer = {
            "request_id": task_decision_ref,
            "proposal_ref": "board-proposal-1",
            "actor": "testlogin",
            "value": "csv",
            "card_version": 24,
            "stop_epoch": "initial-input-hash",
            "source_message_id": "assistant-message-20",
            "source_job_id": "dispatch-job-19",
            "human_request_id": herder_request_id,
            "question_hash": "a" * 64,
        }

        with mock.patch("notification_center.core.time.time", return_value=1500.0):
            accepted = self.center.resolve_task_decision_from_board("token", task_decision_ref, "board-proposal-1")
        current_request = self.center.get_human_request("token", herder_request_id)
        completion = accepted["completion"]
        idem = f"human-request:{herder_request_id}:attempt-1"
        herder_record = {
            "requestId": herder_request_id, "status": "resumed", "resultRef": completion["result_ref"],
            "target": {"agent": "opencode", "sessionId": "opencode-session-17", "cwd": "/srv/qa/project"},
            "idempotencyKey": idem,
            "receipt": f"agent-herder://opencode/opencode-session-17/{idem}",
        }
        with mock.patch("notification_center.http_api._human_completion_runner", return_value=object()), \
             mock.patch("notification_center.agent_job_helper.resume_bound_native_session", return_value={
                 "request_id": herder_request_id, "status": "resumed",
             }) as resume, \
             mock.patch("notification_center.agent_job_helper.read_herder_human_request", return_value=herder_record):
            dispatch = deliver_human_completion(self.center, current_request, "exmanager-board", runner=object())
        with mock.patch("notification_center.core.time.time", return_value=2000.0):
            duplicate = self.center.resolve_task_decision_from_board("token", task_decision_ref, "board-proposal-1")
        herder_result = self.center.human_request_result_receipt("token", herder_request_id)

        self.assertEqual("accepted", accepted["answer_status"])
        self.assertEqual("duplicate", duplicate["answer_status"])
        self.assertEqual(herder_request_id, accepted["canonical_request_id"])
        self.assertEqual(("resumed", "accepted", herder_request_id, completion["result_ref"], idem,
                          herder_record["receipt"]),
                         (dispatch["status"], dispatch["state"], dispatch["request_id"],
                          dispatch["result_ref"], dispatch["idempotency_key"], dispatch["receipt"]))
        self.assertEqual((herder_request_id, "opencode", "opencode-session-17", "/srv/qa/project"),
                         resume.call_args.args[:4])
        self.assertEqual(herder_request_id, herder_result["request_id"])
        self.assertEqual(("resolved", "csv"), (herder_result["state"], herder_result["answer"]))
        self.assertEqual(("accepted", herder_request_id, completion["result_ref"], idem, herder_record["receipt"]),
                         (herder_result["continuation"]["state"], herder_result["continuation"]["request_id"],
                          herder_result["continuation"]["result_ref"], herder_result["continuation"]["idempotency_key"],
                          herder_result["continuation"]["receipt"]))
        self.assertEqual(dispatch["input_id"], herder_result["continuation"]["input_id"])
        self.assertEqual({"harness": "opencode", "session_id": "opencode-session-17", "cwd": "/srv/qa/project"},
                         herder_result["native"])
        self.assertEqual(herder_request_id, accepted["completion"]["registry_request_id"])
        self.assertEqual(1, self.center._connection.execute("SELECT COUNT(*) FROM human_requests").fetchone()[0])
        self.assertEqual(1, self.center._connection.execute("SELECT COUNT(*) FROM human_request_completions").fetchone()[0])

    def test_board_answer_after_delivered_deadline_cannot_win_while_timeout_worker_lags(self) -> None:
        request = self.create()
        request_id = str(request["request_id"])
        canonical_request_id = self.center.task_decisions.canonical_request_id(request_id)
        self.sent(request, at=100.0)
        self.center._task_decision_verifier.board_answer = {
            "request_id": request_id,
            "proposal_ref": "late-board-proposal",
            "actor": "testlogin",
            "value": "continue",
            "card_version": 24,
            "stop_epoch": "initial-input-hash",
            "source_message_id": "assistant-message-20",
            "source_job_id": "dispatch-job-19",
            "human_request_id": canonical_request_id,
            "question_hash": "a" * 64,
        }

        with mock.patch("notification_center.core.time.time", return_value=1900.0):
            with self.assertRaisesRegex(IdempotencyConflict, "arrived after its deadline"):
                self.center.resolve_task_decision_from_board("token", request_id, "late-board-proposal")
        still_pending = self.center.get_human_request("token", request_id)
        self.assertEqual("pending", still_pending["state"])
        self.assertIsNone(still_pending["response_value"])
        self.assertIsNone(still_pending["task_decision"]["source"])

        self.assertEqual(1, self.center.task_decisions.resolve_due(now_epoch=1900.0))
        timed_out = self.center.get_human_request("token", request_id)
        self.assertEqual("expired", timed_out["state"])
        self.assertEqual(("timeout", "continue"),
                         (timed_out["task_decision"]["source"], timed_out["task_decision"]["value"]))
        self.assertIsNone(timed_out["response_value"])

    def test_board_answer_deadline_is_rechecked_in_the_resolution_cas(self) -> None:
        request = self.create()
        request_id = str(request["request_id"])
        self.sent(request, at=100.0)
        self.center._task_decision_verifier.board_answer = {
            "request_id": request_id, "proposal_ref": "boundary-proposal",
            "actor": "testlogin", "value": "continue", "card_version": 24,
            "stop_epoch": "initial-input-hash", "source_message_id": "assistant-message-20",
            "source_job_id": "dispatch-job-19",
            "human_request_id": self.center.task_decisions.canonical_request_id(request_id),
            "question_hash": "a" * 64,
        }

        with mock.patch("notification_center.core.time.time", side_effect=[1899.999, 1900.0]):
            with self.assertRaisesRegex(IdempotencyConflict, "arrived after its deadline"):
                self.center.resolve_task_decision_from_board("token", request_id, "boundary-proposal")
        current = self.center.get_human_request("token", request_id)
        self.assertEqual("pending", current["state"])
        self.assertIsNone(current["response_value"])

    def test_revoked_current_actor_policy_blocks_answer_and_timeout(self) -> None:
        request = self.create()
        self.sent(request, at=100.0)
        self.center._task_decision_verifier.actors.clear()
        with self.assertRaisesRegex(AuthorizationError, "current task actor policy"):
            self.center.resolve_human_request("token", str(request["request_id"]), "telegram:42", "continue")
        self.center._task_decision_verifier.timeout_enabled = False
        self.assertEqual(0, self.center.task_decisions.resolve_due(now_epoch=1900.0))
        state = self.center.get_human_request("token", str(request["request_id"]))
        self.assertEqual("pending", state["state"])
        self.assertEqual("clear", state["task_decision"]["control_state"])

    def test_only_first_success_receipt_starts_immutable_thirty_minute_clock(self) -> None:
        request = self.create()
        request_id = str(request["request_id"])
        self.assertEqual("queued", self.center.task_decisions.notification_state(request_id)["state"])
        self.assertIsNone(self.center.task_decisions.notification_state(request_id)["deadline_at"])
        self.assertTrue(self.center.task_decisions.reserve_notification(request_id))
        self.assertFalse(self.center.task_decisions.reserve_notification(request_id))
        self.center.task_decisions.mark_notification_unknown(request_id)
        self.assertEqual("unknown", self.center.task_decisions.notification_state(request_id)["state"])
        self.assertIsNone(self.center.task_decisions.notification_state(request_id)["deadline_at"])

        delivered = self.create(task_ref="board/task-18")
        delivered_id = str(delivered["request_id"])
        self.assertTrue(self.center.task_decisions.reserve_notification(delivered_id))
        with mock.patch("notification_center.task_decisions.time.time", return_value=1000.0):
            self.center.bind_human_request_message("token", delivered_id, "42", 92)
        first = self.center.task_decisions.notification_state(delivered_id)
        self.assertEqual(("sent", 1000.0, 2800.0), (first["state"], first["notified_at"], first["deadline_at"]))
        with mock.patch("notification_center.task_decisions.time.time", return_value=3000.0):
            self.center.bind_human_request_message("token", delivered_id, "42", 92)
        self.assertEqual(first, self.center.task_decisions.notification_state(delivered_id))

    def test_existing_telegram_sender_receipt_arms_clock_and_unknown_never_replays(self) -> None:
        request = self.create()
        request_id = str(request["request_id"])
        class FakeTransport:
            submit_result: dict[str, object] = {"outcome": "accepted", "sender_id": "8810909089",
                                                 "chat_id": "-1004352022764", "message_id": 93,
                                                 "accepted_at": 1000.0}
            query_result: dict[str, object] = {"outcome": "not_found"}
            submits = 0
            queries = 0

            def __init__(self, _base_url, _bearer):
                pass

            def readiness(self):
                return {"ok": True}

            def submit_question(self, _request_id, _operation_id, _text):
                type(self).submits += 1
                if isinstance(type(self).submit_result, Exception):
                    raise type(self).submit_result
                return type(self).submit_result

            def query_question(self, _request_id, _operation_id):
                type(self).queries += 1
                return type(self).query_result

        FakeTransport.submits = 0
        FakeTransport.queries = 0
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", FakeTransport), \
             mock.patch("notification_center.task_decisions.time.time", return_value=1000.0):
            sent = send_human_request_telegram(self.center, "token", request)
        self.assertEqual("sent", sent["status"])
        self.assertEqual(2800.0, self.center.task_decisions.notification_state(request_id)["deadline_at"])

        uncertain = self.create(task_ref="board/task-20")
        uncertain_id = str(uncertain["request_id"])
        FakeTransport.submit_result = {"outcome": "unknown"}
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", FakeTransport):
            initial = send_human_request_telegram(self.center, "token", uncertain)
            replay = send_human_request_telegram(self.center, "token", uncertain)
        self.assertEqual("unknown", initial["status"])
        self.assertEqual("unknown", replay["status"])
        self.assertIsNone(self.center.task_decisions.notification_state(uncertain_id)["deadline_at"])
        self.assertEqual(2, FakeTransport.submits)
        self.assertEqual(1, FakeTransport.queries)

    def test_exact_deadline_worker_timeout_is_not_a_fabricated_human_answer(self) -> None:
        request = self.create()
        self.sent(request, at=100.0)
        with mock.patch("notification_center.task_decisions.time.time", return_value=1899.999):
            DeliveryWorker(self.center, object()).run_once()
        still_pending = self.center.get_human_request("token", str(request["request_id"]))
        self.assertEqual("pending", still_pending["state"])
        with mock.patch("notification_center.task_decisions.time.time", return_value=1900.0):
            DeliveryWorker(self.center, object()).run_once()
        result = self.center.get_human_request("token", str(request["request_id"]))
        self.assertEqual("expired", result["state"])
        self.assertEqual("timeout", result["task_decision"]["source"])
        self.assertEqual("continue", result["task_decision"]["value"])
        self.assertIsNone(result["response_value"])
        self.assertIsNone(result["response_actor"])
        self.assertTrue(self.center.task_decisions.continuation_allowed(str(request["request_id"])))

    def test_answer_and_timeout_race_across_processes_has_one_canonical_winner(self) -> None:
        request = self.create()
        self.sent(request, at=100.0)
        context = multiprocessing.get_context("fork")
        gate, output = context.Event(), context.Queue()
        workers = [context.Process(target=_race_task_answer_or_timeout, args=(str(self.path), role, gate, output))
                   for role in ("answer", "timeout")]
        for worker in workers:
            worker.start()
        gate.set()
        for worker in workers:
            worker.join(8)
            self.assertEqual(0, worker.exitcode)
        outcomes = [output.get(timeout=1) for _ in workers]
        self.assertEqual(2, len(outcomes))
        row = self.center._connection.execute(
            "SELECT state,response_value,response_actor,task_decision_source,task_decision_value "
            "FROM human_requests WHERE request_id=?", (str(request["request_id"]),),
        ).fetchone()
        self.assertIn(row["task_decision_source"], {"human", "timeout"})
        if row["task_decision_source"] == "timeout":
            self.assertEqual(("expired", None, None, "continue"),
                             (row["state"], row["response_value"], row["response_actor"], row["task_decision_value"]))
        else:
            self.assertEqual(("resolved", "continue", "telegram:42", "continue"),
                             (row["state"], row["response_value"], row["response_actor"], row["task_decision_value"]))

    def test_restart_keeps_deadline_and_late_answer_is_a_new_durable_instruction(self) -> None:
        request = self.create()
        self.sent(request, at=100.0)
        request_id = str(request["request_id"])
        self.center._connection.close()
        self.center = NotificationCenter(
            self.path, {"token": {"project": "airlock", "max_severity": "critical"}},
            task_decision_verifier=_TrustedTestAuthority(),
        )
        context = multiprocessing.get_context("fork")
        output = context.Queue()
        restart = context.Process(target=_read_deadline_after_restart, args=(str(self.path), request_id, output))
        restart.start()
        restart.join(5)
        self.assertEqual(0, restart.exitcode)
        self.assertEqual({"state": "sent", "notified_at": 100.0, "deadline_at": 1900.0}, output.get(timeout=1))
        self.assertEqual(1, self.center.task_decisions.resolve_due(now_epoch=1900.0))
        late_answer = self.center.resolve_human_request_for_surface(request_id, "telegram:42", "continue")
        self.assertTrue(late_answer["late_instruction_recorded"])
        self.assertIsNone(late_answer["response_value"])
        late = self.center.resolve_human_request_for_surface(request_id, "telegram:42", "stop")
        self.assertTrue(late["control_requested"])
        decision = self.center.get_human_request("token", request_id)
        self.assertEqual("timeout", decision["task_decision"]["source"])
        self.assertEqual("continue", decision["task_decision"]["value"])
        self.assertIsNone(decision["response_value"])

    def test_stop_acknowledgment_gates_continuation_and_native_humanstop_stays_unbounded(self) -> None:
        request = self.create(task_ref="board/task-19")
        self.sent(request, at=100.0)
        request_id = str(request["request_id"])
        self.center.resolve_human_request_for_surface(request_id, "telegram:42", "continue")
        control = self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop task")
        self.assertFalse(self.center.task_decisions.continuation_allowed(request_id))
        command = self.center.task_decisions.control_command(request_id)

        class ActionOwner:
            owner_id = "userio"

            def revoke(self, received: dict[str, object], _idempotency_key: str) -> dict[str, object]:
                return {"status": "acknowledged", "receipt_ref": "userio-revoke-1",
                        "effective_epoch": received["epoch"], "accepted_effects": ["send:accepted"],
                        "unknown_effects": ["send:unknown"], "control_id": received["control_id"],
                        "execution": received["execution"]}

        DeliveryWorker(self.center, object(), task_action_owner=ActionOwner()).run_once()
        acknowledged = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("acknowledged", acknowledged["control_state"])
        self.assertEqual({"accepted": ["send:accepted"], "unknown": ["send:unknown"]}, acknowledged["control_effects"])
        self.assertFalse(self.center.task_decisions.continuation_allowed(request_id))
        with self.assertRaisesRegex(ValidationError, "terminal"):
            self.center.task_decisions.request_control(request_id, "telegram:42", "hold", "try to reopen")
        with self.assertRaisesRegex(ValidationError, "hold must be acknowledged"):
            self.center.task_decisions.resume_after_hold(request_id, "telegram:42", "resume-after-stop")
        renewed = self.create(task_ref="board/task-19", version="v4")
        self.assertNotEqual(request_id, renewed["request_id"])
        self.assertEqual("clear", renewed["task_decision"]["control_state"])

        native = self.center.create_human_request("token", {
            "schema": "ask_human.request.v1", "request_id": "native-humanstop",
            "project": "airlock", "recipient": "operator", "mode": "question", "message": "Wait?",
            "choices": None, "allowed_actors": ["telegram:42"], "expires_at": time.time() - 10,
            "native_target": {"harness": "codex", "session_id": "session-native", "cwd": "/srv/project"},
        })
        self.assertEqual("pending", self.center.get_human_request("token", str(native["request_id"]))["state"])

    def test_claimed_control_reconciles_after_fresh_process_restart(self) -> None:
        request = self.create(task_ref="board/task-control-restart")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "hold", "restart proof")
        claimed = self.center.task_decisions.claim_control(request_id, now_epoch=100.0, lease_seconds=10)
        self.assertEqual("sending", claimed["task_decision"]["control_state"])
        control_id = claimed["task_decision"]["control_id"]
        self.center._connection.close()

        context = multiprocessing.get_context("fork")
        output = context.Queue()
        worker = context.Process(target=_reconcile_control_after_restart, args=(str(self.path), request_id, output))
        worker.start()
        worker.join(5)
        self.assertEqual(0, worker.exitcode)
        self.center = NotificationCenter(
            self.path, {"token": {"project": "airlock", "max_severity": "critical"}},
            task_decision_verifier=_TrustedTestAuthority(),
        )
        self.assertEqual((0, control_id, control_id, "acknowledged", "reconciled-revoke"), output.get(timeout=1))

    def test_uncertain_revoke_is_queried_and_never_blindly_repeated(self) -> None:
        request = self.create(task_ref="board/task-control-unknown")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "hold", "ambiguous revoke")

        class AmbiguousActionOwner:
            owner_id = "userio"

            def __init__(self) -> None:
                self.revoke_calls = 0
                self.query_calls = 0

            def revoke(self, _command: dict[str, object], _key: str) -> dict[str, object]:
                self.revoke_calls += 1
                raise TimeoutError("owner reply lost")

            def query_control(self, _control_id: str) -> dict[str, object]:
                self.query_calls += 1
                return {"status": "unknown"}

        adapter = AmbiguousActionOwner()
        worker = DeliveryWorker(self.center, object(), task_action_owner=adapter)
        worker.run_once()
        state = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("unknown", state["control_state"])
        with mock.patch("notification_center.task_decisions.time.time", return_value=float(state["control_lease_until"]) + 1):
            worker.run_once()
        self.assertEqual(1, adapter.revoke_calls)
        self.assertEqual(1, adapter.query_calls)
        self.assertEqual("unknown", self.center.get_human_request("token", request_id)["task_decision"]["control_state"])

    def test_control_command_and_ack_bind_exact_execution_operation_and_turn(self) -> None:
        request = self.create(task_ref="board/task-execution-bound")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "hold", "bind native turn")
        command = self.center.task_decisions.control_command(request_id)
        expected_execution = {
            "task_ref": "board/task-execution-bound", "operation_id": "grant:task-17:v3",
            "board_version": 23, "started_by": "nikita", "host_id": "roomhacker-server-100",
            "harness": "codex", "session_id": "session-123", "turn_id": "turn-456",
        }
        self.assertEqual(expected_execution, command["execution"])

        class WrongExecutionOwner:
            owner_id = "userio"

            def revoke(self, received: dict[str, object], _idempotency_key: str) -> dict[str, object]:
                wrong_execution = dict(received["execution"])
                wrong_execution["turn_id"] = "different-turn"
                return {"status": "acknowledged", "receipt_ref": "wrong-turn-ack",
                        "effective_epoch": received["epoch"], "control_id": received["control_id"],
                        "execution": wrong_execution}

        DeliveryWorker(self.center, object(), task_action_owner=WrongExecutionOwner()).run_once()
        current = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("unknown", current["control_state"])
        self.assertFalse(self.center.task_decisions.continuation_allowed(request_id))

    def test_control_command_fails_closed_without_verified_execution_binding(self) -> None:
        request = self.create(
            task_ref="board/task-unbound-control",
            continuation={"kind": "unbound-test-fixture"},
        )
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "unbound fixture")
        with self.assertRaisesRegex(ValidationError, "complete verified execution binding"):
            self.center.task_decisions.control_command(request_id)

    def test_acknowledged_hold_requires_explicit_resume_before_a_new_answer(self) -> None:
        request = self.create(task_ref="board/task-21")
        self.sent(request, at=100.0)
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "hold", "wait for source update")

        class ActionOwner:
            owner_id = "userio"

            def revoke(self, command: dict[str, object], _idempotency_key: str) -> dict[str, object]:
                return {"status": "acknowledged", "receipt_ref": "userio-revoke-21",
                        "effective_epoch": command["epoch"], "control_id": command["control_id"],
                        "execution": command["execution"]}

        with mock.patch("notification_center.task_decisions.time.time", return_value=101.0):
            DeliveryWorker(self.center, object(), task_action_owner=ActionOwner()).run_once()
        self.center.task_decisions.resume_after_hold(request_id, "telegram:42", "new-directive:resume-21")
        answered = self.center.resolve_human_request_for_surface(request_id, "telegram:42", "continue")
        self.assertTrue(answered["answer_accepted"], answered)
        self.assertEqual("human", answered["task_decision"]["source"])


if __name__ == "__main__":
    unittest.main()
