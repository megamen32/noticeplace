from __future__ import annotations

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
