from __future__ import annotations

import hashlib
import json
import os
import unittest
from unittest.mock import patch

from notification_center.core import AuthorizationError
from notification_center.task_decision_service import BoardTaskDecisionVerifier


OPERATION_ID = "a" * 64


class _Response:
    def __init__(self, value):
        self._raw = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self._raw[:limit]


class _Board:
    def __init__(self, card):
        self.card = card
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return _Response({"card": self.card})


def _card(**receipt_changes):
    receipt = {
        "operationId": OPERATION_ID,
        "lastBoardVersion": 23,
        "messageInputId": "input-message-18",
        "phase": "running",
        "startedBy": "nikita",
        "hostId": "roomhacker-server-100",
        "harness": "codex",
        "nativeSessionId": "session-123",
        "nativeTurnId": "turn-456",
    }
    receipt.update(receipt_changes)
    return {
        "id": "task-123",
        "version": 23,
        "project": "integrator-dashboard",
        "owner": "nikita",
        "assignees": [],
        "column": "in-progress",
        "needsReview": False,
        "taskExecutionHistory": [receipt],
    }


def _mandate(**changes):
    value = {
        "task_ref": "task-123",
        "purpose": "execution-choice",
        "version": "23",
        "mandate_ref": OPERATION_ID,
        "project": "integrator-dashboard",
        "recipient": "configured-owner",
        "message": "Choose whether to continue the task.",
        "allowed_actors": ["telegram:spoofed"],
        "choices": [
            {"value": "continue", "label": "Continue"},
            {"value": "stop", "label": "Stop"},
        ],
        "timeout_value": "continue",
        "continuation": {"session_id": "caller-selected"},
    }
    value.update(changes)
    return value


