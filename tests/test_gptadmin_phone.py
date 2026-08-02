"""Tests for the narrow fixed-command GPTAdmin phone adapter."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from notification_center.gptadmin_phone import FIXED_PHONE_COMMAND, GptAdminPhoneAdapter
from notification_center.http_api import android_phone_from_environment


class _Response:
    status = 202

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"completed"}'


class GptAdminPhoneAdapterTests(unittest.TestCase):
    def test_only_the_fixed_phone_command_reaches_shellmcp(self) -> None:
        requests: list[object] = []

        def runner(request: object, **_kwargs: object) -> _Response:
            requests.append(request)
            return _Response()

        adapter = GptAdminPhoneAdapter("https://gptadmin.example/server/s21/actions/tools/shell_exec", "test-token", runner=runner)
        adapter.phone_call({"incident": {"title": "untrusted title", "body": "$(danger)", "recipient": "+79990000000"}})

        self.assertTrue(adapter.can_phone_call)
        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual("https://gptadmin.example/server/s21/actions/tools/shell_exec", request.full_url)
        self.assertEqual("Bearer test-token", request.get_header("Authorization"))
        self.assertEqual({"cmd": FIXED_PHONE_COMMAND}, json.loads(request.data))

    def test_missing_credentials_disable_the_adapter(self) -> None:
        adapter = GptAdminPhoneAdapter("", "")
        self.assertFalse(adapter.can_phone_call)
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            adapter.phone_call({})

    def test_rejects_non_successful_shellmcp_response(self) -> None:
        class FailedResponse(_Response):
            status = 500

        adapter = GptAdminPhoneAdapter("https://gptadmin.example/server/s21/actions/tools/shell_exec", "test-token", runner=lambda *_args, **_kwargs: FailedResponse())
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            adapter.phone_call({})

    def test_environment_selects_the_fixed_adapter_and_rejects_a_mixed_transport(self) -> None:
        with mock.patch.dict(os.environ, {
            "GPTADMIN_ANDROID_PHONE_CALL_URL": "https://gptadmin.example/server/s21/actions/tools/shell_exec",
            "GPTADMIN_ANDROID_PHONE_CALL_TOKEN": "test-token",
        }, clear=True):
            self.assertIsInstance(android_phone_from_environment(), GptAdminPhoneAdapter)
        with mock.patch.dict(os.environ, {
            "GPTADMIN_ANDROID_PHONE_CALL_URL": "https://gptadmin.example/server/s21/actions/tools/shell_exec",
            "GPTADMIN_ANDROID_PHONE_CALL_TOKEN": "test-token",
            "ANDROID_ADB_SERIAL": "R5CR702SRFP",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "cannot be combined"):
                android_phone_from_environment()
