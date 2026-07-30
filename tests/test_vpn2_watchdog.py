"""Regression tests for the independent vpn2 notification-center watchdog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.watchdog import Watchdog, WatchdogConfig


class Vpn2WatchdogTests(unittest.TestCase):
    """Prove a separate process alerts only after durable consecutive failures."""

    def test_alerts_after_threshold_without_using_primary_notification_api(self) -> None:
        """Emit one direct alert and a single recovery after independent probes recover."""
        with tempfile.TemporaryDirectory() as tempdir:
            messages: list[str] = []
            watchdog = Watchdog(
                WatchdogConfig(failure_threshold=3, recovery_threshold=2, alert_retry_seconds=60),
                Path(tempdir) / "state.json",
                probe=lambda: False,
                direct_sender=messages.append,
                clock=lambda: 1000,
            )
            watchdog.run_once()
            watchdog.run_once()
            watchdog.run_once()
            watchdog.run_once()

            self.assertEqual(1, len(messages))
            self.assertIn("DOWN", messages[0])

            recovered = Watchdog(
                WatchdogConfig(failure_threshold=3, recovery_threshold=2, alert_retry_seconds=60),
                Path(tempdir) / "state.json",
                probe=lambda: True,
                direct_sender=messages.append,
                clock=lambda: 1100,
            )
            recovered.run_once()
            recovered.run_once()
            self.assertEqual(2, len(messages))
            self.assertIn("RECOVERED", messages[1])

    def test_failed_direct_alert_is_persisted_and_retried_after_cooldown(self) -> None:
        """Keep pending state after direct transport failure rather than silently dropping it."""
        with tempfile.TemporaryDirectory() as tempdir:
            state_path = Path(tempdir) / "state.json"
            attempts = 0

            def failing_sender(_message: str) -> None:
                """Simulate a direct Telegram transport error."""
                nonlocal attempts
                attempts += 1
                raise RuntimeError("telegram unavailable")

            first = Watchdog(WatchdogConfig(failure_threshold=1, alert_retry_seconds=60), state_path, lambda: False, failing_sender, lambda: 1000)
            first.run_once()
            self.assertEqual(1, attempts)
            self.assertTrue(first.state()["alert_pending"])

            sent: list[str] = []
            retry = Watchdog(WatchdogConfig(failure_threshold=1, alert_retry_seconds=60), state_path, lambda: False, sent.append, lambda: 1060)
            retry.run_once()
            self.assertEqual(1, len(sent))
            self.assertFalse(retry.state()["alert_pending"])


if __name__ == "__main__":
    unittest.main()
