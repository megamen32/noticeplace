"""Health-incident helpers for the NoticePlace side of the workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any, Mapping

from .core import NotificationCenter, ValidationError

_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TEXT_LIMIT = 128
_SUMMARY_LIMIT = 500
_REF_LIMIT = 128
_RECEIPT_LIMIT = 10
_SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(token|secret|password|credential|authorization|api[-_]?key)\s*(?:=|:)\s*(?:bearer\s+)?\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")

HEALTH_EXECUTION_PROFILE = {
    "runtime": "hermes",
    "provider": "openai-codex",
    "model": "gpt-5.6-luna",
    "reasoning": "high",
    "topic": "health",
}


def sanitize_bounded_text(value: Any, limit: int = _TEXT_LIMIT) -> str:
    """Bound operator receipts and redact assignment/prefix secret forms."""
    text = " ".join(str(value or "").replace("\x00", "").splitlines()).strip()
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    return text[:limit]


def _bounded_text(value: Any, limit: int = _TEXT_LIMIT) -> str:
    return sanitize_bounded_text(value, limit)


def _bounded_summary(value: Any) -> str:
    return _bounded_text(value, _SUMMARY_LIMIT)


def _bounded_refs(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values[:_RECEIPT_LIMIT]:
        ref = _bounded_text(item, _REF_LIMIT)
        if ref:
            result.append(ref)
    return result


def _normalize_orchestration(value: Any) -> dict[str, str]:
    """Keep stage/session/model provenance bounded on the durable plan event."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("health orchestration metadata must be an object")
    fields = (
        "diagnosis_session_id",
        "diagnosis_model",
        "orchestrator_session_id",
        "orchestrator_requested_model",
        "orchestrator_effective_model",
        "harness",
    )
    result = {field: _bounded_text(value.get(field), 128) for field in fields}
    if any(not result[field] for field in fields):
        raise ValidationError("health orchestration metadata is incomplete")
    for field in ("diagnosis_elapsed_ms", "orchestrator_elapsed_ms", "total_elapsed_ms"):
        try:
            result[field] = max(0, min(86_400_000, int(value.get(field) or 0)))
        except (TypeError, ValueError) as error:
            raise ValidationError("health orchestration timing must be a bounded integer") from error
    return result


def _safe_plan_id(plan_id: Any) -> str:
    value = _bounded_text(plan_id)
    if not value or not _PLAN_ID_RE.fullmatch(value):
        raise ValidationError("health plan_id must be a bounded safe identifier")
    return value


def normalize_health_execution(value: Any) -> dict[str, str]:
    """Normalize the one approved health-remediation execution profile."""
    if value is None:
        return dict(HEALTH_EXECUTION_PROFILE)
    if not isinstance(value, Mapping):
        raise ValidationError("health execution profile must be an object")

    runtime = _bounded_text(value.get("runtime") or HEALTH_EXECUTION_PROFILE["runtime"], 32).lower()
    provider = _bounded_text(value.get("provider") or HEALTH_EXECUTION_PROFILE["provider"], 64).lower()
    provider = {"openai": "openai-codex", "codex": "openai-codex", "openai/codex": "openai-codex"}.get(provider, provider)
    model = _bounded_text(value.get("model") or HEALTH_EXECUTION_PROFILE["model"], 64)
    reasoning = _bounded_text(value.get("reasoning") or HEALTH_EXECUTION_PROFILE["reasoning"], 16).lower()
    topic = _bounded_text(value.get("topic") or value.get("topic_key") or HEALTH_EXECUTION_PROFILE["topic"], 64).lower()
    if runtime != HEALTH_EXECUTION_PROFILE["runtime"]:
        raise ValidationError("health remediation runtime must be hermes")
    if provider != HEALTH_EXECUTION_PROFILE["provider"]:
        raise ValidationError("health remediation provider must be openai-codex")
    if model != HEALTH_EXECUTION_PROFILE["model"]:
        raise ValidationError("health remediation model must be gpt-5.6-luna")
    if reasoning != HEALTH_EXECUTION_PROFILE["reasoning"]:
        raise ValidationError("health remediation reasoning must be high")
    if topic != HEALTH_EXECUTION_PROFILE["topic"]:
        raise ValidationError("health remediation topic must be health")
    return {
        "runtime": runtime,
        "provider": provider,
        "model": model,
        "reasoning": reasoning,
        "topic": topic,
    }


