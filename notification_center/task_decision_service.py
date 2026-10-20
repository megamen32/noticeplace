"""Production task-mandate verification against the canonical Kanban card.

The project token authenticates the caller to NoticePlace. It does not prove a
current Board task, so this adapter independently reads the manager-only card
and binds a question to its exact active execution receipt/version.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from .core import AuthorizationError, ValidationError


_MAX_RESPONSE_BYTES = 1_000_000
_MAX_MESSAGE_CHARS = 4_000
_ALLOWED_PHASES = frozenset({"running", "waiting"})


def _clean_text(value: Any, *, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"task mandate {field} is invalid")
    result = value.strip()
    if not result or len(result) > limit or any(ord(ch) < 32 and ch not in "\n\t" for ch in result):
        raise ValidationError(f"task mandate {field} is invalid")
    return result


class BoardTaskDecisionVerifier:
    """Verify task questions against current Board ownership and execution.

    The caller supplies question wording and choices, but cannot supply the
    task's project, allowed answer actors, continuation target, or execution
    receipt. Those values are taken from this process's existing operator
    configuration and the current protected Board response.
    """

    def __init__(
        self,
        board_url: str,
        manager_token: str,
        allowed_actors: list[str],
        *,
        operator_recipient: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: float = 3.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(board_url).strip())
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}) or not parsed.netloc:
            raise ValueError("Board task verifier requires HTTPS or loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Board task verifier URL must not contain credentials or query data")
        actors = sorted({str(actor).strip() for actor in allowed_actors if str(actor).strip()})
        recipient = str(operator_recipient).strip()
        if not str(manager_token).strip() or not actors or not recipient or len(recipient) > 256:
            raise ValueError("Board task verifier requires protected access, recipient, and actor allowlist")
        self._board_root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._manager_token = str(manager_token).strip()
        self._allowed_actors = actors
        self._recipient = recipient
        self._opener = opener
        self._timeout = max(0.2, min(10.0, float(timeout_seconds)))

    @classmethod
    def from_environment(cls) -> "BoardTaskDecisionVerifier | None":
        """Use only existing Board and Notice operator configuration; absent refs deny."""
        board_url = os.environ.get("EXMANAGER_KANBAN_URL", "").strip()
        manager_token = os.environ.get("EXMANAGER_KANBAN_TOKEN", "").strip()
        operator_recipient = os.environ.get("NOTIFY_CENTER_RECIPIENT", "").strip()
        raw_actors = ""
        for name in ("ASK_HUMAN_ALLOWED_ACTORS", "NOTIFY_TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_CALLBACK_ALLOWED_USER_IDS"):
            candidate = os.environ.get(name, "").strip()
            if candidate:
                raw_actors = candidate
                break
        actors: list[str] = []
        if "*" not in raw_actors:
            for part in raw_actors.replace(";", ",").split(","):
                actor = part.strip()
                if actor:
                    actors.append(actor if ":" in actor else f"telegram:{actor}")
        actors = sorted(set(actors))
        if not board_url or not manager_token or not operator_recipient or not actors:
            return None
        try:
            return cls(board_url, manager_token, actors, operator_recipient=operator_recipient)
        except (TypeError, ValueError):
            return None

    def _current_execution(
        self, task_ref: str, operation_id: str, version: str | None = None,
        expected_continuation: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        if not task_ref or len(task_ref) > 256 or any(ord(ch) < 32 for ch in task_ref):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", operation_id):
            return None
        path = "/api/kanban/cards/" + urllib.parse.quote(task_ref, safe="")
        request = urllib.request.Request(
            self._board_root + path,
            headers={"Accept": "application/json", "x-kanban-manager-token": self._manager_token},
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return None
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, UnicodeError, ValueError, TypeError):
            return None
        card = payload.get("card") if isinstance(payload, Mapping) else None
        if not isinstance(card, Mapping) or str(card.get("id") or "") != task_ref:
            return None
        current_version = card.get("version")
        if type(current_version) is not int or current_version < 0:
            return None
        if version is not None and str(current_version) != version:
            return None
        project = card.get("project")
        if not isinstance(project, str) or not project.strip():
            return None
        if card.get("needsReview") is True or str(card.get("column") or "") in {"review", "done", "archived", "blocked"}:
            return None
        history = card.get("taskExecutionHistory")
        if not isinstance(history, list) or not history or not isinstance(history[-1], Mapping):
            return None
        receipt = history[-1]
        if (receipt.get("operationId") != operation_id
                or type(receipt.get("lastBoardVersion")) is not int
                or receipt.get("lastBoardVersion") != current_version
                or receipt.get("phase") not in _ALLOWED_PHASES):
            return None
        started_by = receipt.get("startedBy")
        owner = card.get("owner") or "nikita"
        assignees = card.get("assignees") if isinstance(card.get("assignees"), list) else []
        valid_owners = {str(owner)}
        if isinstance(card.get("assignee"), str):
            valid_owners.add(card["assignee"])
        valid_owners.update(value for value in assignees if isinstance(value, str))
        if not isinstance(started_by, str) or started_by not in valid_owners:
            return None
        host_id = receipt.get("hostId")
        session_id = receipt.get("nativeSessionId")
        turn_id = receipt.get("nativeTurnId")
        if (receipt.get("harness") != "codex" or not isinstance(host_id, str) or not host_id.strip()
                or not isinstance(session_id, str) or not session_id.strip()
                or not isinstance(turn_id, str) or not turn_id.strip()):
            return None
        continuation = {
            "task_ref": task_ref,
            "operation_id": operation_id,
            "board_version": current_version,
            "started_by": started_by,
            "host_id": host_id,
            "harness": "codex",
            "session_id": session_id,
            "turn_id": turn_id,
        }
        if expected_continuation is not None and dict(expected_continuation) != continuation:
            return None
        return dict(card), dict(receipt), continuation

    def verify(self, mandate: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(mandate, Mapping):
            raise AuthorizationError("verified task mandate unavailable")
        try:
            task_ref = _clean_text(mandate.get("task_ref"), limit=256, field="task_ref")
            purpose = _clean_text(mandate.get("purpose"), limit=128, field="purpose")
            version = _clean_text(str(mandate.get("version") or ""), limit=128, field="version")
            operation_id = _clean_text(mandate.get("mandate_ref"), limit=128, field="mandate_ref")
            supplied_recipient = _clean_text(mandate.get("recipient"), limit=256, field="recipient")
            message = _clean_text(mandate.get("message"), limit=_MAX_MESSAGE_CHARS, field="message")
            timeout_value = _clean_text(mandate.get("timeout_value"), limit=128, field="timeout_value")
        except ValidationError:
            raise
        except Exception as error:
            raise AuthorizationError("verified task mandate unavailable") from error
        if supplied_recipient != self._recipient:
            raise AuthorizationError("task recipient does not match the configured operator")
        current = self._current_execution(task_ref, operation_id, version)
        if current is None:
            raise AuthorizationError("current Board task execution is not authorized")
        card, receipt, continuation = current
        supplied_project = mandate.get("project")
        if not isinstance(supplied_project, str) or supplied_project.strip() != str(card["project"]).strip():
            raise AuthorizationError("task project no longer matches the current Board card")
        raw_choices = mandate.get("choices")
        if not isinstance(raw_choices, list) or not 2 <= len(raw_choices) <= 4:
            raise ValidationError("task decision requires two to four allowed choices")
        choices: list[dict[str, str]] = []
        for raw in raw_choices:
            if not isinstance(raw, Mapping):
                raise ValidationError("task decision choices must be objects")
            choices.append({
                "value": _clean_text(raw.get("value"), limit=128, field="choice value"),
                "label": _clean_text(raw.get("label"), limit=256, field="choice label"),
            })
        if len({choice["value"] for choice in choices}) != len(choices) or timeout_value not in {choice["value"] for choice in choices}:
            raise ValidationError("task timeout continuation must be one allowed choice")
        return {
            "task_ref": str(card["id"]),
            "purpose": purpose,
            "version": str(card["version"]),
            "mandate_ref": str(receipt["operationId"]),
            "project": str(card["project"]).strip(),
            "recipient": self._recipient,
            "message": message,
            "allowed_actors": list(self._allowed_actors),
            "choices": choices,
            "timeout_value": timeout_value,
            "continuation": continuation,
        }

    def is_actor_allowed(self, context: Mapping[str, Any], actor: str) -> bool:
        if not isinstance(context, Mapping) or actor not in self._allowed_actors:
            return False
        continuation = context.get("continuation")
        if not isinstance(continuation, Mapping):
            return False
        return self._current_execution(
            str(context.get("task_ref") or ""), str(context.get("mandate_ref") or ""),
            str(context.get("version") or ""), continuation,
        ) is not None

    def timeout_allowed_now(self, context: Mapping[str, Any]) -> bool:
        if not isinstance(context, Mapping):
            return False
        continuation = context.get("continuation")
        if not isinstance(continuation, Mapping):
            return False
        return self._current_execution(
            str(context.get("task_ref") or ""), str(context.get("mandate_ref") or ""),
            str(context.get("version") or ""), continuation,
        ) is not None
