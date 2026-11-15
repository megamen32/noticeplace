"""Action owner that revokes a verified task execution through the Agent Herder.

The task-decision verifier binds every task question to one immutable execution:
the Board operation, its board version, the owner, the host, the harness, the
native session and the native turn.  The durable ``stop``/``hold`` control that
an authenticated operator requests must reach exactly that execution.

The Agent Herder already owns those native sessions and already exposes the
session-control API that the Board uses to dispatch and steer the very same
session, so this owner reuses that existing channel instead of adding a second
one.  The Notice service already talks to the same Herder loopback for its
bound human-request replay.

The owner keeps a small durable receipt store.  It lets ``query_control`` echo
the exact execution binding after a restart, and it makes a repeated control
with the same stable control ID return the first receipt instead of executing
the revocation twice.

Nothing here pretends a delivery: a control is acknowledged only after the
executor confirmed the operation and the bound native session was read back.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


OWNER_ID = "herder-action-owner"
DEFAULT_BASE_URL = "http://127.0.0.1:18787"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RECEIPTS = 512
EXECUTION_FIELDS = (
    "task_ref", "operation_id", "started_by", "board_version",
    "host_id", "harness", "session_id", "turn_id",
)
# A native session that is no longer running can no longer advance the task.
INACTIVE_STATUSES = frozenset({"stopped", "idle", "error", "terminated", "cancelled"})


class HerderControlError(RuntimeError):
    """A bounded session-control failure without response or credential data."""


class HerderUnreachableError(HerderControlError):
    """The request never reached the executor, so nothing was executed."""


class _JsonTransport:
    """One bounded JSON call to the Herder session-control API."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._base_url = str(base_url or "").strip().rstrip("/")
        self._token = str(token or "").strip()
        self._timeout = max(1.0, float(timeout_seconds))
        self._opener = opener or urllib.request.urlopen

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[int, Any]:
        url = self._base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = int(error.code)
            raw = error.read()
        except urllib.error.URLError as error:
            # The connection was never established, so the executor cannot have
            # received the command and a retry with the same ID stays safe.
            raise HerderUnreachableError(f"herder request failed: {error}") from error
        except (TimeoutError, OSError) as error:
            # A lost reply after delivery leaves the outcome unknown.
            raise HerderControlError(f"herder request failed: {error}") from error
        if not raw:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HerderControlError("herder returned invalid JSON") from error


