"""Read-only projection of canonical Notice Place task decision rows.

This module deliberately does not register an HTTP/MCP/dashboard route. A
consumer must supply its own trusted, current authorization guard before this
projection can be read.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .core import AuthorizationError, ValidationError


AuthorizeTaskDecision = Callable[[Any, str], bool]


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return fallback


class TaskDecisionView:
    """Project a task decision only after a trusted caller guard approves it.

    The guard receives the authenticated principal and opaque request ID. It
    must resolve the current project membership itself and return literal
    ``True``. Client-supplied actor labels are never used as authority.
    """

    def __init__(self, center: Any, authorize: AuthorizeTaskDecision | None = None) -> None:
        self._center = center
        self._authorize = authorize

    def read(self, principal: Any, request_id: str) -> dict[str, Any]:
        authorize = self._authorize
        if not callable(authorize) or authorize(principal, str(request_id)) is not True:
            raise AuthorizationError("current task decision access is not authorized")

        with self._center._lock:
            row = self._center._connection.execute(
                "SELECT * FROM human_requests WHERE request_id=? AND task_context_json IS NOT NULL",
                (str(request_id),),
            ).fetchone()
        if row is None:
            raise ValidationError("task decision not found")

        context = _json_value(row["task_context_json"], {})
        if not isinstance(context, Mapping):
            context = {}
        choices = _json_value(row["choices_json"], [])
        if not isinstance(choices, list):
            choices = []

        notified_at = row["task_notified_at"]
        deadline_at = row["task_deadline_at"]
        notification_state = str(row["task_notification_state"] or "unknown")
        has_receipt = notification_state == "sent" and notified_at is not None
        receipt = None
        if has_receipt:
            receipt = {
                "kind": "successful_sender_response",
                "confirmed_at": float(notified_at),
                "chat_id": str(row["telegram_chat_id"]) if row["telegram_chat_id"] is not None else None,
                "message_id": int(row["telegram_message_id"]) if row["telegram_message_id"] is not None else None,
            }

        source = row["task_decision_source"]
        winner = None
        if source is not None:
            winner = {
                "source": str(source),
                "value": row["task_decision_value"],
                "actor": row["task_decision_actor"],
                "decided_at": float(row["task_decided_at"]) if row["task_decided_at"] is not None else None,
                "evidence": _json_value(row["task_decision_evidence_json"], None),
            }

        control_state = str(row["task_control_state"] or "clear")
        control_epoch = int(row["task_control_epoch"] or 0)
        ack_epoch = int(row["task_control_ack_epoch"]) if row["task_control_ack_epoch"] is not None else None
        ack_ref = row["task_control_ack_ref"]
        control = {
            "kind": row["task_control_kind"],
            "control_id": row["task_control_id"],
            "epoch": control_epoch,
            "state": control_state,
            "requested_at": float(row["task_control_requested_at"])
            if row["task_control_requested_at"] is not None else None,
            "pending": control_state in {"pending", "sending", "unknown"},
            "ack_ref": ack_ref,
            "ack_epoch": ack_epoch,
            "acknowledged_at": float(row["task_control_ack_at"])
            if row["task_control_ack_at"] is not None else None,
            "acknowledgement_matches_current_epoch": bool(
                control_state == "acknowledged" and ack_ref and ack_epoch == control_epoch
            ),
            "effects": _json_value(row["task_control_effects_json"], {}),
            "resume_ref": row["task_resume_ref"],
        }
        late = _json_value(row["task_late_instructions_json"], [])
        if not isinstance(late, list):
            late = []

        return {
            "schema": "noticeplace.task_decision_view.v1",
            "request_id": str(row["request_id"]),
            "project": str(row["project"]),
            "recipient": str(row["recipient"]),
            "status": str(row["state"]),
            "created_at": float(row["created_at"]),
            "question": {
                "text": str(row["message"]),
                "mode": str(row["mode"]),
                "choices": choices,
                "expires_at": float(row["expires_at"]) if row["expires_at"] is not None else None,
                "reference": {
                    "task_ref": context.get("task_ref"),
                    "purpose": context.get("purpose"),
                    "version": context.get("version"),
                    "mandate_ref": context.get("mandate_ref"),
                },
            },
            "notification": {
                "attempt_state": notification_state,
                "receipt": receipt,
                # A countdown is real only after the durable first success receipt.
                "deadline_at": float(deadline_at) if has_receipt and deadline_at is not None else None,
            },
            "winner": winner,
            "control": control,
            "late_instructions": late,
        }
