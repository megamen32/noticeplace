"""Keep MCP actor routing aligned with the approved callback policy."""

import os
import unittest
from unittest.mock import patch

from mcp.notify_mcp import _human_request_config


class MCPActorPolicyTests(unittest.TestCase):
    def config(self, **environment):
        with patch.dict(os.environ, environment, clear=True), patch(
            "mcp.notify_mcp.notify_center_config",
            return_value={"event_url": "http://example.invalid/v1/events"},
        ):
            return _human_request_config()

    def test_callback_allowlist_is_the_final_explicit_policy_source(self):
        cfg = self.config(TELEGRAM_CALLBACK_ALLOWED_USER_IDS="42, telegram:7;42")
        self.assertEqual(["telegram:42", "telegram:7", "telegram:42"], cfg["actors"])

    def test_ask_human_override_keeps_precedence(self):
        cfg = self.config(ASK_HUMAN_ALLOWED_ACTORS="telegram:8",
                          NOTIFY_TELEGRAM_ALLOWED_USER_IDS="9",
                          TELEGRAM_CALLBACK_ALLOWED_USER_IDS="42")
        self.assertEqual(["telegram:8"], cfg["actors"])

    def test_notify_override_keeps_precedence(self):
        cfg = self.config(ASK_HUMAN_ALLOWED_ACTORS=" ",
                          NOTIFY_TELEGRAM_ALLOWED_USER_IDS="9",
                          TELEGRAM_CALLBACK_ALLOWED_USER_IDS="42")
        self.assertEqual(["telegram:9"], cfg["actors"])

    def test_recipient_or_chat_id_is_never_authority(self):
        with self.assertRaisesRegex(ValueError, "not configured"):
            self.config(TELEGRAM_CHAT_ID="42", NOTIFY_CENTER_RECIPIENT="42")

    def test_empty_approved_sources_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "not configured"):
            self.config(TELEGRAM_CALLBACK_ALLOWED_USER_IDS=" ; , ")

    def test_wildcard_callback_policy_fails_closed(self):
        with self.assertRaises(ValueError):
            self.config(TELEGRAM_CALLBACK_ALLOWED_USER_IDS="*")


if __name__ == "__main__":
    unittest.main()
