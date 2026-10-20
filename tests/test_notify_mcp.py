import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from mcp import notify_mcp
from notification_center.agentcall_phone import AgentCallPhoneAdapter


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

    def readiness(self):
        return {"ready": True, "question_ready": False}

    def phone_call(self, payload):
        self.payloads.append(payload)


class _ReadyPhoneMesh:
    def __init__(self):
        self.payloads = []

    def readiness(self, _timeout=15):
        return {"ready": True, "question_ready": False, "reason": "direct_route_ready"}

    def __call__(self, _socket_path, payload, _timeout):
        self.payloads.append(payload)
        return b'{"ok":true,"call_id":"synthetic-call"}'


class DirectCallToolTests(unittest.TestCase):
    def test_phone_adapter_reads_only_mcp_scoped_phone_env_file(self):
        with TemporaryDirectory() as tempdir:
            phone_file = Path(tempdir) / "phone.env"
            phone_file.write_text("ANDROID_ADB_SERIAL=R5CR702SRFP\nANDROID_TELEGRAM_TARGET=target\n")
            fake_adapter = object()
            with mock.patch.dict("os.environ", {"NOTIFY_PHONE_SECRETS_FILE": str(phone_file)}, clear=False), \
                 mock.patch("notification_center.http_api.android_phone_from_environment", return_value=fake_adapter) as factory:
                result = notify_mcp.phone_adapter_from_environment()

        self.assertIs(fake_adapter, result)
        factory.assert_called_once_with()

    def test_phone_adapter_loads_explicit_agentcall_mesh_config_from_scoped_file(self):
        with TemporaryDirectory() as tempdir:
            phone_file = Path(tempdir) / "phone.env"
            phone_file.write_text(
                "AGENTCALL_PHONE_SOCKET=/run/agentcall/control.sock\n"
                "NOTICEPLACE_PHONE_PEERS_JSON={\"peer\":{\"address\":\"127.0.0.1\",\"port\":9443}}\n"
                "NOTICEPLACE_MESH_CORE_SOCKET=/run/noticeplace/core.sock\n"
                "NOTICEPLACE_MESH_HOST_ID=noticeplace-test\n"
                "NOTICEPLACE_MESH_CA=/run/keys/ca.pem\n"
                "NOTICEPLACE_MESH_CERT=/run/keys/client.pem\n"
                "NOTICEPLACE_MESH_KEY=/run/keys/client.key\n"
            )
            fake_adapter = object()
            with mock.patch.dict("os.environ", {"NOTIFY_PHONE_SECRETS_FILE": str(phone_file)}, clear=True), \
                 mock.patch("notification_center.http_api.android_phone_from_environment", return_value=fake_adapter) as factory:
                result = notify_mcp.phone_adapter_from_environment()

        self.assertIs(fake_adapter, result)
        factory.assert_called_once_with()

    def test_agentcall_selection_precedes_android_when_both_are_configured(self):
        with mock.patch.dict(os.environ, {
            "AGENTCALL_PHONE_SOCKET": "/run/agentcall/control.sock",
            "ANDROID_ADB_SERIAL": "synthetic-device",
            "ANDROID_TELEGRAM_TARGET": "synthetic-target",
        }, clear=True):
            adapter = notify_mcp.phone_adapter_from_environment()

        self.assertIsInstance(adapter, AgentCallPhoneAdapter)
        self.assertTrue(adapter.can_phone_call)
        self.assertFalse(adapter.readiness()["ready"])

    def test_agentcall_readiness_failure_never_submits_a_call(self):
        phone = _Phone()
        phone.readiness = lambda: {"ready": False, "question_ready": False, "reason": "phone_gateway_unavailable"}
        with mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=None), \
             mock.patch("mcp.notify_mcp.phone_adapter_from_environment", return_value=phone):
            with self.assertRaisesRegex(RuntimeError, "not ready"):
                notify_mcp.tool_call({"channel": "phone", "message": "служба уведомлений"})

        self.assertEqual([], phone.payloads)

    def test_ready_agentcall_is_selected_over_android_and_returns_safe_receipt(self):
        with TemporaryDirectory() as tempdir:
            phone_file = Path(tempdir) / "phone.env"
            phone_file.write_text(
                "AGENTCALL_PHONE_SOCKET=/run/agentcall/control.sock\n"
                "NOTICEPLACE_PHONE_PEERS_JSON={\"peer\":{\"address\":\"127.0.0.1\",\"port\":9443}}\n"
                "NOTICEPLACE_MESH_CORE_SOCKET=/run/noticeplace/core.sock\n"
                "NOTICEPLACE_MESH_HOST_ID=noticeplace-test\n"
                "NOTICEPLACE_MESH_CA=/run/keys/ca.pem\n"
                "NOTICEPLACE_MESH_CERT=/run/keys/client.pem\n"
                "NOTICEPLACE_MESH_KEY=/run/keys/client.key\n"
                "ANDROID_ADB_SERIAL=synthetic-offline-device\n"
                "ANDROID_TELEGRAM_TARGET=synthetic-target\n"
            )
            mesh = _ReadyPhoneMesh()
            with mock.patch.dict("os.environ", {"NOTIFY_PHONE_SECRETS_FILE": str(phone_file)}, clear=True), \
                 mock.patch("notification_center.mesh_phone.PhoneMeshRequester", return_value=mesh), \
                 mock.patch("mcp.notify_mcp._quiet_hours_suppression", return_value=None):
                result = notify_mcp.tool_call({"channel": "phone", "message": "служба уведомлений", "repeat": 1})

        self.assertTrue(result["ok"])
        self.assertEqual("direct_one_way_tts", result["mode"])
        self.assertEqual({"call_id": "synthetic-call", "ok": True}, result["receipt"])
        self.assertEqual(1, len(mesh.payloads))
        request = json.loads(mesh.payloads[0])
        self.assertNotIn("recipient", request)
        self.assertIn("служба уведомлений", request["message"])

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
        self.assertEqual("direct_one_way_tts", result["mode"])
        self.assertFalse(result["interactive"])
        self.assertIsNone(result["receipt"])
        self.assertTrue(result["submitted"])
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


class Mcp2ContractTests(unittest.TestCase):
    def test_initialize_negotiates_mcp2_and_ping(self):
        init = notify_mcp.dispatch({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
        self.assertEqual("2025-11-25", init["result"]["protocolVersion"])
        self.assertIn("noticeplace_instructions", init["result"]["instructions"])
        self.assertEqual({}, notify_mcp.dispatch({"jsonrpc":"2.0","id":2,"method":"ping","params":{}})["result"])

    def test_noticeplace_instructions_are_short_and_actionable(self):
        response = notify_mcp.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "noticeplace_instructions", "arguments": {}},
        })
        result = response["result"]["structuredContent"]
        self.assertEqual("noticeplace.instructions.v1", result["schema"])
        self.assertIn("health-diagnosis", result["automatic_repair"])
        self.assertIn("Кто сломался", result["message_format"][0])
        self.assertIn("Включить обратно", result["telegram_controls"])

    def test_noticeplace_mute_tools_are_discoverable(self):
        tools = {tool["name"] for tool in notify_mcp.tool_specs()}
        self.assertIn("noticeplace_list_mutes", tools)
        self.assertIn("noticeplace_set_notifications", tools)