class HerderActionOwner:
    """Revoke one verified native task execution through the Herder."""

    owner_id = OWNER_ID

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        token: str = "",
        state_path: str | os.PathLike[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        local_host_id: str = "",
        transport: Any = None,
    ) -> None:
        self._base_url = str(base_url or "").strip().rstrip("/")
        if not self._base_url:
            raise HerderControlError("action owner needs a Herder base URL")
        self._token = str(token or "").strip()
        self._state_path = Path(state_path) if state_path is not None else None
        self._local_host_id = str(local_host_id or "").strip()
        self._transport = transport if transport is not None else _JsonTransport(
            self._base_url, self._token, timeout_seconds
        )
        self._lock = threading.RLock()
        self._resolved_local_host: str | None = self._local_host_id or None

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "HerderActionOwner | None":
        """Build the production owner; without a Herder URL the path stays closed."""
        source = os.environ if env is None else env
        base_url = str(source.get("NOTIFY_ACTION_OWNER_HERDER_URL", "") or "").strip()
        if not base_url:
            return None
        try:
            timeout = float(source.get("NOTIFY_ACTION_OWNER_HERDER_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return cls(
            base_url,
            token=str(source.get("NOTIFY_ACTION_OWNER_HERDER_TOKEN", "") or ""),
            state_path=cls.state_path_from_environment(source),
            timeout_seconds=timeout,
            local_host_id=str(source.get("NOTIFY_ACTION_OWNER_LOCAL_HOST_ID", "") or ""),
        )

    @staticmethod
    def state_path_from_environment(env: Mapping[str, str]) -> Path:
        """Resolve the durable receipt store without guessing a writable directory."""
        explicit = str(env.get("NOTIFY_ACTION_OWNER_STATE_PATH", "") or "").strip()
        if explicit:
            return Path(explicit)
        directory = str(env.get("NOTIFY_CENTER_STATE_DIR", "") or "").strip()
        if directory:
            return Path(directory) / "action-owner-receipts.json"
        system = Path("/var/lib/notification-center")
        if system.is_dir():
            return system / "action-owner-receipts.json"
        return Path("./var/action-owner-receipts.json")

    # Durable receipt store -------------------------------------------------

    def _receipts(self) -> dict[str, Any]:
        if self._state_path is None:
            return {}
        try:
            raw = self._state_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        receipts = payload.get("receipts") if isinstance(payload, Mapping) else None
        return dict(receipts) if isinstance(receipts, Mapping) else {}

    def _store_receipt(self, receipt: Mapping[str, Any]) -> None:
        if self._state_path is None:
            raise HerderControlError("action owner receipt store is not configured")
        with self._lock:
            receipts = self._receipts()
            receipts[str(receipt["control_id"])] = dict(receipt)
            if len(receipts) > MAX_RECEIPTS:
                ordered = sorted(receipts, key=lambda key: float(receipts[key].get("at") or 0.0))
                for key in ordered[: len(receipts) - MAX_RECEIPTS]:
                    receipts.pop(key, None)
            payload = json.dumps({"version": 1, "receipts": receipts}, ensure_ascii=False, sort_keys=True, indent=1)
            try:
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self._state_path.with_name(self._state_path.name + ".tmp")
                temp.write_text(payload, encoding="utf-8")
                os.replace(temp, self._state_path)
            except OSError as error:
                raise HerderControlError(f"action owner receipt store is not writable: {error}") from error

    # Worker contract ------------------------------------------------------

    def query_control(self, control_id: str) -> dict[str, Any]:
        """Answer the worker's recovery question from the durable receipt store."""
        key = str(control_id or "").strip()
        if not key:
            return {"status": "unknown"}
        receipt = self._receipts().get(key)
        if not isinstance(receipt, Mapping):
            # The executor keeps no control-ID registry, so the absence of a
            # durable receipt cannot prove that nothing was executed.
            return {"status": "unknown", "control_id": key}
        status = str(receipt.get("status") or "unknown")
        if status != "acknowledged":
            answer = {
                "status": status,
                "control_id": key,
                "execution": receipt.get("execution"),
                "effective_epoch": receipt.get("effective_epoch"),
                "receipt_ref": receipt.get("receipt_ref"),
            }
            if status == "not_found":
                # Only the executor-unreachable case is provably unexecuted.
                answer["retry_safe"] = receipt.get("retry_safe") is True
            return answer
        contradiction = self._contradicted(receipt)
        if contradiction:
            return {
                "status": "unknown",
                "control_id": key,
                "execution": receipt.get("execution"),
                "effective_epoch": receipt.get("effective_epoch"),
                "unknown_effects": [contradiction],
            }
        return self._receipt(receipt)

    def revoke(self, command: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        """Execute one durable stop or hold against the bound native session."""
        execution = self._execution(command)
        control_id = str(idempotency_key or "").strip()
        epoch = command.get("epoch")
        kind = str(command.get("kind") or "").strip()
        if not control_id:
            raise HerderControlError("task control command has no stable control ID")
        if type(epoch) is not int:
            raise HerderControlError("task control command has no integer epoch")
        if kind not in {"stop", "hold"}:
            raise HerderControlError("task control kind must be stop or hold")
        with self._lock:
            existing = self._receipts().get(control_id)
        if isinstance(existing, Mapping) and existing.get("status") == "acknowledged":
            # Same stable control ID: the revocation already happened once.
            return self._receipt(existing)
        intent = {
            "control_id": control_id,
            "status": "sending",
            "kind": kind,
            "effective_epoch": epoch,
            "execution": execution,
            "accepted_effects": [],
            "unknown_effects": [],
            "at": time.time(),
        }
        # Record the intent before the executor call, so a lost reply stays
        # queryable instead of silently repeating the revocation.
        self._store_receipt(intent)
        try:
            session = self._execute(execution, kind)
        except HerderControlError as error:
            retry_safe = isinstance(error, HerderUnreachableError)
            self._store_receipt({
                **intent,
                "status": "not_found" if retry_safe else "unknown",
                "retry_safe": retry_safe,
                "unknown_effects": [] if retry_safe else ["executor_outcome_unknown"],
                "error": str(error)[:200],
            })
            raise
        accepted, unknown = self._effects(session, execution, kind)
        receipt = {
            "control_id": control_id,
            "status": "acknowledged",
            "kind": kind,
            "effective_epoch": epoch,
            "execution": execution,
            "receipt_ref": self._receipt_ref(execution, kind, control_id),
            "accepted_effects": accepted,
            "unknown_effects": unknown,
            "at": time.time(),
        }
        self._store_receipt(receipt)
        return self._receipt(receipt)

    # Executor channel ------------------------------------------------------

    def _execute(self, execution: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
        """Run the existing Herder session-control operation for the bound session."""
        harness = str(execution["harness"])
        session_id = str(execution["session_id"])
        host_id = str(execution["host_id"])
        if self._is_local(host_id):
            operation = "stop" if kind == "stop" else "cancel"
            status, body = self._transport.request(
                "POST",
                f"/api/sessions/{urllib.parse.quote(harness, safe='')}/"
                f"{urllib.parse.quote(session_id, safe='')}/{operation}",
                payload={"humanRequested": True},
            )
        else:
            if kind != "stop":
                raise HerderControlError("the fleet control channel cannot hold a remote session")
            status, body = self._transport.request(
                "POST",
                "/api/fleet/control",
                query={"hostId": host_id, "harness": harness, "sessionId": session_id},
                payload={"operation": "stop"},
            )
        if status != 200:
            raise HerderControlError(f"herder session control returned HTTP {status}")
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise HerderControlError("herder session control did not confirm the operation")
        return self._observe(execution)

    def _observe(self, execution: Mapping[str, Any]) -> Mapping[str, Any]:
        """Read the bound native session back from the executor."""
        harness = str(execution["harness"])
        session_id = str(execution["session_id"])
        host_id = str(execution["host_id"])
        if self._is_local(host_id):
            status, body = self._transport.request(
                "GET",
                f"/api/sessions/{urllib.parse.quote(harness, safe='')}/"
                f"{urllib.parse.quote(session_id, safe='')}",
            )
        else:
            status, body = self._transport.request(
                "GET",
                "/api/fleet/control",
                query={
                    "hostId": host_id,
                    "harness": harness,
                    "sessionId": session_id,
                    "operation": "details",
                },
            )
        if status != 200:
            raise HerderControlError(f"herder session read returned HTTP {status}")
        session = body.get("session") if isinstance(body, Mapping) else None
        if not isinstance(session, Mapping):
            raise HerderControlError("herder returned no native session view")
        if session.get("id") != session_id or session.get("harness") != harness:
            raise HerderControlError("herder returned a different native session identity")
        return session

    def _contradicted(self, receipt: Mapping[str, Any]) -> str:
        """Report a durable receipt that the executor no longer supports."""
        execution = receipt.get("execution")
        if not isinstance(execution, Mapping):
            return "execution_binding_missing"
        try:
            session = self._observe(execution)
        except HerderControlError:
            # The executor cannot be read right now; the durable receipt stays.
            return ""
        meta = session.get("meta") if isinstance(session.get("meta"), Mapping) else {}
        status = str(session.get("status") or "")
        held = meta.get("humanStopHeld") is True
        if held or status in INACTIVE_STATUSES:
            return ""
        return "executor_session_is_active_again"

    def _effects(
        self,
        session: Mapping[str, Any],
        execution: Mapping[str, Any],
        kind: str,
    ) -> tuple[list[str], list[str]]:
        """Translate the observed executor state into accepted and unknown effects."""
        meta = session.get("meta") if isinstance(session.get("meta"), Mapping) else {}
        status = str(session.get("status") or "")
        held = meta.get("humanStopHeld") is True
        accepted: list[str] = []
        unknown: list[str] = []
        if kind == "stop":
            if held or status in INACTIVE_STATUSES:
                accepted.append("native_session_stopped")
            else:
                raise HerderControlError("the bound native session is still running after the stop")
        else:
            if held:
                accepted.append("native_session_held")
            elif status in INACTIVE_STATUSES:
                accepted.append("native_session_held")
                accepted.append("native_turn_cancelled")
            else:
                raise HerderControlError("the bound native session is not held after the hold")
        observed_turn = meta.get("activeTurnId") or meta.get("nativeTurnId")
        if isinstance(observed_turn, str) and observed_turn.strip():
            if observed_turn != str(execution["turn_id"]):
                raise HerderControlError("the bound native turn is no longer the current turn")
            accepted.append("native_turn_bound")
        else:
            unknown.append("native_turn_not_verifiable")
        return accepted, unknown

    # Helpers ---------------------------------------------------------------

    def _is_local(self, host_id: str) -> bool:
        local = self._resolve_local_host()
        return bool(local) and host_id == local

    def _resolve_local_host(self) -> str:
        with self._lock:
            if self._resolved_local_host:
                return self._resolved_local_host
        try:
            status, body = self._transport.request("GET", "/api/fleet/hosts")
        except HerderControlError:
            return ""
        if status != 200 or not isinstance(body, Mapping):
            return ""
        local = str(body.get("defaultHostId") or "").strip()
        if not local:
            return ""
        with self._lock:
            self._resolved_local_host = local
        return local

    @staticmethod
    def _execution(command: Mapping[str, Any]) -> dict[str, Any]:
        raw = command.get("execution")
        if not isinstance(raw, Mapping):
            raise HerderControlError("task control command has no execution binding")
        execution = {key: raw.get(key) for key in EXECUTION_FIELDS}
        if type(execution["board_version"]) is not int:
            raise HerderControlError("task control execution binding has no integer board version")
        for key in EXECUTION_FIELDS:
            if key == "board_version":
                continue
            value = execution[key]
            if not isinstance(value, str) or not value.strip():
                raise HerderControlError(f"task control execution binding has no {key}")
        return execution

    @staticmethod
    def _receipt_ref(execution: Mapping[str, Any], kind: str, control_id: str) -> str:
        return (
            f"herder:{execution['host_id']}/{execution['harness']}/{execution['session_id']}"
            f"/{kind}@{control_id[:16]}"
        )

    @staticmethod
    def _receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": str(receipt.get("status") or "unknown"),
            "control_id": str(receipt.get("control_id") or ""),
            "effective_epoch": receipt.get("effective_epoch"),
            "execution": receipt.get("execution"),
            "receipt_ref": receipt.get("receipt_ref"),
            "accepted_effects": list(receipt.get("accepted_effects") or []),
            "unknown_effects": list(receipt.get("unknown_effects") or []),
        }
