"""The human-answer callback must bypass only the owner-paused AI launch port."""

import os
import hashlib
import json
import unittest
import urllib.request
from unittest.mock import patch

from notification_center.agent_job_helper import read_herder_human_request
from notification_center.http_api import _human_completion_runner, deliver_human_completion


OLD = "http://127.0.0.1:18787/internal/human-requests/ask-user-completion"
NEW = "http://127.0.0.1:19187/internal/human-requests/ask-user-completion"


class CompletionRouteTests(unittest.TestCase):
    def test_only_exact_completion_post_uses_separate_loopback_route(self) -> None:
        calls = []

        def runner(request, timeout):
            calls.append((request.full_url, request.get_method(), request.data, timeout))
            return object()

        with patch.dict(os.environ, {"NOTIFY_HUMAN_COMPLETION_URL": NEW}):
            wrapped = _human_completion_runner(runner)
            body = b'{"request_id":"fixture"}'
            wrapped(urllib.request.Request(OLD, data=body, method="POST"), timeout=2)
            self.assertEqual([(NEW, "POST", body, 2)], calls)
            with self.assertRaisesRegex(ValueError, "another action"):
                wrapped(urllib.request.Request("http://127.0.0.1:18787/api/sessions/new-or-resume", data=body, method="POST"), timeout=2)
            with self.assertRaisesRegex(ValueError, "another action"):
                wrapped(urllib.request.Request(OLD, method="GET"), timeout=2)
            self.assertEqual(1, len(calls))

    def test_rejects_remote_or_broad_callback_configuration(self) -> None:
        for value in [
            "https://agent.bezrabotnyi.com/internal/human-requests/ask-user-completion",
            "http://127.0.0.1:19187/api/sessions/new-or-resume",
            NEW + "?unsafe=1",
        ]:
            with self.subTest(value=value), patch.dict(os.environ, {"NOTIFY_HUMAN_COMPLETION_URL": value}):
                with self.assertRaisesRegex(ValueError, "exact loopback"):
                    _human_completion_runner(lambda *_args, **_kwargs: None)

    def test_delivery_passes_only_the_callback_through_the_new_route(self) -> None:
        class Center:
            def __init__(self):
                self.row = {"state": "retryable", "result_ref": "opaque-ref", "harness": "opencode", "session_id": "native-session", "cwd": "/tmp"}

            def human_request_completion(self, _request_id):
                return self.row.copy()

            def claim_human_completion(self, _request_id):
                return True

            def mark_human_completion(self, _request_id, state, reason=""):
                self.row["state"] = state
                return self.row.copy()

        called = []

        def runner(request, timeout):
            called.append(request.full_url)
            return object()

        def native_resume(_rid, _harness, _sid, _cwd, _ref, routed_runner):
            routed_runner(urllib.request.Request(OLD, data=b"{}", method="POST"), timeout=1)
            return {"status": "resumed"}

        with patch.dict(os.environ, {"NOTIFY_HUMAN_COMPLETION_URL": NEW}), \
                patch("notification_center.agent_job_helper.resume_bound_native_session", side_effect=native_resume):
            result = deliver_human_completion(Center(), {"request_id": "fixture"}, "fixture", runner)
        self.assertEqual("resumed", result["status"])
        self.assertEqual([NEW], called)

    def test_registry_readback_uses_only_existing_readonly_owner_loopback_mcp(self) -> None:
        request_id = "1b45fd65-6013-4925-a781-afd5367d7b1f"
        record = {"requestId": request_id, "status": "resumed", "target": {"agent": "opencode"}}
        body = {"result": {"structuredContent": {"request": record}}, "jsonrpc": "2.0", "id": 1}
        calls = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return f"event: message\ndata: {json.dumps(body)}\n\n".encode()

        def runner(request, timeout):
            calls.append((request, timeout))
            return Response()

        with patch("notification_center.agent_job_helper._herder_loopback_profile", return_value={
            "url": "http://127.0.0.1:18787/api/sessions/new-or-resume",
        }):
            self.assertEqual(record, read_herder_human_request(request_id, runner))
        request, timeout = calls[0]
        self.assertEqual("http://127.0.0.1:18787/mcp", request.full_url)
        self.assertEqual("POST", request.get_method())
        self.assertFalse(request.has_header("Authorization"))
        self.assertEqual(5, timeout)
        payload = json.loads(request.data)
        self.assertEqual("human_request_get", payload["params"]["name"])
        self.assertEqual({"requestId": request_id}, payload["params"]["arguments"])

    def test_task_completion_is_sent_only_after_matching_herder_receipt_readback(self) -> None:
        request_id = "1b45fd65-6013-4925-a781-afd5367d7b1f"
        result_ref = "6944969d-ac29-5003-fba0-a545f0c2891c"
        session_id = "opencode-l06-session"
        key = f"human-request:{request_id}:attempt-1"
        transport_receipt = f"agent-herder://opencode/{session_id}/{key}"

        class Center:
            def __init__(self):
                self.row = {"state": "retryable", "result_ref": result_ref, "registry_request_id": request_id,
                            "harness": "opencode", "session_id": session_id, "cwd": "/tmp/l06"}

            def human_request_completion(self, _request_id):
                return self.row.copy()

            def claim_human_completion(self, _request_id):
                return True

            def record_herder_completion_receipt(self, _request_id, registry_request_id, idem, receipt, input_id):
                self.row.update(registry_request_id=registry_request_id, herder_idempotency_key=idem,
                                herder_receipt=receipt, herder_input_id=input_id)
                return self.row.copy()

            def mark_human_completion(self, _request_id, state, reason=""):
                self.row.update(state=state, reason=reason)
                return self.row.copy()

        record = {"requestId": request_id, "status": "resumed", "resultRef": result_ref,
                  "target": {"agent": "opencode", "sessionId": session_id, "cwd": "/tmp/l06"},
                  "idempotencyKey": key, "receipt": transport_receipt}
        center = Center()
        with patch.dict(os.environ, {"NOTIFY_HUMAN_COMPLETION_URL": NEW}), \
                patch("notification_center.agent_job_helper.resume_bound_native_session", return_value={"status": "resumed"}), \
                patch("notification_center.agent_job_helper.read_herder_human_request", return_value=record) as readback:
            result = deliver_human_completion(center, {"request_id": request_id}, "qa")

        input_id = "herder-resume-" + hashlib.sha256(key.encode()).hexdigest()[:32]
        self.assertEqual({"status": "resumed", "state": "accepted", "request_id": request_id,
                          "result_ref": result_ref, "idempotency_key": key, "receipt": transport_receipt,
                          "input_id": input_id}, result)
        self.assertEqual("sent", center.row["state"])
        self.assertEqual(transport_receipt, center.row["herder_receipt"])
        readback.assert_called_once_with(request_id, unittest.mock.ANY)

    def test_task_completion_readback_mismatch_stays_unknown_and_never_replays(self) -> None:
        request_id = "1b45fd65-6013-4925-a781-afd5367d7b1f"
        class Center:
            def __init__(self):
                self.row = {"state": "retryable", "result_ref": "6944969d-ac29-5003-fba0-a545f0c2891c",
                            "registry_request_id": request_id, "harness": "opencode", "session_id": "session",
                            "cwd": "/tmp/l06"}
                self.resume_claims = 0

            def human_request_completion(self, _request_id): return self.row.copy()
            def claim_human_completion(self, _request_id): self.resume_claims += 1; return True
            def record_herder_completion_receipt(self, *_args): raise AssertionError("mismatched proof must not persist")
            def mark_human_completion(self, _request_id, state, reason=""):
                self.row.update(state=state, reason=reason)
                return self.row.copy()

        center = Center()
        with patch.dict(os.environ, {"NOTIFY_HUMAN_COMPLETION_URL": NEW}), \
                patch("notification_center.agent_job_helper.resume_bound_native_session", return_value={"status": "resumed"}) as resume, \
                patch("notification_center.agent_job_helper.read_herder_human_request", return_value={
                    "requestId": request_id, "status": "resumed", "resultRef": "different-answer",
                    "target": {"agent": "opencode", "sessionId": "session", "cwd": "/tmp/l06"},
                    "idempotencyKey": "k", "receipt": "agent-herder://opencode/session/k",
                }):
            result = deliver_human_completion(center, {"request_id": request_id}, "qa")
            repeated = deliver_human_completion(center, {"request_id": request_id}, "qa")

        self.assertEqual("unknown", result["status"])
        self.assertEqual("unknown", repeated["status"])
        self.assertEqual("unknown", center.row["state"])
        self.assertEqual(1, center.resume_claims)
        resume.assert_called_once()


if __name__ == "__main__":
    unittest.main()
