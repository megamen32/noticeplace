"""Tests for the local AI incident-call adapter."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from notification_center.agentcall_phone import AgentCallPhoneAdapter
from notification_center.http_api import android_phone_from_environment


class AgentCallPhoneAdapterTests(unittest.TestCase):
    def test_incident_context_is_spoken_twice_through_local_control_socket(self) -> None:
        requests: list[dict[str, object]] = []

        def requester(_path: str, payload: bytes, _timeout: float) -> bytes:
            requests.append(json.loads(payload))
            return b'{"ok":true,"receipt_id":"call-123","call_id":"call-123"}\n'

        adapter = AgentCallPhoneAdapter("/run/agentcall/control.sock", requester=requester)
        result = adapter.phone_call({
            "incident": {
                "id": "incident-1",
                "severity": "critical",
                "title": "Сервер 88 недоступен",
                "body": "Три проверки завершились ошибкой.",
            }
        })

        self.assertTrue(adapter.can_phone_call)
        self.assertEqual("call-123", result["receipt_id"])
        self.assertEqual(2, requests[0]["repeat"])
        self.assertIn("Сломалось что-то", requests[0]["message"])
        self.assertIn("Сервер 88 недоступен", requests[0]["message"])
        self.assertIn("Три проверки", requests[0]["context"])

    def test_environment_prefers_agentcall_over_legacy_adb_settings(self) -> None:
        with mock.patch.dict(os.environ, {
            "AGENTCALL_PHONE_SOCKET": "/run/agentcall/control.sock",
            "ANDROID_ADB_SERIAL": "R5CR702SRFP",
            "ANDROID_TELEGRAM_TARGET": "legacy",
        }, clear=True):
            self.assertIsInstance(android_phone_from_environment(), AgentCallPhoneAdapter)


if __name__ == "__main__":
    unittest.main()
