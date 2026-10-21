"""HTTP and existing-worker join for task-scoped human questions."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, build_handler
from notification_center.exmanager_notice_transport import ExManagerNoticeTransportError
from notification_center.task_decision_service import BoardTaskDecisionVerifier


class _BoardResponse:
    def __init__(self, value):
        self.raw = json.dumps({"card": value}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.raw[:limit]


class _Authority:
    def __call__(self, mandate):
        return mandate

    def is_actor_allowed(self, _context, actor):
        return actor == "telegram:42"

    def timeout_allowed_now(self, _context):
        return True


class _FakeExManagerTransport:
    instances = []
    submit_results = []
    query_results = []

    def __init__(self, base_url, bearer):
        self.base_url = base_url
        self.bearer = bearer
        self.readiness_calls = 0
        self.submit_calls = []
        self.query_calls = []
        self.submit_result = type(self).submit_results.pop(0) if type(self).submit_results else {
            "outcome": "accepted", "sender_id": "8810909089",
            "chat_id": "-1004352022764", "message_id": 601, "accepted_at": 1000.0,
        }
        self.query_result = {"outcome": "not_found"}
        type(self).instances.append(self)

    def readiness(self):
        self.readiness_calls += 1
        return {"ok": True}

    def submit_question(self, request_id, operation_id, text):
        self.submit_calls.append((request_id, operation_id, text))
        if isinstance(self.submit_result, Exception):
            raise self.submit_result
        return self.submit_result

    def query_question(self, request_id, operation_id):
        self.query_calls.append((request_id, operation_id))
        if type(self).query_results:
            return type(self).query_results.pop(0)
        return self.query_result


def _mandate(version: str = "v3") -> dict[str, object]:
    return {
        "task_ref": "board/task-17", "purpose": "choose-next-step", "version": version,
        "mandate_ref": f"grant:task-17:{version}", "project": "airlock", "recipient": "operator",
        "message": "Choose the permitted next step.", "allowed_actors": ["telegram:42"],
        "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
        "timeout_value": "continue", "continuation": {
            "task_ref": "board/task-17", "operation_id": f"grant:task-17:{version}", "board_version": 23,
            "started_by": "nikita", "host_id": "roomhacker-server-100", "harness": "codex",
            "session_id": "session-123", "turn_id": "turn-456",
        },
    }


class TaskDecisionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeExManagerTransport.instances.clear()
        _FakeExManagerTransport.submit_results.clear()
        _FakeExManagerTransport.query_results.clear()
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notice.sqlite3",
            {"airlock-token": {"project": "airlock"}, "other-token": {"project": "other"}},
            task_decision_verifier=_Authority(),
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health", "mcp"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.center._connection.close()
        self.tempdir.cleanup()

    def request(self, method: str, path: str, token: str, body: dict[str, object] | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_create_read_answer_and_retry_reuse_one_receipt_started_deadline(self) -> None:
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", _FakeExManagerTransport), \
             mock.patch("notification_center.http_api.telegram_api") as send, \
             mock.patch("notification_center.task_decisions.time.time", return_value=1000.0):
            status, created = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            self.assertEqual(201, status)
            self.assertEqual("sent", created["delivery_status"])
            self.assertEqual("sent", created["notification"]["attempt_state"])
            self.assertEqual(2800.0, created["notification"]["deadline_at"])
            request_id = created["request_id"]

            read_status, read = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
            self.assertEqual(200, read_status)
            self.assertEqual("board/task-17", read["question"]["reference"]["task_ref"])
            self.assertEqual(("-1004352022764", 601), (read["notification"]["receipt"]["chat_id"],
                                                        read["notification"]["receipt"]["message_id"]))

            answer_status, answer = self.request(
                "POST", f"/v1/human-requests/{request_id}/resolve", "airlock-token",
                {"actor": "telegram:42", "value": "continue"},
            )
            self.assertEqual(200, answer_status)
            self.assertEqual("accepted", answer["answer_status"])
            after_status, after = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
            self.assertEqual(200, after_status)
            self.assertEqual({"source": "human", "value": "continue", "actor": "telegram:42"},
                             {key: after["winner"][key] for key in ("source", "value", "actor")})

            retry_status, retry = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            self.assertEqual(200, retry_status)
            self.assertTrue(retry["idempotent"])
            self.assertEqual("sent", retry["delivery_status"])
            send.assert_not_called()
            self.assertEqual(1, len(_FakeExManagerTransport.instances[0].submit_calls))

    def test_unknown_send_is_visible_and_idempotent_create_never_replays(self) -> None:
        _FakeExManagerTransport.submit_results[:] = [{"outcome": "unknown"}]
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", _FakeExManagerTransport), \
             mock.patch("notification_center.http_api.telegram_api") as send:
            first_status, first = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            self.assertEqual(201, first_status)
            self.assertEqual("unknown", first["delivery_status"])
            request_id = "td-" + hashlib.sha256(
                json.dumps(["board/task-17", "choose-next-step", "v3"], separators=(",", ":")).encode()
            ).hexdigest()[:40]

            read_status, read = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
            self.assertEqual(200, read_status)
            self.assertEqual("unknown", read["notification"]["attempt_state"])
            self.assertIsNone(read["notification"]["deadline_at"])

            retry_status, retry = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            self.assertEqual(200, retry_status)
            self.assertTrue(retry["idempotent"])
            self.assertEqual("unknown", retry["delivery_status"])
            self.assertEqual(1, len(_FakeExManagerTransport.instances[0].submit_calls))
            self.assertEqual(1, len(_FakeExManagerTransport.instances[1].query_calls))
            send.assert_not_called()

    def test_airlock_uses_fixed_exmanager_transport_and_binds_receipt(self) -> None:
        _FakeExManagerTransport.instances.clear()
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", _FakeExManagerTransport), \
             mock.patch("notification_center.http_api.telegram_api") as telegram_send, \
             mock.patch("notification_center.task_decisions.time.time", return_value=1000.0):
            status, created = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})

        self.assertEqual(201, status)
        self.assertEqual("sent", created["delivery_status"])
        self.assertEqual("sent", created["notification"]["attempt_state"])
        self.assertEqual({"kind": "successful_sender_response", "confirmed_at": 1000.0,
                          "chat_id": "-1004352022764", "message_id": 601},
                         created["notification"]["receipt"])
        self.assertEqual(2800.0, created["notification"]["deadline_at"])
        telegram_send.assert_not_called()
        transport = _FakeExManagerTransport.instances[-1]
        self.assertEqual("https://notify.bezrabotnyi.com/_exmanager", transport.base_url)
        self.assertEqual("airlock-token", transport.bearer)
        self.assertEqual(1, transport.readiness_calls)
        self.assertEqual(1, len(transport.submit_calls))
        request_id, operation_id, text = transport.submit_calls[0]
        self.assertEqual(created["request_id"], request_id)
        self.assertEqual("notice-question:" + request_id, operation_id)
        self.assertIn("continue — Continue", text)
        self.assertIn("stop — Stop", text)

    def test_airlock_unknown_reconciles_by_get_and_never_posts_again(self) -> None:
        _FakeExManagerTransport.instances.clear()
        _FakeExManagerTransport.submit_results[:] = [{"outcome": "unknown"}]
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", _FakeExManagerTransport):
            first_status, first = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            second_status, second = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
        self.assertEqual(201, first_status)
        self.assertEqual("unknown", first["delivery_status"])
        self.assertEqual(200, second_status)
        self.assertEqual("unknown", second["delivery_status"])
        uncertain_transport = _FakeExManagerTransport.instances[0]
        self.assertEqual(1, len(uncertain_transport.submit_calls))
        self.assertEqual([], uncertain_transport.query_calls)
        reconciliation = _FakeExManagerTransport.instances[1]
        self.assertEqual([], reconciliation.submit_calls)
        self.assertEqual([(first["request_id"], "notice-question:" + first["request_id"])],
                         reconciliation.query_calls)

    def test_airlock_query_recovers_accepted_receipt_after_unknown_without_resubmit(self) -> None:
        _FakeExManagerTransport.submit_results[:] = [ExManagerNoticeTransportError("response lost after accept")]
        _FakeExManagerTransport.query_results[:] = [{
            "outcome": "accepted", "sender_id": "8810909089",
            "chat_id": "-1004352022764", "message_id": 777, "accepted_at": 1000.0,
        }]
        with mock.patch.dict(os.environ, {"EXMANAGER_NOTICE_QUESTION_BASE_URL": "https://notify.bezrabotnyi.com/_exmanager"}), \
             mock.patch("notification_center.http_api.ExManagerNoticeTransport", _FakeExManagerTransport), \
             mock.patch("notification_center.task_decisions.time.time", return_value=1200.0):
            first_status, first = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            recovered_status, recovered = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})

        self.assertEqual(201, first_status)
        self.assertEqual("unknown", first["delivery_status"])
        self.assertEqual(200, recovered_status)
        self.assertEqual("sent", recovered["delivery_status"])
        self.assertEqual({"kind": "successful_sender_response", "confirmed_at": mock.ANY,
                          "chat_id": "-1004352022764", "message_id": 777},
                         recovered["notification"]["receipt"])
        self.assertEqual(2800.0, recovered["notification"]["deadline_at"])
        self.assertEqual(1, len(_FakeExManagerTransport.instances[0].submit_calls))
        self.assertEqual([], _FakeExManagerTransport.instances[1].submit_calls)
        self.assertEqual(1, len(_FakeExManagerTransport.instances[1].query_calls))

    def test_airlock_missing_transport_does_not_fall_back_or_reserve(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("notification_center.http_api.telegram_api") as telegram_send:
            status, result = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
        self.assertEqual(201, status)
        self.assertEqual("not_configured", result["delivery_status"])
        self.assertEqual("queued", result["notification"]["attempt_state"])
        self.assertIsNone(result["notification"]["receipt"])
        telegram_send.assert_not_called()

    def test_alternate_recipient_is_denied_before_request_row_or_send(self) -> None:
        operation_id = "a" * 64
        card = {
            "id": "board/task-17", "version": 23, "project": "airlock", "owner": "nikita",
            "assignees": [], "column": "in-progress", "needsReview": False,
            "taskExecutionHistory": [{
                "operationId": operation_id, "lastBoardVersion": 23, "phase": "running",
                "startedBy": "nikita", "hostId": "test-host", "harness": "codex",
                "nativeSessionId": "session-123", "nativeTurnId": "turn-456",
            }],
        }
        self.center._task_decision_verifier = BoardTaskDecisionVerifier(
            "https://todo.example.test", "protected-manager-token", ["telegram:42"],
            operator_recipient="operator", opener=lambda _request, timeout: _BoardResponse(card),
        )
        mandate = {
            "task_ref": "board/task-17", "purpose": "choose-next-step", "version": "23",
            "mandate_ref": operation_id, "project": "airlock", "recipient": "alternate-recipient",
            "message": "Choose the permitted next step.", "allowed_actors": ["telegram:spoofed"],
            "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
            "timeout_value": "continue", "continuation": {"session_id": "caller-selected"},
        }
        with mock.patch("notification_center.http_api.send_human_request_telegram") as send:
            status, failure = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": mandate})

        self.assertEqual(401, status)
        self.assertIn("recipient", failure["error"])
        self.assertEqual(0, self.center._connection.execute("SELECT COUNT(*) FROM human_requests").fetchone()[0])
        send.assert_not_called()

    def test_project_scope_and_default_denial_are_preserved(self) -> None:
        request = self.center.create_task_decision_request("airlock-token", _mandate())
        request_id = str(request["request_id"])
        status, _body = self.request("GET", f"/v1/task-decisions/{request_id}", "other-token")
        self.assertEqual(401, status)

        denied = NotificationCenter(Path(self.tempdir.name) / "denied.sqlite3", {"airlock-token": {"project": "airlock"}})
        handler = build_handler(denied, "health", "mcp")
        denied_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=denied_server.serve_forever, daemon=True)
        thread.start()
        base_url, self.base_url = self.base_url, f"http://127.0.0.1:{denied_server.server_port}"
        try:
            status, error = self.request("POST", "/v1/task-decisions", "airlock-token", {"mandate": _mandate()})
            self.assertEqual(401, status)
            self.assertIn("verified task mandate unavailable", error["error"])
        finally:
            self.base_url = base_url
            denied_server.shutdown()
            thread.join()
            denied_server.server_close()
            denied._connection.close()

    def test_control_request_flows_through_existing_worker_and_ack_is_readable(self) -> None:
        request = self.center.create_task_decision_request("airlock-token", _mandate())
        request_id = str(request["request_id"])
        status, pending = self.request(
            "POST", f"/v1/human-requests/{request_id}/resolve", "airlock-token",
            {"value": "stop", "actor": "telegram:42"},
        )
        self.assertEqual(200, status)
        self.assertEqual("no_accept", pending["answer_status"])
        _read_status, control_pending = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
        self.assertEqual("pending", control_pending["control"]["state"])

        DeliveryWorker(self.center, object()).run_once()
        _read_status, still_pending = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
        self.assertEqual("pending", still_pending["control"]["state"])

        class ActionOwner:
            owner_id = "test-only"

            def revoke(self, command, _idempotency_key):
                return {"status": "acknowledged", "receipt_ref": "test-revoke-receipt",
                        "effective_epoch": command["epoch"], "accepted_effects": ["send:accepted"],
                        "unknown_effects": ["send:unknown"], "control_id": command["control_id"],
                        "execution": command["execution"]}

        DeliveryWorker(self.center, object(), task_action_owner=ActionOwner()).run_once()
        final_status, final = self.request("GET", f"/v1/task-decisions/{request_id}", "airlock-token")
        self.assertEqual(200, final_status)
        self.assertEqual("acknowledged", final["control"]["state"])
        self.assertTrue(final["control"]["acknowledgement_matches_current_epoch"])
        self.assertEqual("test-revoke-receipt", final["control"]["ack_ref"])
        self.assertEqual({"accepted": ["send:accepted"], "unknown": ["send:unknown"]}, final["control"]["effects"])


if __name__ == "__main__":
    unittest.main()
