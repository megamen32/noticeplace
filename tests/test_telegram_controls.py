from __future__ import annotations

import unittest

from notification_center.http_api import telegram_inline_keyboard
from notification_center.telegram_interactions import TelegramActionCodec


class TelegramControlPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = TelegramActionCodec("test-callback-secret")

    def test_important_message_keeps_ask_without_acknowledgement_controls(self) -> None:
        keyboard = telegram_inline_keyboard(self.codec, {"id": "inc_important", "severity": "important"})

        self.assertEqual([["Ask"]], [[button["text"] for button in row] for row in keyboard["inline_keyboard"]])

    def test_only_exact_critical_gets_acknowledgement_and_snooze_controls(self) -> None:
        critical = telegram_inline_keyboard(self.codec, {"id": "inc_critical", "severity": "critical"})
        emergency = telegram_inline_keyboard(self.codec, {"id": "inc_emergency", "severity": "emergency"})

        self.assertEqual([["ACK", "Snooze 15m"], ["Ask"]], [[button["text"] for button in row] for row in critical["inline_keyboard"]])
        self.assertEqual([["Ask"]], [[button["text"] for button in row] for row in emergency["inline_keyboard"]])
