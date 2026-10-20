"""Exercise task-decision upkeep through the actual production worker loop."""

from __future__ import annotations

import runpy
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker


class _Authority:
    def __call__(self, mandate):
        return mandate

    def is_actor_allowed(self, _context, _actor):
        return True

    def timeout_allowed_now(self, _context):
        return True


class NotifyCenterWorkerTests(unittest.TestCase):
    def test_agent_worker_loop_resolves_receipt_started_task_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            center = NotificationCenter(
                Path(tempdir) / "notify.sqlite3", {"token": {"project": "airlock"}},
                task_decision_verifier=_Authority(),
            )
            try:
                mandate = {
                    "task_ref": "board/task-worker", "purpose": "worker-loop", "version": "v1",
                    "mandate_ref": "grant:worker-loop:v1", "project": "airlock", "recipient": "operator",
                    "message": "Choose a path.", "allowed_actors": ["telegram:42"],
                    "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
                    "timeout_value": "continue", "continuation": {"kind": "bounded", "limit": 1},
                }
                request = center.create_task_decision_request("token", mandate)
                request_id = str(request["request_id"])
                self.assertTrue(center.task_decisions.reserve_notification(request_id))
                with mock.patch("notification_center.task_decisions.time.time", return_value=100.0):
                    center.bind_human_request_message("token", request_id, "42", 91)

                worker = DeliveryWorker(center, object())
                iteration = runpy.run_path(str(Path(__file__).parents[1] / "bin" / "notify-center"))["worker_iteration"]
                with mock.patch("notification_center.task_decisions.time.time", return_value=1900.0):
                    iteration(
                        worker, "agent", 1, set(), None, None,
                        time.monotonic() + 60, None, time.monotonic() + 60, object(),
                    )

                result = center.get_human_request("token", request_id)
                self.assertEqual("expired", result["state"])
                self.assertEqual("timeout", result["task_decision"]["source"])
                self.assertEqual("continue", result["task_decision"]["value"])
            finally:
                center._connection.close()


if __name__ == "__main__":
    unittest.main()