def normalize_health_signal(signal: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a health-specific source signal into a bounded v1 event."""
    nested = signal.get("health") if isinstance(signal.get("health"), Mapping) else {}

    def value(name: str, default: Any = "") -> Any:
        direct = signal.get(name)
        return direct if direct not in (None, "") else nested.get(name, default)

    required = ("project", "recipient", "severity", "title", "dedup_key", "source_id", "host_id", "signal_type")
    missing = [field for field in required if not _bounded_text(value(field))]
    if missing:
        raise ValidationError(f"missing required health signal fields: {', '.join(missing)}")
    severity = _bounded_text(value("severity")).lower()
    if severity not in {"debug", "info", "notice", "important", "critical", "emergency"}:
        raise ValidationError("unsupported health signal severity")
    signal_type = _bounded_text(value("signal_type"))
    if not signal_type:
        raise ValidationError("health signal_type is required")
    event = {
        "schema": "notify.event.v1",
        "project": _bounded_text(value("project"), 128),
        "recipient": _bounded_text(value("recipient"), 128),
        "kind": "incident",
        "severity": severity,
        "title": _bounded_summary(value("title")),
        "body": _bounded_summary(signal.get("summary") or signal.get("body") or nested.get("summary") or nested.get("body")),
        "dedup_key": _bounded_text(value("dedup_key"), 500),
        "event_type": _bounded_text(signal.get("event_type") or f"health.{signal_type}", 128),
        "producer": _bounded_text(signal.get("producer") or "health-monitor", 128),
        "plugin": _bounded_text(signal.get("plugin") or "health-incident-monitor", 128),
        "correlation_id": _bounded_text(value("correlation_id") or value("source_id"), 256),
        "operator_note": _bounded_summary(signal.get("summary") or nested.get("summary")),
        "source_id": _bounded_text(value("source_id"), 128),
        "host_id": _bounded_text(value("host_id"), 128),
        "signal_type": signal_type,
        "evidence_refs": _bounded_refs(value("evidence_refs", [])),
    }
    source_fingerprint = _bounded_text(value("source_fingerprint") or value("fingerprint"), 128)
    if source_fingerprint:
        event["source_fingerprint"] = source_fingerprint
    agent_job = _bounded_text(signal.get("agent_job"), 128)
    if agent_job:
        event["agent_job"] = agent_job
    parent_event_id = signal.get("parent_event_id")
    parent_incident_id = signal.get("parent_incident_id")
    if isinstance(parent_event_id, str) and parent_event_id.strip():
        event["parent_event_id"] = _bounded_text(parent_event_id, 128)
    if isinstance(parent_incident_id, str) and parent_incident_id.strip():
        event["parent_incident_id"] = _bounded_text(parent_incident_id, 128)
    return event


def validate_health_plans(plans: Any) -> list[dict[str, Any]]:
    """Require exactly three unique bounded plans with stable IDs."""
    if not isinstance(plans, list) or len(plans) != 3:
        raise ValidationError("health plans must contain exactly three entries")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in plans:
        if not isinstance(raw, Mapping):
            raise ValidationError("health plans must be objects")
        plan_id = _safe_plan_id(raw.get("plan_id") or raw.get("id"))
        if plan_id in seen:
            raise ValidationError("health plan IDs must be unique")
        seen.add(plan_id)
        normalized.append({
            "plan_id": plan_id,
            "title": _bounded_summary(raw.get("title")),
            "summary": _bounded_summary(raw.get("summary") or raw.get("body")),
            "step": _bounded_text(raw.get("step") or raw.get("action") or raw.get("kind"), 64),
            "execution": normalize_health_execution(raw.get("execution")),
        })
    return normalized


def _default_health_plans() -> list[dict[str, Any]]:
    return [
        {"plan_id": "observe", "title": "Observe", "summary": "Gather a bounded live snapshot", "step": "observe"},
        {"plan_id": "repair", "title": "Repair", "summary": "Execute the selected repair path", "step": "repair"},
        {"plan_id": "verify", "title": "Verify", "summary": "Confirm the original source is healthy", "step": "verify"},
    ]


def health_plan_keyboard(codec: Any, incident: Mapping[str, Any] | str, plans: list[Mapping[str, Any]] | None = None) -> dict[str, list[list[dict[str, str]]]]:
    """Render exactly three signed selection buttons."""
    incident_id = str(incident.get("id") if isinstance(incident, Mapping) else incident)
    normalized = validate_health_plans(list(plans)) if plans is not None else _default_health_plans()
    buttons: list[list[dict[str, str]]] = []
    for plan in normalized:
        try:
            callback_data = codec.encode(incident_id, plan["plan_id"])
        except TypeError:
            callback_data = codec.encode("health_plan", incident_id, plan["plan_id"])
        buttons.append([{"text": plan["title"], "callback_data": callback_data}])
    return {"inline_keyboard": buttons}


def progress_fingerprint(plan_id: str, step: str, evidence_refs: list[str]) -> str:
    """Create a bounded deterministic receipt fingerprint."""
    payload = json.dumps({"evidence_refs": evidence_refs[:_RECEIPT_LIMIT], "plan_id": plan_id, "step": step}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:64]


def useful_progress(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    """Treat only step/evidence/fingerprint changes as useful progress."""
    if previous is None:
        return True
    return any(
        previous.get(field) != current.get(field)
        for field in ("step", "evidence_refs", "progress_fingerprint")
    )


class TelegramHealthPlanCodec:
    """Sign plan-selection callbacks so a health plan cannot be forged."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Telegram health callback secret must be at least 16 characters")
        self._secret = secret.encode()

    @property
    def secret(self) -> str:
        """Expose the configured secret for same-process helper construction."""
        return self._secret.decode()

    def encode(self, incident_id: str, plan_id: str) -> str:
        safe_incident = _bounded_text(incident_id, 128)
        safe_plan = _safe_plan_id(plan_id)
        unsigned = f"h:{safe_incident}:{safe_plan}"
        signature = hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()[:12]
        return f"{unsigned}:{signature}"

    def decode(self, value: str) -> tuple[str, str] | None:
        parts = value.split(":")
        if len(parts) != 4 or parts[0] != "h":
            return None
        incident_id, plan_id, signature = parts[1:]
        if not _PLAN_ID_RE.fullmatch(plan_id) or not _bounded_text(incident_id, 128):
            return None
        expected = self.encode(incident_id, plan_id).rsplit(":", 1)[1]
        if not hmac.compare_digest(signature, expected):
            return None
        return incident_id, plan_id


