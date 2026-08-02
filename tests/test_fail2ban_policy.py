from __future__ import annotations

import unittest
from pathlib import Path


class Fail2banPolicyTests(unittest.TestCase):
    def test_ban_notifications_are_notice_severity(self) -> None:
        action = (Path(__file__).parents[1] / "deploy" / "fail2ban" / "notify-center.conf").read_text()

        self.assertIn("--severity notice", action)
        self.assertNotIn("--severity important", action)
