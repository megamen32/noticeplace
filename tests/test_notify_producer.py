"""Contract tests for the dependency-light shell producer used by Fail2ban."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "bin" / "notify-producer"
FAIL2BAN_ACTION = ROOT / "deploy" / "fail2ban" / "notify-center.conf"


class _CaptureHandler(BaseHTTPRequestHandler):
    """Capture one producer request without logging headers or secrets."""

    request: dict[str, Any] = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler spelling
        length = int(self.headers["Content-Length"])
        type(self).request = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "idempotency_key": self.headers.get("Idempotency-Key"),
            "body": json.loads(self.rfile.read(length)),
        }
        self.send_response(HTTPStatus.ACCEPTED)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class NotifyProducerTests(unittest.TestCase):
    """Prove the producer emits the public API contract and fails safely."""

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.environment = {
            **os.environ,
            "NOTIFY_CENTER_EVENT_URL": f"http://127.0.0.1:{self.server.server_port}/v1/events",
            "NOTIFY_CENTER_TOKEN": "producer-test-token",
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_producer(self, *arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PRODUCER), *arguments],
            text=True,
            capture_output=True,
            env=environment or self.environment,
            timeout=5,
            check=False,
        )

    def test_sends_a_secret_safe_idempotent_incident(self) -> None:
        result = self.run_producer(
            "--project", "fail2ban.100",
            "--recipient", "me",
            "--severity", "important",
            "--dedupe-key", "fail2ban.100:sshd:198.51.100.10",
            "--title", "Fail2ban ban: sshd",
            "--body", "host 100 banned 198.51.100.10",
            "--idempotency-key", "event-1",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("/v1/events", _CaptureHandler.request["path"])
        self.assertEqual("Bearer producer-test-token", _CaptureHandler.request["authorization"])
        self.assertEqual("event-1", _CaptureHandler.request["idempotency_key"])
        self.assertEqual(
            {
                "schema": "notify.event.v1",
                "project": "fail2ban.100",
                "recipient": "me",
                "kind": "incident",
                "severity": "important",
                "dedup_key": "fail2ban.100:sshd:198.51.100.10",
                "title": "Fail2ban ban: sshd",
                "body": "host 100 banned 198.51.100.10",
            },
            _CaptureHandler.request["body"],
        )
        self.assertNotIn("producer-test-token", result.stdout + result.stderr)

    def test_resolve_uses_the_same_deduplication_identity(self) -> None:
        result = self.run_producer(
            "--resolve",
            "--project", "fail2ban.100",
            "--recipient", "me",
            "--dedupe-key", "fail2ban.100:sshd:198.51.100.10",
            "--idempotency-key", "event-2",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "schema": "notify.event.v1",
                "action": "resolve",
                "project": "fail2ban.100",
                "recipient": "me",
                "dedup_key": "fail2ban.100:sshd:198.51.100.10",
            },
            _CaptureHandler.request["body"],
        )

    def test_missing_token_fails_without_printing_other_environment_values(self) -> None:
        environment = {**self.environment, "NOTIFY_CENTER_TOKEN": "", "UNRELATED_SECRET": "must-not-leak"}
        result = self.run_producer("--check-config", environment=environment)

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("must-not-leak", result.stdout + result.stderr)

    def test_fail2ban_action_uses_the_producer_and_resolves_unbans(self) -> None:
        """Keep security enforcement local while removing the direct Telegram API call."""
        action = FAIL2BAN_ACTION.read_text()
        self.assertIn("/usr/local/bin/notify-producer", action)
        self.assertIn("--resolve", action)
        self.assertIn("$(cat /proc/sys/kernel/random/uuid)", action)
        self.assertNotIn("%", action)
        self.assertNotIn("$$(", action)
        self.assertNotIn("api.telegram.org", action)

    def test_fail2ban_action_expands_environment_without_turning_variables_into_pid(self) -> None:
        """Execute rendered action syntax, catching the historical ``$$`` shell bug."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captured = root / "arguments.txt"
            producer = root / "notify-producer"
            producer.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURED_ARGUMENTS\"\n")
            producer.chmod(0o755)
            environment = root / "notify-center.env"
            environment.write_text("NOTIFY_PROJECT=fail2ban.server-100\nNOTIFY_RECIPIENT=me\nNOTIFY_CENTER_TOKEN=contract-token\n")
            action = next(line.split("=", 1)[1].strip() for line in FAIL2BAN_ACTION.read_text().splitlines() if line.startswith("actionban ="))
            rendered = (
                action.replace("/etc/fail2ban/notify-center.env", str(environment))
                .replace("/usr/local/bin/notify-producer", str(producer))
                .replace("<name>", "sshd")
                .replace("<ip>", "198.51.100.10")
                .replace("<bantime>", "600")
            )
            result = subprocess.run(["/bin/sh", "-c", rendered], env={**os.environ, "CAPTURED_ARGUMENTS": str(captured)}, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            arguments = captured.read_text().splitlines()
            self.assertIn("fail2ban.server-100", arguments)
            self.assertIn("fail2ban:fail2ban.server-100:sshd:198.51.100.10", arguments)
            self.assertFalse(any("NOTIFY_PROJECT" in argument for argument in arguments))


if __name__ == "__main__":
    unittest.main()
