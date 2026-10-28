from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.core import AuthorizationError, NotificationCenter
from notification_center.task_decision_view import TaskDecisionView


class _Authority:
    def __call__(self, mandate):
        return mandate

    def is_actor_allowed(self, _context, actor):
        return actor == "telegram:42"

    def timeout_allowed_now(self, _context):
        return True


def _mandate():
    return {
        "task_ref": "board/task-17", "purpose": "choose-next-step", "version": "v3",
        "mandate_ref": "grant:task-17:v3", "project": "airlock", "recipient": "operator",
        "message": "Choose the permitted next step.", "allowed_actors": ["telegram:42"],
        "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
        "timeout_value": "continue", "continuation": {
            "task_ref": "board/task-17", "operation_id": "grant:task-17:v3", "board_version": 23,
            "started_by": "test-owner", "host_id": "qa-board", "harness": "codex",
            "session_id": "codex-session-17", "turn_id": "codex-turn-18",
        },
    }


class TaskDecisionViewTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "notice.sqlite3"
        self.center = NotificationCenter(
            self.path, {"token": {"project": "airlock"}}, task_decision_verifier=_Authority()
        )
        self.request = self.center.create_task_decision_request("token", _mandate())
        self.request_id = str(self.request["request_id"])
        self.calls = []
        self.view = TaskDecisionView(self.center, lambda principal, request_id: self.calls.append(
            (principal, request_id)
        ) or principal == "current-admin")

    def tearDown(self):
        self.center._connection.close()
        self.tempdir.cleanup()

    def test_denied_read_is_rejected_before_source_query(self):
        statements = []
        self.center._connection.set_trace_callback(statements.append)
        with self.assertRaises(AuthorizationError):
            self.view.read("stale-admin", self.request_id)
        self.assertEqual([("stale-admin", self.request_id)], self.calls)
        self.assertFalse(any("FROM human_requests" in sql for sql in statements))

    def test_unknown_notification_has_no_receipt_or_countdown_after_restart(self):
        self.center.task_decisions.reserve_notification(self.request_id)
        self.center.task_decisions.mark_notification_unknown(self.request_id)
        first = self.view.read("current-admin", self.request_id)
        self.assertEqual("unknown", first["notification"]["attempt_state"])
        self.assertIsNone(first["notification"]["receipt"])
        self.assertIsNone(first["notification"]["deadline_at"])

        self.center._connection.close()
        reopened = NotificationCenter(self.path, {"token": {"project": "airlock"}})
        try:
            view = TaskDecisionView(reopened, lambda _principal, _request_id: True)
            again = view.read("current-admin", self.request_id)
            self.assertEqual(first["notification"], again["notification"])
            self.assertIsNone(again["winner"])
        finally:
            reopened._connection.close()

    def test_exact_first_success_deadline_survives_reopen(self):
        self.center.task_decisions.reserve_notification(self.request_id)
        self.center.bind_human_request_message("token", self.request_id, "42", 91)
        before = self.view.read("current-admin", self.request_id)["notification"]
        self.center._connection.close()
        reopened = NotificationCenter(self.path, {"token": {"project": "airlock"}})
        try:
            view = TaskDecisionView(reopened, lambda _principal, _request_id: True)
            after = view.read("current-admin", self.request_id)["notification"]
            self.assertEqual(before, after)
            self.assertEqual(1800.0, after["deadline_at"] - after["receipt"]["confirmed_at"])
        finally:
            reopened._connection.close()

    def test_task_continuation_separates_answer_reference_from_herder_transport_receipt(self):
        request_id = str(self.request["canonical_request_id"])
        result_ref = "6944969d-ac29-5003-fba0-a545f0c2891c"
        registry_request_id = "d920a01e-a5f3-4dce-a640-b65b8e312433"
        idem = "human-request:d920a01e-a5f3-4dce-a640-b65b8e312433:attempt-1"
        receipt = f"agent-herder://opencode/opencode-session-17/{idem}"
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "INSERT INTO human_request_completions(request_id,result_ref,harness,session_id,cwd,actor,state,"
                "attempts,reason,next_attempt_at,card_next_attempt_at,created_at,updated_at,registry_request_id,"
                "herder_idempotency_key,herder_receipt,herder_input_id) "
                "VALUES (?,?,?,?,?,?,'sent',1,'',0,0,0,0,?,?,?,?)",
                (request_id, result_ref, "opencode", "opencode-session-17", "/srv/qa/project", "testlogin",
                 registry_request_id, idem, receipt, "herder-resume-" + "a" * 32),
            )

        continuation = self.view.read("current-admin", request_id)["continuation"]
        self.assertEqual(
            {"state": "accepted", "status": "accepted", "request_id": registry_request_id, "result_ref": result_ref,
             "idempotency_key": idem, "receipt": receipt, "input_id": "herder-resume-" + "a" * 32},
            {key: continuation[key] for key in
             ("state", "status", "request_id", "result_ref", "idempotency_key", "receipt", "input_id")},
        )

    def test_projection_uses_persisted_first_receipt_winner_and_matching_control_ack(self):
        self.center.task_decisions.reserve_notification(self.request_id)
        self.center.bind_human_request_message("token", self.request_id, "42", 91)
        self.center.resolve_human_request("token", self.request_id, "telegram:42", "continue")
        self.center.task_decisions.request_control(self.request_id, "telegram:42", "stop", "cancel task")
        pending = self.view.read("current-admin", self.request_id)["control"]
        self.assertTrue(pending["pending"])
        self.assertFalse(pending["acknowledgement_matches_current_epoch"])
        control = self.center.task_decisions.control_command(self.request_id)
        self.center.task_decisions.acknowledge_control(
            self.request_id, control_id=control["control_id"], action_owner="userio",
            receipt_ref="revoke-receipt:17", effective_epoch=control["epoch"],
            accepted_effects=["cancelled:task-17"],
        )
        self.center.task_decisions.record_late_instruction(self.request_id, "telegram:42", "stop")

        projection = self.view.read("current-admin", self.request_id)
        self.assertEqual("Choose the permitted next step.", projection["question"]["text"])
        self.assertEqual("board/task-17", projection["question"]["reference"]["task_ref"])
        self.assertEqual("sent", projection["notification"]["attempt_state"])
        self.assertEqual(1800.0, projection["notification"]["deadline_at"] - projection["notification"]["receipt"]["confirmed_at"])
        self.assertEqual(("42", 91), (projection["notification"]["receipt"]["chat_id"],
                                     projection["notification"]["receipt"]["message_id"]))
        self.assertEqual({"source": "human", "value": "continue", "actor": "telegram:42"},
                         {key: projection["winner"][key] for key in ("source", "value", "actor")})
        self.assertEqual("acknowledged", projection["control"]["state"])
        self.assertTrue(projection["control"]["acknowledgement_matches_current_epoch"])
        self.assertEqual("revoke-receipt:17", projection["control"]["ack_ref"])
        self.assertEqual("stop", projection["late_instructions"][0]["value"])


if __name__ == "__main__":
    unittest.main()