class HealthWorkflow:
    """Thin workflow wrapper over durable NoticePlace storage."""

    def __init__(self, center: NotificationCenter, callback_secret: str | None = None) -> None:
        self._center = center
        self._codec = TelegramHealthPlanCodec(callback_secret) if callback_secret else None

    @property
    def codec(self) -> TelegramHealthPlanCodec | None:
        return self._codec

    def intake_signal(self, token: str, idempotency_key: str, signal: Mapping[str, Any], request_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
        event = normalize_health_signal(signal)
        return self._center.create_event(token, idempotency_key, event, request_meta=request_meta)

    def attach_plans(
        self,
        incident_id: str,
        idempotency_key: str,
        plans: list[Mapping[str, Any]],
        actor: str = "gptadmin",
        correlation_id: str | None = None,
        trace_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        orchestration: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = validate_health_plans(plans)
        return self._center.record_health_update(
            incident_id,
            idempotency_key,
            "health.plans_attached",
            {
                "plans": normalized,
                "correlation_id": _bounded_text(correlation_id, 256) if correlation_id else None,
                "trace_refs": _bounded_refs(trace_refs),
                "evidence_refs": _bounded_refs(evidence_refs),
                "orchestration": _normalize_orchestration(orchestration),
            },
            actor=actor,
        )

    def select_plan(self, incident_id: str, idempotency_key: str, plan_id: str, actor: str) -> dict[str, Any]:
        safe_plan_id = _safe_plan_id(plan_id)
        return self._center.select_health_plan(incident_id, idempotency_key, safe_plan_id, actor)

    def select_plan_from_callback(self, callback_data: str, actor: str) -> dict[str, Any]:
        if self._codec is None:
            raise ValidationError("health plan callbacks are not configured")
        parsed = self._codec.decode(callback_data)
        if parsed is None:
            raise ValidationError("invalid health plan callback")
        incident_id, plan_id = parsed
        return self.select_plan(incident_id, f"{incident_id}:health.plan:{plan_id}", plan_id, actor)

    def record_progress(
        self,
        incident_id: str,
        idempotency_key: str,
        *,
        plan_id: str,
        step: str,
        evidence_refs: list[str],
        progress_fingerprint: str,
        heartbeat_at: float | None = None,
        actor: str,
    ) -> dict[str, Any]:
        safe_plan_id = _safe_plan_id(plan_id)
        safe_step = _bounded_text(step, 128)
        safe_refs = _bounded_refs(evidence_refs)
        safe_fingerprint = _bounded_text(progress_fingerprint, 128)
        if not (safe_step or safe_refs or safe_fingerprint):
            raise ValidationError("health progress requires a non-empty step, evidence, or fingerprint")
        heartbeat_label = safe_step.strip().lower() in {"heartbeat", "keepalive", "heartbeat-only"}
        if heartbeat_at is not None and not safe_refs and heartbeat_label:
            raise ValidationError("heartbeat-only health progress is not accepted")
        result = self._center.record_health_progress(
            incident_id,
            idempotency_key,
            safe_plan_id,
            safe_step,
            safe_refs,
            safe_fingerprint,
            heartbeat_at,
            actor,
        )
        # Core storage is authoritative across HTTP and direct callers; do not
        # recompute a first-receipt value in this wrapper.
        return result | {"useful_progress": bool(result.get("useful"))}

    def record_verification(
        self,
        incident_id: str,
        idempotency_key: str,
        *,
        source_id: str,
        verification_id: str,
        observed_state: str,
        evidence_refs: list[str],
        fingerprint: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        state = _bounded_text(observed_state, 32).lower()
        if state not in {"healthy", "degraded"}:
            raise ValidationError("verification observed_state must be healthy or degraded")
        return self._center.record_health_verification(
            incident_id,
            actor or "verification",
            {
                "source_id": _bounded_text(source_id, 128),
                "verifier_id": _bounded_text(actor or "verification", 128),
                "verification_id": _bounded_text(verification_id, 128),
                "healthy": state == "healthy",
                "fingerprint": _bounded_text(fingerprint, 128) if fingerprint else "",
                "evidence": " ".join(_bounded_refs(evidence_refs)),
            },
            idempotency_key,
        )

    def resolve(self, incident_id: str, source_id: str, verification_id: str, actor: str, elapsed_ms: int | None = None, trace_refs: list[str] | None = None) -> dict[str, Any]:
        bounded_elapsed = max(0, min(86_400_000, int(elapsed_ms or 0)))
        return self._center.resolve_health_incident(
            incident_id,
            _bounded_text(source_id, 128),
            _bounded_text(verification_id, 128),
            actor,
            elapsed_ms=bounded_elapsed,
            trace_refs=_bounded_refs(trace_refs or []),
        )
