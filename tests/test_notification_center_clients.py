"""Contract tests for the distributable NoticePlace Python and Node clients."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE = ROOT / "python"
NODE_CLIENT = ROOT / "npm" / "notification-center-client.js"
sys.path.insert(0, str(PYTHON_PACKAGE))

from notify_center_client import NotificationCenterClient  # noqa: E402
from notification_center.core import NotificationCenter  # noqa: E402
from notification_center.http_api import build_handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class NotificationCenterClientContractTests(unittest.TestCase):
    """Both SDKs emit one event and wait only for an operator state change."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"project-token": {"project": "sdk-demo", "max_severity": "critical"}},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health-token", "mcp-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.event_url = f"http://127.0.0.1:{self.server.server_port}/v1/events"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tempdir.cleanup()

    def test_python_client_emits_then_waits_for_acknowledgement(self) -> None:
        client = NotificationCenterClient(self.event_url, "project-token", request_timeout=1)
        created = client.emit(
            project="sdk-demo",
            severity="important",
            title="Python SDK canary",
            dedup_key="sdk:python",
            idempotency_key="python-canary-1",
        )
        self.assertEqual("open", created["state"])
        incident_id = str(created["incident_id"])
        self.center.acknowledge(incident_id, actor="test:operator")

        answered = client.wait_for_response(incident_id, timeout_seconds=0.2, poll_interval_seconds=0.01)

        self.assertEqual("acknowledged", answered["state"])

    def test_node_client_emits_then_waits_for_acknowledgement(self) -> None:
        script = """
const { NotificationCenterClient } = await import(process.argv[1]);
const client = new NotificationCenterClient({ eventUrl: process.argv[2], token: process.argv[3], requestTimeoutMs: 1000 });
const created = await client.emit({ project: 'sdk-demo', severity: 'important', title: 'Node SDK canary', dedupKey: 'sdk:node', idempotencyKey: 'node-canary-1' });
const answered = await client.waitForResponse(created.incident_id, { timeoutMs: 1000, pollIntervalMs: 10 });
process.stdout.write(JSON.stringify(answered));
"""
        process = subprocess.Popen(
            ["node", "--input-type=module", "--eval", script, NODE_CLIENT.as_uri(), self.event_url, "project-token"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 1
        incident_id: str | None = None
        while time.monotonic() < deadline:
            incidents = self.center.list_incidents()
            if incidents:
                incident_id = str(incidents[0]["id"])
                break
            time.sleep(0.01)
        self.assertIsNotNone(incident_id, "Node SDK did not create an incident")
        self.center.acknowledge(str(incident_id), actor="test:operator")
        stdout, stderr = process.communicate(timeout=2)
        self.assertEqual(0, process.returncode, stderr)
        self.assertEqual("acknowledged", json.loads(stdout)["state"])


if __name__ == "__main__":
    unittest.main()
