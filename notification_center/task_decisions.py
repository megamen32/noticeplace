"""Task-scoped questions layered on Notice's canonical human request record.

This is a state-transition helper, not another question store or scheduler.
The existing ``human_requests`` row owns the question; DeliveryWorker calls
the due/notification-control methods on its normal tick.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping


class TaskDecisionStore:
    DEADLINE_SECONDS = 30 * 60

    def __init__(self, center: Any) -> None:
        self._center = center

    def create_request(self, token: str, mandate: Mapping[str, Any]) -> dict[str, Any]:
        verifier = self._center._task_decision_verifier
        if verifier is None:
            from .core import AuthorizationError
            raise AuthorizationError("verified task mandate unavailable")
        verified = verifier(mandate) if callable(verifier) else verifier.verify(mandate)
        if not isinstance(verified, Mapping):
            from .core import AuthorizationError
            raise AuthorizationError("verified task mandate unavailable")
        required = ("task_ref", "purpose", "version", "mandate_ref", "project", "recipient",
                    "message", "allowed_actors", "choices", "timeout_value", "continuation")
        if any(not str(verified.get(key) or "").strip() for key in required):
            from .core import ValidationError
            raise ValidationError("verified task mandate is incomplete")
        identity = [str(verified["task_ref"]).strip(), str(verified["purpose"]).strip(), str(verified["version"]).strip()]
        if any(len(value) > limit for value, limit in zip(identity, (256, 128, 128))):
            from .core import ValidationError
            raise ValidationError("task decision identity exceeds bounds")
        if not isinstance(verified["continuation"], Mapping):
            from .core import ValidationError
            raise ValidationError("verified task continuation must be structured")
        choices = verified["choices"]
        actors = verified["allowed_actors"]
        if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
            from .core import ValidationError
            raise ValidationError("task decision requires two to four allowed choices")
        values = {str(choice.get("value") or "") for choice in choices if isinstance(choice, Mapping)}
        if len(values) != len(choices) or str(verified["timeout_value"]) not in values:
            from .core import ValidationError
            raise ValidationError("task timeout continuation must be an immutable allowed choice")
        if (not isinstance(actors, list) or not actors
                or any(not isinstance(actor, str) or not actor.strip() or len(actor) > 128 for actor in actors)
                or len(set(actors)) != len(actors)):
            from .core import ValidationError
            raise ValidationError("verified task actor policy is invalid")
        canonical = {key: verified[key] for key in required}
        canonical["task_ref"], canonical["purpose"], canonical["version"] = identity
        canonical["allowed_actors"] = sorted(str(actor).strip() for actor in actors)
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16_384 or len(str(verified["mandate_ref"])) > 512:
            from .core import ValidationError
            raise ValidationError("verified task mandate exceeds bounds")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        request_id = "td-" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:40]
        context = {
            "task_ref": identity[0], "purpose": identity[1], "version": identity[2],
            "mandate_ref": str(verified["mandate_ref"]).strip(),
            "continuation": dict(verified["continuation"]),
            "timeout_value": str(verified["timeout_value"]),
            "allowed_actors": canonical["allowed_actors"],
        }
        request = {
            "schema": "ask_human.request.v1", "request_id": request_id,
            "project": str(verified["project"]).strip(), "recipient": str(verified["recipient"]).strip(),
            "mode": "choice", "message": str(verified["message"]), "choices": choices,
            "allowed_actors": canonical["allowed_actors"], "expires_at": None,
        }
        return self._center.create_human_request(
            token, request, _task_context=context, _task_input_sha256=digest,
            _task_writer=self._center._task_decision_writer,
        )

    def _row(self, request_id: str) -> Any:
        row = self._center._connection.execute(
            "SELECT * FROM human_requests WHERE request_id=?", (str(request_id),)
        ).fetchone()
        if row is None or not row["task_context_json"]:
            from .core import ValidationError
            raise ValidationError("task decision not found")
        return row

    def actor_allowed_now(self, request_id: str, actor: str) -> bool:
        row = self._row(request_id)
        context = json.loads(str(row["task_context_json"]))
        verifier = self._center._task_decision_verifier
        check = getattr(verifier, "is_actor_allowed", None)
        return bool(callable(check) and check(context, actor) is True)

    @staticmethod
    def control_gate_open(row: Any) -> bool:
        if row["task_control_state"] == "clear":
            return True
        return bool(row["task_control_state"] == "acknowledged"
                    and row["task_control_kind"] == "hold" and row["task_resume_ref"])

    def reserve_notification(self, request_id: str) -> bool:
        with self._center._lock, self._center._connection:
            changed = self._center._connection.execute(
                "UPDATE human_requests SET task_notification_state='sending' "
                "WHERE request_id=? AND task_context_json IS NOT NULL AND state='pending' "
                "AND task_notification_state='queued'",
                (request_id,),
            ).rowcount
            return changed == 1

    def notification_state(self, request_id: str) -> dict[str, Any]:
        with self._center._lock:
            row = self._row(request_id)
            return {"state": str(row["task_notification_state"]), "notified_at": row["task_notified_at"],
                    "deadline_at": row["task_deadline_at"]}

    def mark_notification_unknown(self, request_id: str) -> None:
        with self._center._lock, self._center._connection:
            self._center._connection.execute(
                "UPDATE human_requests SET task_notification_state='unknown' "
                "WHERE request_id=? AND task_context_json IS NOT NULL AND task_notification_state='sending' "
                "AND task_notified_at IS NULL",
                (request_id,),
            )

    def record_notification_success(self, request_id: str, observed_at: float | None = None) -> dict[str, Any]:
        """Arm the one deadline only when the existing sender has a success receipt."""
        now = time.time() if observed_at is None else float(observed_at)
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            if row["task_notified_at"] is not None:
                return self._center._human_request_result(row)
            if row["task_notification_state"] != "sending":
                from .core import ValidationError
                raise ValidationError("task question has no reserved notification attempt")
            self._center._connection.execute(
                "UPDATE human_requests SET task_notification_state='sent', task_notified_at=?, task_deadline_at=? "
                "WHERE request_id=? AND task_context_json IS NOT NULL AND task_notification_state='sending' "
                "AND task_notified_at IS NULL AND state='pending'",
                (now, now + self.DEADLINE_SECONDS, request_id),
            )
            current = self._row(request_id)
            if current["task_notified_at"] is None:
                from .core import ValidationError
                raise ValidationError("task notification receipt was not committed")
            self._center._audit(None, "task_decision.notification_succeeded", "notice", {
                "request_id": request_id, "notified_at": float(current["task_notified_at"]),
                "deadline_at": float(current["task_deadline_at"]),
            })
            return self._center._human_request_result(current)

    def resolve_due(self, now_epoch: float | None = None, limit: int = 50) -> int:
        """Commit timeout choices with one SQL CAS; never write a human response."""
        now = time.time() if now_epoch is None else float(now_epoch)
        resolved = 0
        with self._center._lock, self._center._connection:
            rows = self._center._connection.execute(
                "SELECT request_id, task_context_json, task_input_sha256, task_deadline_at "
                "FROM human_requests WHERE task_context_json IS NOT NULL AND state='pending' "
                "AND task_notification_state='sent' AND task_deadline_at<=? "
                "AND task_decision_source IS NULL AND task_control_state='clear' "
                "ORDER BY task_deadline_at LIMIT ?", (now, max(1, min(200, int(limit)))),
            ).fetchall()
            for row in rows:
                context = json.loads(str(row["task_context_json"]))
                verifier = self._center._task_decision_verifier
                timeout_allowed = getattr(verifier, "timeout_allowed_now", None)
                if not callable(timeout_allowed) or timeout_allowed(context) is not True:
                    continue
                evidence = json.dumps({"source": "deadline", "input_sha256": row["task_input_sha256"],
                                       "deadline_at": row["task_deadline_at"], "evaluated_at": now},
                                      sort_keys=True, separators=(",", ":"))
                changed = self._center._connection.execute(
                    "UPDATE human_requests SET state='expired', task_decision_source='timeout', "
                    "task_decision_value=?, task_decision_actor='notice-timeout', task_decided_at=?, "
                    "task_decision_evidence_json=? WHERE request_id=? AND state='pending' "
                    "AND task_notification_state='sent' AND task_deadline_at<=? "
                    "AND task_decision_source IS NULL AND task_control_state='clear'",
                    (str(context["timeout_value"]), now, evidence, row["request_id"], now),
                ).rowcount
                if changed:
                    self._center._audit(None, "task_decision.timeout_committed", "notice-timeout", {
                        "request_id": row["request_id"], "value": str(context["timeout_value"]),
                        "deadline_at": row["task_deadline_at"],
                    })
                    resolved += 1
        return resolved

    def request_control(self, request_id: str, actor: str, kind: str, reason: str = "") -> dict[str, Any]:
        if kind not in {"stop", "hold"} or not str(actor).strip():
            from .core import ValidationError
            raise ValidationError("task control requires an authenticated stop or hold")
        reason = " ".join(str(reason).split())[:500]
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            if actor not in json.loads(str(row["allowed_actors_json"])):
                from .core import ValidationError
                raise ValidationError("human response actor is not allowed")
            if not self.actor_allowed_now(request_id, actor):
                from .core import AuthorizationError
                raise AuthorizationError("current task actor policy does not allow this decision")
            current_state = str(row["task_control_state"])
            if current_state == "acknowledged" and str(row["task_control_kind"]) == "stop":
                from .core import ValidationError
                raise ValidationError("an acknowledged stop is terminal for this task decision version")
            if current_state in {"pending", "sending", "unknown"}:
                if str(row["task_control_kind"]) == kind:
                    return self._center._human_request_result(row)
                from .core import ValidationError
                raise ValidationError("another task control is awaiting action-owner acknowledgment")
            epoch = int(row["task_control_epoch"] or 0) + 1
            control_id = hashlib.sha256(
                f"{request_id}:{epoch}:{kind}:{actor}:{reason}".encode("utf-8")
            ).hexdigest()
            now = time.time()
            changed = self._center._connection.execute(
                "UPDATE human_requests SET task_control_state='pending', task_control_kind=?, task_control_id=?, "
                "task_control_epoch=?, task_control_requested_at=?, task_control_lease_until=NULL, task_control_ack_ref=NULL, "
                "task_control_ack_epoch=NULL, task_control_ack_at=NULL, task_control_effects_json='[]', "
                "task_resume_ref=NULL, task_resumed_at=NULL WHERE request_id=? AND task_context_json IS NOT NULL "
                "AND task_control_epoch=? AND task_control_state IN ('clear','acknowledged')",
                (kind, control_id, epoch, now, request_id, epoch - 1),
            ).rowcount
            if changed != 1:
                from .core import ValidationError
                raise ValidationError("task control changed concurrently")
            self._center._audit(None, "task_decision.control_requested", actor, {
                "request_id": request_id, "control_id": control_id, "kind": kind,
                "epoch": epoch, "reason": reason,
            })
            return self._center._human_request_result(self._row(request_id))

    def claim_control(self, request_id: str, *, now_epoch: float | None = None, lease_seconds: float = 30) -> dict[str, Any] | None:
        now = time.time() if now_epoch is None else float(now_epoch)
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            state = str(row["task_control_state"])
            if state == "pending":
                changed = self._center._connection.execute(
                    "UPDATE human_requests SET task_control_state='sending', task_control_lease_until=? "
                    "WHERE request_id=? AND task_control_state='pending' AND task_context_json IS NOT NULL",
                    (now + max(1.0, float(lease_seconds)), request_id),
                ).rowcount
            elif state in {"sending", "unknown"} and float(row["task_control_lease_until"] or 0) <= now:
                changed = self._center._connection.execute(
                    "UPDATE human_requests SET task_control_state='sending', task_control_lease_until=? "
                    "WHERE request_id=? AND task_control_state=? AND COALESCE(task_control_lease_until,0)<=? "
                    "AND task_context_json IS NOT NULL",
                    (now + max(1.0, float(lease_seconds)), request_id, state, now),
                ).rowcount
            else:
                changed = 0
            if not changed:
                return None
            return self._center._human_request_result(self._row(request_id))

    def pending_controls(self, limit: int = 20, now_epoch: float | None = None) -> list[str]:
        now = time.time() if now_epoch is None else float(now_epoch)
        with self._center._lock:
            rows = self._center._connection.execute(
                "SELECT request_id FROM human_requests WHERE task_context_json IS NOT NULL "
                "AND (task_control_state='pending' OR (task_control_state IN ('sending','unknown') "
                "AND COALESCE(task_control_lease_until,0)<=?)) ORDER BY task_control_requested_at LIMIT ?",
                (now, max(1, min(100, int(limit)))),
            ).fetchall()
            return [str(row["request_id"]) for row in rows]

    def control_command(self, request_id: str) -> dict[str, Any]:
        """Return the bounded durable intent for the existing worker adapter."""
        with self._center._lock:
            row = self._row(request_id)
            context = json.loads(str(row["task_context_json"]))
            raw_execution = context.get("continuation") if isinstance(context, Mapping) else None
            required_text = ("task_ref", "operation_id", "started_by", "host_id", "harness", "session_id", "turn_id")
            if (not isinstance(raw_execution, Mapping)
                    or type(raw_execution.get("board_version")) is not int
                    or raw_execution["board_version"] < 0
                    or any(not isinstance(raw_execution.get(key), str) or not raw_execution[key].strip()
                           for key in required_text)):
                from .core import ValidationError
                raise ValidationError("task control requires a complete verified execution binding")
            execution_fields = (
                "task_ref", "operation_id", "started_by", "board_version",
                "host_id", "harness", "session_id", "turn_id",
            )
            execution = {key: raw_execution[key] for key in execution_fields}
            if (execution["task_ref"] != context.get("task_ref")
                    or execution["operation_id"] != context.get("mandate_ref")
                    or execution["harness"] != "codex"):
                from .core import ValidationError
                raise ValidationError("task control execution binding does not match its mandate")
            return {
                "request_id": request_id, "task_ref": context["task_ref"],
                "mandate_ref": context["mandate_ref"], "control_id": str(row["task_control_id"]),
                "kind": str(row["task_control_kind"]), "epoch": int(row["task_control_epoch"]),
                "state": str(row["task_control_state"]), "execution": execution,
                "lease_until": row["task_control_lease_until"],
            }

    def mark_control_unknown(self, request_id: str) -> None:
        with self._center._lock, self._center._connection:
            self._center._connection.execute(
                "UPDATE human_requests SET task_control_state='unknown', task_control_lease_until=? WHERE request_id=? "
                "AND task_control_state='sending' AND task_context_json IS NOT NULL",
                (time.time() + 30, request_id),
            )

    def acknowledge_control(
        self, request_id: str, *, control_id: str, action_owner: str, receipt_ref: str,
        effective_epoch: int, accepted_effects: list[str] | None = None,
        unknown_effects: list[str] | None = None,
    ) -> dict[str, Any]:
        if not action_owner.strip() or not receipt_ref.strip() or len(receipt_ref) > 512:
            from .core import ValidationError
            raise ValidationError("action-owner acknowledgment requires a bounded receipt")
        raw_effects = {"accepted": list(accepted_effects or []), "unknown": list(unknown_effects or [])}
        if any(not isinstance(item, str) or not item or len(item) > 256 for group in raw_effects.values() for item in group):
            from .core import ValidationError
            raise ValidationError("task effect references must be bounded")
        effects = {key: sorted(set(items)) for key, items in raw_effects.items()}
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            if str(row["task_control_id"] or "") != control_id:
                from .core import ValidationError
                raise ValidationError("action-owner acknowledgment does not match the current control")
            if int(row["task_control_epoch"] or 0) != int(effective_epoch):
                from .core import ValidationError
                raise ValidationError("action-owner acknowledgment epoch does not match")
            if row["task_control_state"] == "acknowledged":
                if row["task_control_ack_ref"] == receipt_ref:
                    return self._center._human_request_result(row)
                from .core import ValidationError
                raise ValidationError("task control already has a different acknowledgment")
            changed = self._center._connection.execute(
                "UPDATE human_requests SET task_control_state='acknowledged', task_control_lease_until=NULL, task_control_ack_ref=?, "
                "task_control_ack_epoch=?, task_control_ack_at=?, task_control_effects_json=? "
                "WHERE request_id=? AND task_control_id=? AND task_control_epoch=? "
                "AND task_control_state IN ('sending','unknown','pending')",
                (receipt_ref[:512], effective_epoch, time.time(), json.dumps(effects, sort_keys=True),
                 request_id, control_id, effective_epoch),
            ).rowcount
            if changed != 1:
                from .core import ValidationError
                raise ValidationError("task control changed before acknowledgment")
            self._center._audit(None, "task_decision.control_acknowledged", action_owner[:128], {
                "request_id": request_id, "control_id": control_id, "epoch": effective_epoch,
                "receipt_ref": receipt_ref[:512], "effects": effects,
            })
            return self._center._human_request_result(self._row(request_id))

    def resume_after_hold(self, request_id: str, actor: str, resume_ref: str) -> dict[str, Any]:
        if not resume_ref.strip() or len(resume_ref) > 512:
            from .core import ValidationError
            raise ValidationError("resume requires a bounded new directive reference")
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            if actor not in json.loads(str(row["allowed_actors_json"])):
                from .core import ValidationError
                raise ValidationError("human response actor is not allowed")
            if not self.actor_allowed_now(request_id, actor):
                from .core import AuthorizationError
                raise AuthorizationError("current task actor policy does not allow resume")
            if row["task_control_kind"] != "hold" or row["task_control_state"] != "acknowledged":
                from .core import ValidationError
                raise ValidationError("hold must be acknowledged before resume")
            if row["task_resume_ref"]:
                if row["task_resume_ref"] == resume_ref:
                    return self._center._human_request_result(row)
                from .core import ValidationError
                raise ValidationError("hold already has a different resume directive")
            self._center._connection.execute(
                "UPDATE human_requests SET task_resume_ref=?, task_resumed_at=? WHERE request_id=? "
                "AND task_control_kind='hold' AND task_control_state='acknowledged' AND task_resume_ref IS NULL",
                (resume_ref[:512], time.time(), request_id),
            )
            return self._center._human_request_result(self._row(request_id))

    def continuation_allowed(self, request_id: str) -> bool:
        with self._center._lock:
            row = self._row(request_id)
            has_decision = row["task_decision_source"] in {"human", "timeout"}
            if not has_decision:
                return False
            if row["task_control_kind"] == "stop":
                return False
            if row["task_control_state"] == "clear":
                return True
            return bool(row["task_control_state"] == "acknowledged"
                        and row["task_control_kind"] == "hold" and row["task_resume_ref"])

    def record_late_instruction(self, request_id: str, actor: str, value: str) -> dict[str, Any]:
        with self._center._lock, self._center._connection:
            row = self._row(request_id)
            if actor not in json.loads(str(row["allowed_actors_json"])):
                from .core import ValidationError
                raise ValidationError("human response actor is not allowed")
            if not self.actor_allowed_now(request_id, actor):
                from .core import AuthorizationError
                raise AuthorizationError("current task actor policy does not allow this instruction")
            choices = json.loads(str(row["choices_json"]))
            if row["mode"] == "choice" and value not in {str(choice["value"]) for choice in choices}:
                from .core import ValidationError
                raise ValidationError("late task instruction is not an allowed choice")
            entries = json.loads(str(row["task_late_instructions_json"] or "[]"))
            item = {"actor": actor, "value": value, "recorded_at": time.time()}
            existing = next((entry for entry in entries if entry.get("actor") == actor and entry.get("value") == value), None)
            if existing is None:
                entries.append(item)
                self._center._connection.execute(
                    "UPDATE human_requests SET task_late_instructions_json=? WHERE request_id=? AND task_context_json IS NOT NULL",
                    (json.dumps(entries, ensure_ascii=False), request_id),
                )
                self._center._audit(None, "task_decision.late_instruction", actor, {
                    "request_id": request_id, "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                })
            return self._center._human_request_result(self._row(request_id))