def _executor_question():
    question = {
        "humanRequestId": "d920a01e-a5f3-4dce-a640-b65b8e312433",
        "sourceMessageId": "assistant-message-20",
        "sourceMessageHash": "b" * 64,
        "sourceInputMessageId": "input-message-18",
        "sourceJobId": "dispatch-job-19",
        "text": "Which output format should I use?",
        "choices": [
            {"value": "csv", "label": "CSV"},
            {"value": "markdown", "label": "Markdown"},
        ],
        "timeoutValue": "csv",
        "contextRef": "exmanager-task:task-123:" + OPERATION_ID,
        "registrationMessageId": "registration-message-21",
        "registrationCallId": "human-request-call-22",
        "registrationTarget": {
            "harness": "opencode",
            "sessionId": "opencode-session-17",
            "cwd": "/srv/qa/project",
        },
    }
    hashed = {key: question[key] for key in (
        "humanRequestId", "sourceMessageId", "sourceMessageHash", "sourceInputMessageId",
        "sourceJobId", "text", "choices", "timeoutValue",
    )}
    question["questionHash"] = hashlib.sha256(
        json.dumps(hashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return question


def _open_code_card_with_question(question, direct_controls=None):
    card = _card(
        phase="waiting", hostId="qa-board", harness="opencode",
        nativeSessionId="opencode-session-17", nativeTurnId=None,
        nativeInputMessageId="input-message-18", nativeDispatchJobId="dispatch-job-19",
        startedBy="testlogin",
        projectBinding={
            "contract": "integrator-host-readiness-v1", "projectId": "qa-project-1",
            "cwd": "/srv/qa/project", "hostId": "qa-board", "harness": "opencode",
        },
    )
    card["owner"] = "testlogin"
    receipt = card["taskExecutionHistory"][-1]
    receipt["executorQuestion"] = question
    if direct_controls is not None:
        receipt["sessionControls"] = {"direct": direct_controls}
    return card


def _rehash_executor_question(question):
    hashed = {key: question[key] for key in (
        "humanRequestId", "sourceMessageId", "sourceMessageHash", "sourceInputMessageId",
        "sourceJobId", "text", "choices", "timeoutValue",
    )}
    question["questionHash"] = hashlib.sha256(
        json.dumps(hashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return question


class TaskDecisionServiceTests(unittest.TestCase):
    def verifier(self, board, actors=None):
        return BoardTaskDecisionVerifier(
            "https://todo.example.test", "protected-manager-token",
            actors or ["telegram:42"], operator_recipient="configured-owner", opener=board.open,
        )

    def test_verifies_current_task_and_derives_execution_and_actor_authority(self):
        board = _Board(_card())
        verifier = self.verifier(board)

        verified = verifier.verify(_mandate())

        self.assertEqual("integrator-dashboard", verified["project"])
        self.assertEqual("configured-owner", verified["recipient"])
        self.assertEqual(["telegram:42"], verified["allowed_actors"])
        self.assertEqual("session-123", verified["continuation"]["session_id"])
        self.assertEqual("turn-456", verified["continuation"]["turn_id"])
        self.assertEqual(23, verified["continuation"]["board_version"])
        request, timeout = board.requests[0]
        self.assertEqual("GET", request.get_method())
        self.assertTrue(request.full_url.endswith("/api/kanban/cards/task-123"))
        self.assertEqual("protected-manager-token", request.get_header("X-kanban-manager-token"))
        self.assertLessEqual(timeout, 10.0)

    def test_verifies_open_code_input_job_project_binding_without_fabricated_turn(self):
        card = _card(
            phase="waiting",
            hostId="qa-board",
            harness="opencode",
            nativeSessionId="opencode-session-17",
            nativeTurnId=None,
            nativeInputMessageId="input-message-18",
            nativeDispatchJobId="dispatch-job-19",
            startedBy="testlogin",
            projectBinding={
                "contract": "integrator-host-readiness-v1",
                "projectId": "qa-project-1",
                "cwd": "/srv/qa/project",
                "hostId": "qa-board",
                "harness": "opencode",
            },
        )
        card["owner"] = "testlogin"
        card["taskExecutionHistory"][-1]["executorQuestion"] = _executor_question()
        board = _Board(card)
        mandate = _mandate(
            project="integrator-dashboard",
            timeout_value="csv",
            continuation={"caller_selected": True},
        )

        verified = self.verifier(board).verify(mandate)

        self.assertEqual({
            "task_ref": "task-123",
            "operation_id": OPERATION_ID,
            "board_version": 23,
            "started_by": "testlogin",
            "host_id": "qa-board",
            "harness": "opencode",
            "session_id": "opencode-session-17",
            "project_id": "qa-project-1",
            "cwd": "/srv/qa/project",
            "input_id": "input-message-18",
            "job_id": "dispatch-job-19",
            "human_request_id": "d920a01e-a5f3-4dce-a640-b65b8e312433",
            "executor_question": {
                "source_message_id": "assistant-message-20",
                "source_message_hash": "b" * 64,
                "source_input_id": "input-message-18",
                "source_job_id": "dispatch-job-19",
                "human_request_id": "d920a01e-a5f3-4dce-a640-b65b8e312433",
                "question_hash": _executor_question()["questionHash"],
                "context_ref": "exmanager-task:task-123:" + OPERATION_ID,
                "registration_message_id": "registration-message-21",
                "registration_call_id": "human-request-call-22",
            },
            "question_text": "Which output format should I use?",
            "question_choices": [
                {"value": "csv", "label": "CSV"},
                {"value": "markdown", "label": "Markdown"},
            ],
            "question_timeout_value": "csv",
        }, verified["continuation"])
        self.assertNotIn("turn_id", verified["continuation"])

    def test_open_code_question_requires_and_uses_the_current_executor_question_proof(self):
        card = _card(
            phase="waiting",
            hostId="qa-board",
            harness="opencode",
            nativeSessionId="opencode-session-17",
            nativeTurnId=None,
            nativeInputMessageId="input-message-18",
            nativeDispatchJobId="dispatch-job-19",
            startedBy="testlogin",
            projectBinding={
                "contract": "integrator-host-readiness-v1",
                "projectId": "qa-project-1",
                "cwd": "/srv/qa/project",
                "hostId": "qa-board",
                "harness": "opencode",
            },
        )
        card["owner"] = "testlogin"
        board = _Board(card)
        verifier = self.verifier(board)
        mandate = _mandate(project="integrator-dashboard")

        with self.assertRaises(AuthorizationError):
            verifier.verify(mandate)

        card["taskExecutionHistory"][-1]["executorQuestion"] = _executor_question()
        mandate["timeout_value"] = "csv"
        mandate["message"] = "operator supplied unrelated question"
        mandate["choices"] = [{"value": "continue", "label": "Continue"}, {"value": "stop", "label": "Stop"}]

        verified = verifier.verify(mandate)

        self.assertEqual("Which output format should I use?", verified["message"])
        self.assertEqual(["csv", "markdown"], [choice["value"] for choice in verified["choices"]])
        self.assertEqual("assistant-message-20", verified["continuation"]["executor_question"]["source_message_id"])

    def test_open_code_question_accepts_real_registry_proof_and_persists_it_without_fake_ids(self):
        question = _executor_question()
        question.pop("registrationMessageId")
        question.pop("registrationCallId")
        question["registrationRef"] = {
            "kind": "herder-registry",
            "requestId": question["humanRequestId"],
            "contextRef": question["contextRef"],
        }
        card = _card(
            phase="waiting", hostId="qa-board", harness="opencode",
            nativeSessionId="opencode-session-17", nativeTurnId=None,
            nativeInputMessageId="input-message-18", nativeDispatchJobId="dispatch-job-19",
            startedBy="testlogin",
            projectBinding={
                "contract": "integrator-host-readiness-v1", "projectId": "qa-project-1",
                "cwd": "/srv/qa/project", "hostId": "qa-board", "harness": "opencode",
            },
        )
        card["owner"] = "testlogin"
        card["taskExecutionHistory"][-1]["executorQuestion"] = question

        board = _Board(card)
        verifier = self.verifier(board)
        verified = verifier.verify(_mandate(project="integrator-dashboard", timeout_value="csv"))
        executor_question = verified["continuation"]["executor_question"]

        self.assertEqual({
            "kind": "herder-registry",
            "requestId": "d920a01e-a5f3-4dce-a640-b65b8e312433",
            "contextRef": "exmanager-task:task-123:" + OPERATION_ID,
        }, executor_question["registration_ref"])
        self.assertNotIn("registration_message_id", executor_question)
        self.assertNotIn("registration_call_id", executor_question)
        context = {
            "task_ref": verified["task_ref"],
            "mandate_ref": verified["mandate_ref"],
            "continuation": verified["continuation"],
        }
        self.assertTrue(verifier.is_actor_allowed(context, "telegram:42"))

    def test_open_code_registry_proof_rejects_mismatched_identity_context_and_target(self):
        base = _executor_question()
        base.pop("registrationMessageId")
        base.pop("registrationCallId")
        base["registrationRef"] = {
            "kind": "herder-registry",
            "requestId": base["humanRequestId"],
            "contextRef": base["contextRef"],
        }
        invalid = []
        changed = json.loads(json.dumps(base))
        changed["registrationRef"]["requestId"] = "11111111-1111-4111-8111-111111111111"
        invalid.append(changed)
        changed = json.loads(json.dumps(base))
        changed["registrationRef"]["contextRef"] = "exmanager-task:other-card:" + OPERATION_ID
        invalid.append(changed)
        changed = json.loads(json.dumps(base))
        changed["registrationTarget"]["sessionId"] = "another-session"
        invalid.append(changed)
        changed = json.loads(json.dumps(base))
        changed["registrationMessageId"] = "legacy-message"
        changed["registrationCallId"] = "legacy-call"
        invalid.append(changed)

        for question in invalid:
            with self.subTest(question=question):
                card = _card(
                    phase="waiting", hostId="qa-board", harness="opencode",
                    nativeSessionId="opencode-session-17", nativeTurnId=None,
                    nativeInputMessageId="input-message-18", nativeDispatchJobId="dispatch-job-19",
                    startedBy="testlogin",
                    projectBinding={
                        "contract": "integrator-host-readiness-v1", "projectId": "qa-project-1",
                        "cwd": "/srv/qa/project", "hostId": "qa-board", "harness": "opencode",
                    },
                )
                card["owner"] = "testlogin"
                card["taskExecutionHistory"][-1]["executorQuestion"] = question
                with self.assertRaises(AuthorizationError):
                    self.verifier(_Board(card)).verify(_mandate(project="integrator-dashboard", timeout_value="csv"))

    def test_latest_accepted_direct_input_is_the_current_question_source(self):
        question = _executor_question()
        question.pop("registrationMessageId")
        question.pop("registrationCallId")
        question["registrationRef"] = {
            "kind": "herder-registry",
            "requestId": question["humanRequestId"],
            "contextRef": question["contextRef"],
        }
        direct = {
            "phase": "accepted",
            "requestHash": "d" * 64,
            "nativeInputMessageId": "msg_direct_input_20",
            "nativeDispatchJobId": "job_direct_dispatch_20",
        }
        question["sourceInputMessageId"] = direct["nativeInputMessageId"]
        question["sourceJobId"] = direct["nativeDispatchJobId"]
        _rehash_executor_question(question)
        board = _Board(_open_code_card_with_question(question, [direct]))
        verifier = self.verifier(board)

        verified = verifier.verify(_mandate(project="integrator-dashboard", timeout_value="csv"))

        self.assertEqual("msg_direct_input_20", verified["continuation"]["input_id"])
        self.assertEqual("job_direct_dispatch_20", verified["continuation"]["job_id"])
        self.assertEqual("d" * 64, verified["continuation"]["direct_request_hash"])
        context = {
            "task_ref": verified["task_ref"],
            "mandate_ref": verified["mandate_ref"],
            "continuation": verified["continuation"],
        }
        self.assertTrue(verifier.is_actor_allowed(context, "telegram:42"))

    def test_old_pending_unknown_and_invalid_direct_inputs_are_not_current(self):
        accepted = {
            "phase": "accepted", "requestHash": "d" * 64,
            "nativeInputMessageId": "msg_direct_input_20", "nativeDispatchJobId": "job_direct_dispatch_20",
        }
        old = {
            "phase": "accepted", "requestHash": "c" * 64,
            "nativeInputMessageId": "msg_old_input_19", "nativeDispatchJobId": "job_old_dispatch_19",
        }
        cases = [
            ([old, accepted], "msg_old_input_19", "job_old_dispatch_19"),
            ([{**accepted, "phase": "pending"}], "msg_direct_input_20", "job_direct_dispatch_20"),
            ([old, {**accepted, "phase": "pending"}], "msg_old_input_19", "job_old_dispatch_19"),
            ([{**accepted, "phase": "unknown"}], "msg_direct_input_20", "job_direct_dispatch_20"),
            ([old, {**accepted, "phase": "unknown"}], "msg_old_input_19", "job_old_dispatch_19"),
            ([{**accepted, "requestHash": "invalid"}], "msg_direct_input_20", "job_direct_dispatch_20"),
            ([old, {**accepted, "requestHash": "invalid"}], "msg_old_input_19", "job_old_dispatch_19"),
        ]
        for controls, source_input, source_job in cases:
            with self.subTest(controls=controls):
                question = _executor_question()
                question.pop("registrationMessageId")
                question.pop("registrationCallId")
                question["registrationRef"] = {
                    "kind": "herder-registry",
                    "requestId": question["humanRequestId"],
                    "contextRef": question["contextRef"],
                }
                question["sourceInputMessageId"] = source_input
                question["sourceJobId"] = source_job
                _rehash_executor_question(question)
                verifier = self.verifier(_Board(_open_code_card_with_question(question, controls)))
                with self.assertRaises(AuthorizationError):
                    verifier.verify(_mandate(project="integrator-dashboard", timeout_value="csv"))

    def test_board_answer_proposal_is_rechecked_from_current_card_cas_and_stop_epoch(self):
        card = _card(
            phase="waiting",
            hostId="qa-board",
            harness="opencode",
            nativeSessionId="opencode-session-17",
            nativeTurnId=None,
            nativeInputMessageId="input-message-18",
            nativeDispatchJobId="dispatch-job-19",
            startedBy="testlogin",
            projectBinding={
                "contract": "integrator-host-readiness-v1",
                "projectId": "qa-project-1",
                "cwd": "/srv/qa/project",
                "hostId": "qa-board",
                "harness": "opencode",
            },
        )
        card["owner"] = "testlogin"
        question = _executor_question()
        question["sourceInputMessageId"] = "msg_direct_input_20"
        question["sourceJobId"] = "job_direct_dispatch_20"
        _rehash_executor_question(question)
        receipt = card["taskExecutionHistory"][-1]
        receipt["executorQuestion"] = question
        direct_request_hash = "d" * 64
        receipt["sessionControls"] = {
            "direct": [{
                "phase": "accepted", "requestHash": direct_request_hash,
                "nativeInputMessageId": "msg_direct_input_20",
                "nativeDispatchJobId": "job_direct_dispatch_20",
            }],
            "stops": [{"phase": "accepted", "requestHash": "historic-stop-request"}],
        }
        reference = "td-" + hashlib.sha256(
            json.dumps(["task-123", "execution-choice", "23"], separators=(",", ":")).encode()
        ).hexdigest()[:40]
        request_hash = "a" * 64
        card["taskExecutionHistory"][-1]["taskDecisionRequest"] = {
            "requestId": reference,
            "requestHash": request_hash,
            "purpose": "execution-choice",
            "version": 23,
        }
        card["taskExecutionHistory"][-1]["taskDecisionProposal"] = {
            "proposalRef": "proposal-1",
            "requestId": reference,
            "requestHash": request_hash,
            "purpose": "execution-choice",
            "version": 23,
            "value": "csv",
            "authenticatedActor": "testlogin",
            "cardVersion": 23,
            "stopEpoch": direct_request_hash,
            "sourceMessageId": "assistant-message-20",
            "sourceJobId": "job_direct_dispatch_20",
            "humanRequestId": "d920a01e-a5f3-4dce-a640-b65b8e312433",
        }
        board = _Board(card)
        verifier = self.verifier(board)
        continuation = verifier.verify(_mandate(project="integrator-dashboard", timeout_value="csv"))["continuation"]
        context = {
            "task_ref": "task-123",
            "purpose": "execution-choice",
            "version": "23",
            "mandate_ref": OPERATION_ID,
            "request_hash": request_hash,
            "task_decision_ref": reference,
            "continuation": continuation,
        }

        answer = verifier.verify_board_answer(context, reference, "proposal-1")

        self.assertEqual({
            "request_id": reference,
            "proposal_ref": "proposal-1",
            "actor": "testlogin",
            "value": "csv",
            "card_version": 23,
            "stop_epoch": direct_request_hash,
            "source_message_id": "assistant-message-20",
            "source_job_id": "job_direct_dispatch_20",
            "human_request_id": "d920a01e-a5f3-4dce-a640-b65b8e312433",
            "question_hash": question["questionHash"],
        }, answer)

        # Saving the answer proposal is its own current Board CAS. It advances
        # the card version while keeping the original task decision version and
        # immutable native target bound to the same execution.
        card["version"] = 24
        card["taskExecutionHistory"][-1]["lastBoardVersion"] = 24
        card["taskExecutionHistory"][-1]["taskDecisionProposal"]["cardVersion"] = 24
        after_proposal_cas = verifier.verify_board_answer(context, reference, "proposal-1")
        self.assertEqual(24, after_proposal_cas["card_version"])
        self.assertEqual("23", context["version"])

        direct = card["taskExecutionHistory"][-1]["sessionControls"]["direct"][-1]
        direct["requestHash"] = "invalid"
        card["taskExecutionHistory"][-1]["taskDecisionProposal"]["stopEpoch"] = "invalid"
        self.assertIsNone(verifier.verify_board_answer(context, reference, "proposal-1"))
        direct.update(phase="unknown", requestHash=direct_request_hash)
        card["taskExecutionHistory"][-1]["taskDecisionProposal"]["stopEpoch"] = direct_request_hash
        self.assertIsNone(verifier.verify_board_answer(context, reference, "proposal-1"))
        direct["phase"] = "accepted"
        card["taskExecutionHistory"][-1]["sessionControls"]["stops"].append({"phase": "unknown"})
        self.assertIsNone(verifier.verify_board_answer(context, reference, "proposal-1"))
        card["taskExecutionHistory"][-1]["sessionControls"]["stops"][-1]["phase"] = "accepted"
        self.assertEqual(24, verifier.verify_board_answer(context, reference, "proposal-1")["card_version"])

        for change in (
            {"authenticatedActor": "attacker"},
            {"cardVersion": 22},
            {"stopEpoch": "stale-input"},
            {"sourceJobId": "another-job"},
        ):
            with self.subTest(change=change):
                card["taskExecutionHistory"][-1]["taskDecisionProposal"].update(change)
                self.assertIsNone(verifier.verify_board_answer(context, reference, "proposal-1"))
                card["taskExecutionHistory"][-1]["taskDecisionProposal"].update({
                    "authenticatedActor": "testlogin", "cardVersion": 23,
                    "stopEpoch": direct_request_hash, "sourceJobId": "job_direct_dispatch_20",
                })

    def test_actor_and_timeout_checks_reverify_the_live_execution_receipt(self):
        board = _Board(_card())
        verifier = self.verifier(board)
        verified = verifier.verify(_mandate())
        context = {
            "task_ref": verified["task_ref"],
            "purpose": verified["purpose"],
            "version": verified["version"],
            "mandate_ref": verified["mandate_ref"],
            "allowed_actors": verified["allowed_actors"],
            "continuation": verified["continuation"],
        }

        self.assertTrue(verifier.is_actor_allowed(context, "telegram:42"))
        self.assertFalse(verifier.is_actor_allowed(context, "telegram:spoofed"))
        self.assertTrue(verifier.timeout_allowed_now(context))

        board.card = _card(phase="result")
        self.assertFalse(verifier.is_actor_allowed(context, "telegram:42"))
        self.assertFalse(verifier.timeout_allowed_now(context))

    def test_denies_stale_versions_receipts_projects_and_reassigned_owners(self):
        cases = (
            (_card(lastBoardVersion=22), _mandate()),
            (_card(phase="result"), _mandate()),
            (_card(startedBy="other-user"), _mandate()),
            (_card(), _mandate(version="22")),
            (_card(), _mandate(mandate_ref="b" * 64)),
            (_card(), _mandate(project="another-project")),
        )
        for card, mandate in cases:
            with self.subTest(card=card["taskExecutionHistory"][-1], mandate=mandate):
                with self.assertRaises(AuthorizationError):
                    self.verifier(_Board(card)).verify(mandate)

    def test_missing_protected_runtime_references_keep_verifier_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(BoardTaskDecisionVerifier.from_environment())

    def test_environment_factory_uses_existing_protected_reference_names(self):
        configured = {
            "EXMANAGER_KANBAN_URL": "https://todo.example.test",
            "EXMANAGER_KANBAN_TOKEN": "not-output",
            "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "42,73",
            "NOTIFY_CENTER_RECIPIENT": "configured-owner",
        }
        with patch.dict(os.environ, configured, clear=True):
            verifier = BoardTaskDecisionVerifier.from_environment()
        self.assertIsInstance(verifier, BoardTaskDecisionVerifier)
        self.assertEqual(["telegram:42", "telegram:73"], verifier._allowed_actors)
        self.assertEqual("configured-owner", verifier._recipient)

    def test_rejects_caller_selected_recipient_and_canonicalizes_configured_value(self):
        board = _Board(_card())
        verifier = self.verifier(board)
        with self.assertRaises(AuthorizationError):
            verifier.verify(_mandate(recipient="somebody-else"))

        verified = verifier.verify(_mandate(recipient=" configured-owner "))
        self.assertEqual("configured-owner", verified["recipient"])

    def test_environment_actor_policy_uses_explicit_human_request_allowlist_only(self):
        base = {
            "EXMANAGER_KANBAN_URL": "https://todo.example.test",
            "EXMANAGER_KANBAN_TOKEN": "not-output",
            "TELEGRAM_CHAT_ID": "999",
            "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "42",
            "NOTIFY_CENTER_RECIPIENT": "configured-owner",
        }
        with patch.dict(os.environ, {**base, "ASK_HUMAN_ALLOWED_ACTORS": "telegram:73"}, clear=True):
            verifier = BoardTaskDecisionVerifier.from_environment()
        self.assertEqual(["telegram:73"], verifier._allowed_actors)
        with patch.dict(os.environ, {**base, "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "*"}, clear=True):
            self.assertIsNone(BoardTaskDecisionVerifier.from_environment())


if __name__ == "__main__":
    unittest.main()
