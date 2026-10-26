"""The human-answer callback must bypass only the owner-paused AI launch port."""

import os
import unittest
import urllib.request
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
