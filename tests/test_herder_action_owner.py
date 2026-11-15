"""Production contract tests for the Herder-backed task action owner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from notification_center.core import NotificationCenter
from notification_center.herder_action_owner import HerderActionOwner
from notification_center.http_api import DeliveryWorker


class _TrustedTestAuthority:
    """Test-only injected issuer; production has no configured issuer today."""

    def __init__(self) -> None:
        self.actors = {"telegram:42"}
        self.timeout_enabled = True

    def __call__(self, mandate: dict[str, object]) -> dict[str, object]:
        return mandate

    def is_actor_allowed(self, _context: dict[str, object], actor: str) -> bool:
        return actor in self.actors

    def timeout_allowed_now(self, _context: dict[str, object]) -> bool:
        return self.timeout_enabled


def _mandate(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "task_ref": "board/task-17", "purpose": "choose-next-step", "version": "v3",
        "mandate_ref": "grant:task-17:v3", "project": "airlock", "recipient": "operator",
        "message": "Choose the permitted next step.",
        "allowed_actors": ["telegram:42"],
        "choices": [{"label": "Continue", "value": "continue"}, {"label": "Stop", "value": "stop"}],
        "timeout_value": "continue", "continuation": {
            "task_ref": "board/task-17", "operation_id": "grant:task-17:v3", "board_version": 23,
            "started_by": "nikita", "host_id": "roomhacker-server-100", "harness": "codex",
            "session_id": "session-123", "turn_id": "turn-456",
        },
    }
    value.update(changes)
    if "continuation" not in changes:
        continuation = dict(value["continuation"])
        continuation["task_ref"] = value["task_ref"]
        continuation["operation_id"] = value["mandate_ref"]
        value["continuation"] = continuation
    return value


class _FakeHerder:
    """One bounded stand-in for the existing Herder session-control API."""

    def __init__(
        self,
        *,
        local_host_id: str = "roomhacker-server-100",
        stop_status: int = 200,
        stop_body: Any = None,
        session_status: str = "stopped",
        human_stop_held: bool = True,
        active_turn_id: str = "turn-456",
        unreachable: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []
        self._local_host_id = local_host_id
        self._stop_status = stop_status
        self._stop_body = stop_body if stop_body is not None else {"ok": True}
        self._session_status = session_status
        self._human_stop_held = human_stop_held
        self._active_turn_id = active_turn_id
        self._unreachable = unreachable

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Any = None,
        payload: Any = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path, dict(query or {}), dict(payload or {})))
        if self._unreachable:
            from notification_center.herder_action_owner import HerderUnreachableError

            raise HerderUnreachableError("herder request failed: connection refused")
        if path == "/api/fleet/hosts":
            return 200, {"hosts": [], "defaultHostId": self._local_host_id}
        if method == "POST" and path.endswith("/stop"):
            return self._stop_status, self._stop_body
        if method == "POST" and path.endswith("/cancel"):
            return self._stop_status, self._stop_body
        if method == "GET" and path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            meta: dict[str, Any] = {"humanStopHeld": self._human_stop_held}
            if self._active_turn_id:
                meta["activeTurnId"] = self._active_turn_id
            return 200, {"session": {
                "id": session_id, "harness": "codex", "status": self._session_status, "meta": meta,
            }}
        raise AssertionError(f"unexpected herder call: {method} {path}")

    def mutations(self) -> list[tuple[str, str, dict[str, str], dict[str, Any]]]:
        return [call for call in self.calls if call[0] == "POST"]


class HerderActionOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "notice.sqlite3"
        self.state = Path(self.tempdir.name) / "action-owner-receipts.json"
        self.center = NotificationCenter(
            self.path, {"token": {"project": "airlock", "max_severity": "critical"}},
            task_decision_verifier=_TrustedTestAuthority(),
        )

    def tearDown(self) -> None:
        self.center._connection.close()
        self.tempdir.cleanup()

    def create(self, **changes: object) -> dict[str, object]:
        return self.center.create_task_decision_request("token", _mandate(**changes))

    def owner(self, herder: _FakeHerder) -> HerderActionOwner:
        return HerderActionOwner("http://127.0.0.1:18787", state_path=self.state, transport=herder)

    def test_verified_stop_reaches_the_bound_session_and_acknowledges_the_epoch(self) -> None:
        request = self.create(task_ref="board/task-owner-stop")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")
        command = self.center.task_decisions.control_command(request_id)
        herder = _FakeHerder()
        owner = self.owner(herder)

        DeliveryWorker(self.center, object(), task_action_owner=owner).run_once()

        state = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("acknowledged", state["control_state"])
        self.assertEqual(command["epoch"], state["control_ack_epoch"])
        self.assertEqual(command["epoch"], state["control_epoch"])
        self.assertTrue(state["control_ack_ref"].startswith("herder:"))
        mutations = herder.mutations()
        self.assertEqual(1, len(mutations))
        method, path, _query, payload = mutations[0]
        self.assertEqual("POST", method)
        self.assertEqual("/api/sessions/codex/session-123/stop", path)
        self.assertEqual({"humanRequested": True}, payload)
        effects = state["control_effects"]
        self.assertIn("native_session_stopped", effects["accepted"])
        self.assertIn("native_turn_bound", effects["accepted"])
        self.assertEqual([], effects["unknown"])

    def test_missing_configuration_keeps_the_control_path_closed(self) -> None:
        request = self.create(task_ref="board/task-owner-unconfigured")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")

        self.assertIsNone(HerderActionOwner.from_environment({}))
        herder = _FakeHerder()
        DeliveryWorker(self.center, object(), task_action_owner=None).run_once()
        state = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("pending", state["control_state"])
        self.assertEqual([], herder.calls)

    def test_executor_failure_never_acknowledges_and_never_repeats_blindly(self) -> None:
        request = self.create(task_ref="board/task-owner-failing")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")

        herder = _FakeHerder(stop_status=500, stop_body={"ok": False, "error": "boom"})
        owner = self.owner(herder)
        DeliveryWorker(self.center, object(), task_action_owner=owner).run_once()

        state = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("unknown", state["control_state"])
        self.assertEqual(1, len(herder.mutations()))
        # The owner answered with an unknown outcome, so the worker never repeats
        # the revocation on its own.
        query = owner.query_control(str(state["control_id"]))
        self.assertEqual("unknown", query["status"])

    def test_repeated_control_with_the_same_id_executes_once(self) -> None:
        request = self.create(task_ref="board/task-owner-idempotent")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")
        command = self.center.task_decisions.control_command(request_id)
        herder = _FakeHerder()
        owner = self.owner(herder)

        first = owner.revoke(command, str(command["control_id"]))
        second = owner.revoke(command, str(command["control_id"]))

        self.assertEqual("acknowledged", first["status"])
        self.assertEqual(command["epoch"], first["effective_epoch"])
        self.assertEqual(command["execution"], first["execution"])
        self.assertEqual(first, second)
        self.assertEqual(1, len(herder.mutations()))

    def test_restart_reconciles_the_receipt_without_a_second_execution(self) -> None:
        request = self.create(task_ref="board/task-owner-restart")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")
        command = self.center.task_decisions.control_command(request_id)
        herder = _FakeHerder()
        owner = self.owner(herder)
        owner.revoke(command, str(command["control_id"]))
        self.assertEqual(1, len(herder.mutations()))

        # A fresh process claims the same control and finds the durable receipt.
        self.center.task_decisions.claim_control(request_id, now_epoch=100.0, lease_seconds=10)
        restarted = HerderActionOwner("http://127.0.0.1:18787", state_path=self.state, transport=herder)
        DeliveryWorker(self.center, object(), task_action_owner=restarted).run_once()

        state = self.center.get_human_request("token", request_id)["task_decision"]
        self.assertEqual("acknowledged", state["control_state"])
        self.assertEqual(1, len(herder.mutations()))

    def test_unreachable_executor_allows_one_safe_retry_with_the_same_id(self) -> None:
        request = self.create(task_ref="board/task-owner-unreachable")
        request_id = str(request["request_id"])
        self.center.task_decisions.request_control(request_id, "telegram:42", "stop", "stop the task")
        command = self.center.task_decisions.control_command(request_id)
        herder = _FakeHerder(unreachable=True)
        owner = self.owner(herder)

        with self.assertRaises(Exception):
            owner.revoke(command, str(command["control_id"]))
        query = owner.query_control(str(command["control_id"]))
        self.assertEqual("not_found", query["status"])
        self.assertIs(True, query["retry_safe"])
        self.assertEqual(command["execution"], query["execution"])


if __name__ == "__main__":
    unittest.main()