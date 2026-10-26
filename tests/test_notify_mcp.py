import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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


class Mcp2ContractTests(unittest.TestCase):
    def test_initialize_negotiates_mcp2_and_ping(self):
        # The 2026-07-28 revision is a stateless per-request envelope this
        # server does not implement, so it is never claimed: the handshake
        # falls back to the newest supported classic revision instead.
        init = notify_mcp.dispatch({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28"}})
        self.assertEqual("2025-11-25", init["result"]["protocolVersion"])
        echoed = notify_mcp.dispatch({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}})
        self.assertEqual("2025-06-18", echoed["result"]["protocolVersion"])
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

    def test_noticeplace_mute_tools_remain_routable_but_hidden(self):
        # The public manifest is the one unified tool; the mute operations
        # stay routable as its actions and as hidden legacy aliases.
        public = {tool["name"] for tool in notify_mcp.tool_specs()}
        self.assertEqual({"noticeplace"}, public)
        routable = {tool["name"] for tool in notify_mcp.tool_manifest(deep=True)}
        self.assertIn("noticeplace_list_mutes", routable)
        self.assertIn("noticeplace_set_notifications", routable)


class UnifiedToolContractTests(unittest.TestCase):
    def test_public_manifest_is_exactly_one_unified_tool(self):
        specs = notify_mcp.tool_specs()
        self.assertEqual(["noticeplace"], [spec["name"] for spec in specs])
        enum = specs[0]["inputSchema"]["properties"]["action"]["enum"]
        self.assertNotIn("noticeplace", enum)
        # Every legacy tool remains reachable as an action of the unified tool.
        self.assertEqual(enum, sorted(set(notify_mcp.TOOLS) - {"noticeplace"}))

    def test_unified_dispatch_runs_the_existing_handler(self):
        seen = {}
        def fake_ask_human(args):
            seen.update(args)
            return {"ok": True}
        with mock.patch.dict(notify_mcp.TOOLS, {"ask_human": {**notify_mcp.TOOLS["ask_human"], "handler": fake_ask_human}}):
            response = notify_mcp.dispatch({
                "jsonrpc": "2.0", "id": 11, "method": "tools/call",
                "params": {"name": "noticeplace", "arguments": {"action": "ask_human", "params": {"message": "проверка"}}},
            })
        self.assertTrue(response["result"]["structuredContent"]["ok"])
        self.assertEqual({"message": "проверка"}, seen)

    def test_unified_instructions_dispatch_without_params(self):
        response = notify_mcp.dispatch({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "noticeplace", "arguments": {"action": "noticeplace_instructions"}},
        })
        self.assertEqual("noticeplace.instructions.v1", response["result"]["structuredContent"]["schema"])

    def test_hidden_legacy_alias_still_dispatches_for_internal_callers(self):
        response = notify_mcp.dispatch({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "noticeplace_instructions", "arguments": {}},
        })
        self.assertEqual("noticeplace.instructions.v1", response["result"]["structuredContent"]["schema"])
        self.assertNotIn("noticeplace_instructions", {tool["name"] for tool in notify_mcp.tool_specs()})

    def test_unified_rejects_unknown_empty_and_self_actions(self):
        for arguments in ({"action": "nope"}, {}, {"action": "noticeplace"},
                          {"action": "ask_human", "params": "not-an-object"}):
            response = notify_mcp.dispatch({
                "jsonrpc": "2.0", "id": 14, "method": "tools/call",
                "params": {"name": "noticeplace", "arguments": arguments},
            })
            self.assertIn("error", response, arguments)
        unknown = notify_mcp.dispatch({
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "noticeplace", "arguments": {"action": "nope"}},
        })
        self.assertIn("unknown action", unknown["error"]["message"])
