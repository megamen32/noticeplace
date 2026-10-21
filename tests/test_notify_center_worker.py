"""Exercise task-decision upkeep through the actual production worker loop."""

from __future__ import annotations

import runpy
import tempfile
import threading
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
    def test_worker_shutdown_stops_claims_and_waits_for_admitted_delivery(self) -> None:
        delivery_started = threading.Event()
        finish_delivery = threading.Event()
        shutdown = threading.Event()

        class Worker:
            _center = object()

            def __init__(self):
                self.claim_calls = 0

            def claim_due(self, limit, channel_group):
                self.claim_calls += 1
                return [{"id": "already-claimed"}]

            def deliver(self, delivery):
                self.assert_claimed = delivery["id"] == "already-claimed"
                delivery_started.set()
                finish_delivery.wait(3)

        worker = Worker()
        worker_loop = runpy.run_path(str(Path(__file__).parents[1] / "bin" / "notify-center"))["worker_loop"]
        thread = threading.Thread(target=worker_loop, args=(worker, "message", 2, shutdown, threading.Lock()))
        thread.start()
        self.assertTrue(delivery_started.wait(2))

        shutdown.set()
        time.sleep(1.1)
        self.assertEqual(1, worker.claim_calls)
        self.assertTrue(thread.is_alive(), "shutdown must wait for the already-admitted provider call")

        finish_delivery.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, worker.claim_calls)

    def test_http_runner_closes_listener_when_shutdown_is_requested(self) -> None:
        shutdown = threading.Event()
        entered = threading.Event()

        class Server:
            timeout = None
            daemon_threads = True
            closed = False

            def handle_request(self):
                entered.set()
                time.sleep(0.005)

            def server_close(self):
                self.closed = True

        server = Server()
        run_http = __import__("notification_center.http_api", fromlist=["run_http"]).run_http
        with mock.patch("notification_center.http_api.ThreadingHTTPServer", return_value=server):
            thread = threading.Thread(target=run_http, args=(object(), "127.0.0.1", 0, "test-health-token", "test-mcp-token"), kwargs={"shutdown_event": shutdown})
            thread.start()
            self.assertTrue(entered.wait(1))
            shutdown.set()
            thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(server.closed)
        self.assertFalse(server.daemon_threads)

    def test_telegram_poller_stops_after_current_poll_and_is_joinable(self) -> None:
        poll_started = threading.Event()
        finish_poll = threading.Event()
        shutdown = threading.Event()

        class Poller:
            calls = 0

            def poll_once(self):
                self.calls += 1
                poll_started.set()
                finish_poll.wait(3)

        poller = Poller()
        telegram_loop = runpy.run_path(str(Path(__file__).parents[1] / "bin" / "notify-center"))["telegram_interaction_loop"]
        with mock.patch.dict("os.environ", {"NOTIFY_CENTER_DB": "sqlite:test"}):
            thread = threading.Thread(target=telegram_loop, args=(poller, shutdown))
            thread.start()
            self.assertTrue(poll_started.wait(2))
            shutdown.set()
            time.sleep(0.05)
            self.assertTrue(thread.is_alive(), "shutdown must let the current poll finish")
            self.assertEqual(1, poller.calls)
            finish_poll.set()
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, poller.calls, "shutdown must prevent another poll_once")

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
