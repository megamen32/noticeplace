import unittest
from unittest import mock

from mcp import notify_mcp


class _Matrix:
    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return {"answered": False, "actor": None}


class _Phone:
    can_phone_call = True

    def __init__(self):
        self.payloads = []

    def phone_call(self, payload):
        self.payloads.append(payload)


class DirectCallToolTests(unittest.TestCase):
    def test_matrix_call_is_direct_and_returns_bridge_receipt(self):
        matrix = _Matrix()
        with mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=None), \
             mock.patch("notification_center.http_api.matrix_call_from_environment", return_value=matrix):
            result = notify_mcp.tool_call({"channel": "matrix", "message": "Please call me"})

        self.assertTrue(result["ok"])
        self.assertEqual("matrix", result["channel"])
        self.assertEqual({"answered": False, "actor": None}, result["receipt"])
        self.assertEqual("Please call me", matrix.payloads[0]["incident"]["body"])
        self.assertEqual("direct", matrix.payloads[0]["incident"]["kind"])

    def test_phone_call_is_direct_and_never_creates_an_incident(self):
        phone = _Phone()
        with mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=None), \
             mock.patch("notification_center.http_api.android_phone_from_environment", return_value=phone):
            result = notify_mcp.tool_call({"channel": "phone", "message": "Hermes ждёт пароль", "repeat": 2, "hangup_after": True})

        self.assertTrue(result["ok"])
        self.assertEqual("phone", result["channel"])
        self.assertEqual("direct", phone.payloads[0]["kind"])
        self.assertEqual({"text": "Hermes ждёт пароль", "repeat": 2, "hangup_after": True}, phone.payloads[0]["voice"])

    def test_direct_phone_call_is_suppressed_during_quiet_hours(self):
        phone = _Phone()
        quiet_rule = {
            "start": "01:00",
            "end": "09:00",
            "timezone": "Europe/Moscow",
            "suppress": ["call"],
        }
        with mock.patch("notification_center.http_api.android_phone_from_environment", return_value=phone), \
             mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=quiet_rule):
            result = notify_mcp.tool_call({"channel": "phone", "message": "Не звони ночью"})

        self.assertTrue(result["ok"])
        self.assertTrue(result["suppressed"])
        self.assertEqual("quiet_hours", result["reason"])
        self.assertEqual(quiet_rule, result["quiet_hours"])
        self.assertEqual([], phone.payloads)

    def test_call_rejects_unknown_or_unavailable_channel(self):
        with self.assertRaisesRegex(ValueError, "channel"):
            notify_mcp.tool_call({"channel": "whatsapp"})
        with mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=None), \
             mock.patch("notification_center.http_api.matrix_call_from_environment", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Matrix"):
                notify_mcp.tool_call({"channel": "matrix"})


if __name__ == "__main__":
    unittest.main()
