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
        with mock.patch("notification_center.http_api.matrix_call_from_environment", return_value=matrix):
            result = notify_mcp.tool_call({"channel": "matrix", "message": "Please call me"})

        self.assertTrue(result["ok"])
        self.assertEqual("matrix", result["channel"])
        self.assertEqual({"answered": False, "actor": None}, result["receipt"])
        self.assertEqual("Please call me", matrix.payloads[0]["incident"]["body"])
        self.assertEqual("direct", matrix.payloads[0]["incident"]["kind"])

    def test_phone_call_is_direct_and_never_creates_an_incident(self):
        phone = _Phone()
        with mock.patch("notification_center.http_api.android_phone_from_environment", return_value=phone):
            result = notify_mcp.tool_call({"channel": "phone"})

        self.assertTrue(result["ok"])
        self.assertEqual("phone", result["channel"])
        self.assertEqual("direct", phone.payloads[0]["kind"])

    def test_call_rejects_unknown_or_unavailable_channel(self):
        with self.assertRaisesRegex(ValueError, "channel"):
            notify_mcp.tool_call({"channel": "whatsapp"})
        with mock.patch("notification_center.http_api.matrix_call_from_environment", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Matrix"):
                notify_mcp.tool_call({"channel": "matrix"})


if __name__ == "__main__":
    unittest.main()
