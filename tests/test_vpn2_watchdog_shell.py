"""Black-box tests for the dependency-light vpn2 shell watchdog."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "deploy" / "vpn2" / "notification-center-watchdog.sh"


class Vpn2ShellWatchdogTests(unittest.TestCase):
    """Exercise the deployed shell entrypoint with a deterministic fake curl."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.curl_log = self.root / "curl.log"
        self.curl_arg_log = self.root / "curl-args.log"
        fake_curl = self.bin_dir / "curl"
        fake_curl.write_text(
            """#!/bin/sh
set -eu
config=$(mktemp)
trap 'rm -f "$config"' EXIT HUP INT TERM
printf '%s\\n' "$*" >>"$FAKE_CURL_ARG_LOG"
cat >"$config"
url=$(sed -n 's/^url = "\\(.*\\)"$/\\1/p' "$config")
printf '%s\\n' "$url" >>"$FAKE_CURL_LOG"
case "$url" in
  "$PRIMARY_HEALTH_URL")
    grep -F "header = \\"Authorization: Bearer $PRIMARY_HEALTH_TOKEN\\"" "$config" >/dev/null || exit 96
    output=$(sed -n 's/^output = "\\(.*\\)"$/\\1/p' "$config")
    printf '%s' "${FAKE_HEALTH_BODY:-{\\"schema\\":\\"notify.health.v1\\",\\"service\\":\\"notification-center\\",\\"status\\":\\"ok\\",\\"storage_ready\\":true,\\"dispatcher_ready\\":true}}" >"$output"
    printf '%s' "${FAKE_HEALTH_HTTP:-200}"
    exit "${FAKE_HEALTH_RC:-0}"
    ;;
  https://api.telegram.org/*)
    grep -F "data-urlencode = \\"chat_id=$TELEGRAM_CHAT_ID\\"" "$config" >/dev/null || exit 95
    grep -F 'data-urlencode = "text=NOTIFICATION CENTER ' "$config" >/dev/null || exit 94
    exit "${FAKE_TELEGRAM_RC:-0}"
    ;;
  "$MATRIX_HOMESERVER"/*)
    grep -F "header = \\"Authorization: Bearer $MATRIX_ACCESS_TOKEN\\"" "$config" >/dev/null || exit 93
    grep -F '\\"msgtype\\":\\"m.text\\"' "$config" >/dev/null || exit 92
    exit "${FAKE_MATRIX_RC:-0}"
    ;;
  *)
    exit 97
    ;;
esac
""",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)

    def run_cycle(self, now: int, **overrides: str) -> subprocess.CompletedProcess[str]:
        """Run one systemd-timer-style cycle with isolated state and fake transports."""
        environment = {
            **os.environ,
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "PRIMARY_HEALTH_URL": "https://notify.example.test/health",
            "PRIMARY_HEALTH_TOKEN": "test-health-token",
            "PRIMARY_EXPECTED_SERVICE": "notification-center",
            "WATCHDOG_STATE_FILE": str(self.root / "state"),
            "WATCHDOG_LOCK_DIR": str(self.root / "lock"),
            "WATCHDOG_NOW_EPOCH": str(now),
            "FAIL_THRESHOLD": "3",
            "RECOVERY_THRESHOLD": "2",
            "ALERT_RETRY_SECONDS": "60",
            "WATCHDOG_CHANNELS": "telegram,matrix",
            "TELEGRAM_BOT_TOKEN": "test-telegram-token",
            "TELEGRAM_CHAT_ID": "test-chat",
            "MATRIX_HOMESERVER": "https://matrix.example.test",
            "MATRIX_ROOM_ID_ENCODED": "%21alerts%3Aexample.test",
            "MATRIX_ACCESS_TOKEN": "test-matrix-token",
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_ARG_LOG": str(self.curl_arg_log),
            **overrides,
        }
        return subprocess.run(
            [str(WATCHDOG)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def curl_urls(self) -> list[str]:
        """Return fake-curl calls without exposing real credentials."""
        if not self.curl_log.exists():
            return []
        return self.curl_log.read_text(encoding="utf-8").splitlines()

    def state(self) -> dict[str, str]:
        """Parse the watchdog's intentionally simple, non-sourceable state format."""
        result: dict[str, str] = {}
        for line in (self.root / "state").read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            result[key] = value
        return result

    def test_threshold_suppresses_flaps_and_telegram_success_is_not_repeated(self) -> None:
        """Require consecutive failures and send one DOWN through the first healthy channel."""
        for now in (1000, 1010):
            result = self.run_cycle(now, FAKE_HEALTH_RC="1")
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(self.curl_urls()))

        third = self.run_cycle(1020, FAKE_HEALTH_RC="1")
        fourth = self.run_cycle(1030, FAKE_HEALTH_RC="1")
        self.assertEqual(0, third.returncode, third.stderr)
        self.assertEqual(0, fourth.returncode, fourth.stderr)
        urls = self.curl_urls()
        self.assertEqual(5, len(urls))
        self.assertEqual(1, sum("api.telegram.org" in url for url in urls))
        self.assertEqual(0, sum("matrix.example.test" in url for url in urls))
        self.assertEqual("1", self.state()["DOWN"])
        self.assertEqual("1", self.state()["ALERT_SENT"])

    def test_transport_failover_and_persisted_cooldown(self) -> None:
        """Fall through to Matrix, persist total failure, and retry only after cooldown."""
        first = self.run_cycle(
            1000,
            FAIL_THRESHOLD="1",
            FAKE_HEALTH_RC="1",
            FAKE_TELEGRAM_RC="22",
            FAKE_MATRIX_RC="22",
        )
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual("DOWN", self.state()["PENDING_KIND"])

        blocked = self.run_cycle(
            1059,
            FAIL_THRESHOLD="1",
            FAKE_HEALTH_RC="1",
            FAKE_TELEGRAM_RC="22",
            FAKE_MATRIX_RC="0",
        )
        self.assertEqual(0, blocked.returncode, blocked.stderr)
        self.assertEqual(2, sum("api.telegram.org" in url or "matrix.example.test" in url for url in self.curl_urls()))

        retry = self.run_cycle(
            1060,
            FAIL_THRESHOLD="1",
            FAKE_HEALTH_RC="1",
            FAKE_TELEGRAM_RC="22",
            FAKE_MATRIX_RC="0",
        )
        self.assertEqual(0, retry.returncode, retry.stderr)
        urls = self.curl_urls()
        self.assertEqual(2, sum("api.telegram.org" in url for url in urls))
        self.assertEqual(2, sum("matrix.example.test" in url for url in urls))
        self.assertEqual("", self.state()["PENDING_KIND"])
        self.assertEqual("1", self.state()["ALERT_SENT"])

    def test_recovery_requires_threshold_and_retries_direct_delivery(self) -> None:
        """Announce recovery only after consecutive success and retain it on send failure."""
        down = self.run_cycle(1000, FAIL_THRESHOLD="1", FAKE_HEALTH_RC="1")
        self.assertEqual(0, down.returncode, down.stderr)

        first_success = self.run_cycle(1010)
        self.assertEqual(0, first_success.returncode, first_success.stderr)
        self.assertEqual("1", self.state()["DOWN"])

        failed_recovery = self.run_cycle(1060, FAKE_TELEGRAM_RC="22", FAKE_MATRIX_RC="22")
        self.assertEqual(0, failed_recovery.returncode, failed_recovery.stderr)
        self.assertEqual("RECOVERED", self.state()["PENDING_KIND"])

        retried_recovery = self.run_cycle(1120, FAKE_TELEGRAM_RC="0")
        self.assertEqual(0, retried_recovery.returncode, retried_recovery.stderr)
        self.assertEqual("0", self.state()["DOWN"])
        self.assertEqual("", self.state()["PENDING_KIND"])
        self.assertGreaterEqual(sum("api.telegram.org" in url for url in self.curl_urls()), 3)

    def test_state_is_private_and_never_contains_transport_secrets(self) -> None:
        """Keep credentials only in the environment and write state mode 0600."""
        result = self.run_cycle(1000, FAIL_THRESHOLD="1", FAKE_HEALTH_RC="1")
        self.assertEqual(0, result.returncode, result.stderr)
        state_path = self.root / "state"
        mode = stat.S_IMODE(state_path.stat().st_mode)
        contents = state_path.read_text(encoding="utf-8")
        self.assertEqual(0o600, mode)
        self.assertNotIn("test-telegram-token", contents)
        self.assertNotIn("test-matrix-token", contents)
        self.assertNotIn("test-health-token", contents)
        self.assertNotIn("test-chat", contents)
        arguments = self.curl_arg_log.read_text(encoding="utf-8")
        self.assertNotIn("test-telegram-token", arguments)
        self.assertNotIn("test-matrix-token", arguments)
        self.assertNotIn("test-health-token", arguments)

    def test_untrusted_state_is_parsed_without_shell_evaluation(self) -> None:
        """Ignore malformed counters instead of evaluating state file contents as code."""
        marker = self.root / "state-was-evaluated"
        (self.root / "state").write_text(
            f"FAILURE_COUNT=$(touch {marker})\nDOWN=not-a-boolean\nPENDING_KIND=INVALID\n",
            encoding="utf-8",
        )
        result = self.run_cycle(1000, FAKE_HEALTH_RC="1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual("1", self.state()["FAILURE_COUNT"])
        self.assertEqual("0", self.state()["DOWN"])

    def test_stale_process_lock_is_recovered(self) -> None:
        """Do not stay blind forever after a killed process leaves its lock directory."""
        lock = self.root / "lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999\n", encoding="utf-8")
        result = self.run_cycle(1000)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("removed stale watchdog lock", result.stderr)
        self.assertTrue((self.root / "state").exists())
        self.assertFalse(lock.exists())

    def test_wrong_authenticated_health_contract_counts_as_failure(self) -> None:
        """Reject a 200 response whose identity does not match the primary service."""
        result = self.run_cycle(
            1000,
            FAIL_THRESHOLD="1",
            FAKE_HEALTH_BODY='{"schema":"notify.health.v1","service":"other","status":"ok","storage_ready":true,"dispatcher_ready":true}',
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1", self.state()["DOWN"])
        self.assertEqual(1, sum("api.telegram.org" in url for url in self.curl_urls()))

    def test_check_config_rejects_missing_selected_channel_secret(self) -> None:
        """Fail before probing when a configured failover channel has incomplete credentials."""
        environment = {
            **os.environ,
            "PRIMARY_HEALTH_URL": "https://notify.example.test/health",
            "PRIMARY_HEALTH_TOKEN": "health-token",
            "WATCHDOG_CHANNELS": "telegram,matrix",
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_CHAT_ID": "chat",
            "MATRIX_HOMESERVER": "https://matrix.example.test",
            "MATRIX_ROOM_ID_ENCODED": "%21alerts%3Aexample.test",
        }
        environment.pop("MATRIX_ACCESS_TOKEN", None)
        result = subprocess.run(
            [str(WATCHDOG), "--check-config"],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MATRIX_ACCESS_TOKEN", result.stderr)


if __name__ == "__main__":
    unittest.main()
