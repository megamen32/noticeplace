"""Signed, idempotent GPTAdmin webhook adapter for allowlisted agent jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from .health_workflow import sanitize_bounded_text


_INCIDENT_FIELDS = ("id", "project", "severity", "title", "body", "dedup_key", "occurrences")
_INCIDENT_LIMITS = {"id": 128, "project": 128, "severity": 32, "title": 500, "body": 3000, "dedup_key": 500, "occurrences": 32}
_TERMINAL_STATES = {"completed", "failed"}
_RESPONSE_LIMIT = 64 * 1024
_HEALTH_EXECUTION_PROFILE = {
    "runtime": "opencode",
    "provider": "openai-codex",
    "model": "gpt-5.6-luna",
    "reasoning": "high",
    "topic": "health",
}


def _agent_job_event(job_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded helper event shared by Hub and direct execution."""
    incident = payload.get("incident")
    if not isinstance(incident, dict):
        raise RuntimeError("GPTAdmin agent job payload is missing incident telemetry")
    bounded_incident = {
        field: sanitize_bounded_text(
            str(incident.get(field) if incident.get(field) is not None else ""),
            _INCIDENT_LIMITS[field],
        )
        for field in _INCIDENT_FIELDS
    }
    event: dict[str, Any] = {"schema": "notify.agent-job.v1", "job_id": job_id, "incident": bounded_incident}
    health_context = payload.get("health_context")
    if isinstance(health_context, dict):
        bounded_health: dict[str, Any] = {
            "source_id": _safe_health_ref(health_context.get("source_id")),
            "host_id": _safe_health_ref(health_context.get("host_id")),
            "signal_type": _safe_health_ref(health_context.get("signal_type"), 64),
            "correlation_id": _safe_health_ref(health_context.get("correlation_id")),
            "source_fingerprint": _safe_health_ref(health_context.get("source_fingerprint")),
            "trace_refs": [_safe_health_ref(ref) for ref in health_context.get("trace_refs", [])[:16]] if isinstance(health_context.get("trace_refs"), list) else [],
        }
        plans = payload.get("health_plans")
        if isinstance(plans, list):
            bounded_health["plans"] = [
                {key: _safe_health_ref(plan.get(key), 64 if key in {"plan_id", "step"} else 256)
                 for key in ("plan_id", "title", "summary", "step")}
                for plan in plans[:3] if isinstance(plan, dict)
            ]
        selection = payload.get("health_selection")
        if isinstance(selection, Mapping):
            execution = selection.get("execution")
            # Existing selections predate the operator's runtime switch. The
            # chosen plan remains authoritative; execution is now canonical.
            if isinstance(execution, Mapping) and str(execution.get("runtime") or "") == "hermes":
                execution = dict(_HEALTH_EXECUTION_PROFILE)
            bounded_health["selection"] = {
                "plan_id": _safe_health_ref(selection.get("plan_id"), 64),
                "actor": _safe_health_ref(selection.get("actor"), 128),
                "execution": _bounded_health_execution(execution),
            }
        event["health"] = bounded_health
        if bounded_health["correlation_id"]:
            event["correlation_id"] = bounded_health["correlation_id"]
        if bounded_health["trace_refs"]:
            event["trace_refs"] = bounded_health["trace_refs"]
    return event


class DirectHealthRemediationAdapter:
    """Run the existing helper locally, bypassing GPTAdmin Hub for remediation."""

    job_id = "health-remediation"

    def __init__(self, config_path: Path | None = None, runner: Any = urllib.request.urlopen) -> None:
        self._config_path = config_path
        self._runner = runner

    def send(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self.send_with_progress(payload, idempotency_key)

    def send_with_progress(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 512:
            raise RuntimeError("direct health remediation requires a bounded idempotency key")
        from .agent_job_helper import default_profile_path, run_profile
        started_at = time.monotonic()
        direct_profile = {
            "url": os.environ.get("NOTIFY_HEALTH_REMEDIATION_URL", "http://127.0.0.1:18787/api/sessions/new-or-resume"),
            "harness": "opencode",
            "name": "health_remediation_direct",
            "cwd": os.environ.get("NOTIFY_HEALTH_REMEDIATION_CWD", "/home/roomhacker/ServersAdministartion"),
            "mode": "queue",
            "instruction": "Apply only the selected health remediation plan and report useful progress.",
            "model": "openai-codex/gpt-5.6-luna",
            "reasoning": "high",
            "topic": "health",
            "poll_seconds": os.environ.get("NOTIFY_HEALTH_REMEDIATION_POLL_SECONDS", "1"),
            "remediation_timeout_seconds": os.environ.get("NOTIFY_HEALTH_REMEDIATION_TIMEOUT_SECONDS", "1200"),
        }
        receipt = run_profile(
            self.job_id,
            _agent_job_event(self.job_id, payload),
            self._config_path or default_profile_path(),
            runner=self._runner,
            profile_override=direct_profile,
        )
        if progress_callback is not None:
            progress_callback({"status": "completed", "agent_receipt": receipt})
        return {
            "job_id": "direct-agent-herder",
            "route_id": "noticeplace-direct-health-remediation",
            "status": "completed",
            "agent_receipt": receipt,
            "elapsed_ms": max(0, int((time.monotonic() - started_at) * 1000)),
            "supervision": {
                "state": "terminal",
                "useful_progress": receipt.get("useful_progress") is True,
                "progress_fingerprint": _safe_health_ref(receipt.get("progress_fingerprint"), 128),
                "evidence_refs": receipt.get("evidence_refs", [])[:16],
            },
        }


def _safe_health_ref(value: Any, limit: int = 128) -> str:
    return sanitize_bounded_text(value, limit)


def _bounded_health_execution(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RuntimeError("health remediation request is missing execution profile")
    result = {key: _safe_health_ref(value.get(key), 64).lower() for key in _HEALTH_EXECUTION_PROFILE}
    if result != _HEALTH_EXECUTION_PROFILE:
        raise RuntimeError("health remediation request has an unsupported execution profile")
    return result


def _is_useful_progress_entry(item: Mapping[str, Any]) -> bool:
    """Require evidence for useful work and reject heartbeat-only labels."""
    if item.get("useful_progress") is not True:
        return False
    step = _safe_health_ref(item.get("step"), 128).strip().lower()
    evidence_refs = item.get("evidence_refs")
    has_evidence = isinstance(evidence_refs, list) and any(
        _safe_health_ref(ref, 128).strip() for ref in evidence_refs[:16]
    )
    return not (step in {"heartbeat", "keepalive", "heartbeat-only"} and not has_evidence)


class HealthProgressSupervisor:
    """Classify useful work from durable receipts, ignoring heartbeat-only entries."""

    def __init__(self, stale_after_seconds: float = 120, now: Callable[[], float] = time.time) -> None:
        self._stale_after_seconds = max(1, float(stale_after_seconds))
        self._now = now

    def observe(self, job: Mapping[str, Any]) -> dict[str, Any]:
        status = str(job.get("status") or "unknown")
        progress = job.get("progress")
        entries = [item for item in progress if isinstance(item, Mapping)] if isinstance(progress, list) else []
        useful = [item for item in entries if _is_useful_progress_entry(item)]
        if status in _TERMINAL_STATES:
            state = "terminal"
        elif not useful:
            state = "waiting"
        else:
            latest = useful[-1]
            received_at = latest.get("received_at")
            try:
                if isinstance(received_at, (int, float)):
                    age = max(0.0, self._now() - float(received_at))
                else:
                    parsed = str(received_at or "").replace("Z", "+00:00")
                    age = max(0.0, self._now() - datetime.fromisoformat(parsed).timestamp()) if parsed else float("inf")
            except (TypeError, ValueError, OverflowError):
                age = float("inf")
            state = "stalled" if age > self._stale_after_seconds else "progressing"
        latest = useful[-1] if useful else {}
        return {
            "state": state,
            "useful_progress": bool(useful),
            "last_useful_at": latest.get("received_at"),
            "progress_fingerprint": _safe_health_ref(latest.get("fingerprint")),
            "evidence_refs": [_safe_health_ref(ref) for ref in latest.get("evidence_refs", [])[:16]] if isinstance(latest.get("evidence_refs"), list) else [],
        }


class GptAdminAgentJobAdapter:
    """Submit one fixed job route and follow its durable result to completion."""

    def __init__(
        self,
        job_id: str,
        url: str,
        hmac_secret: str,
        timeout_seconds: float = 90,
        poll_interval_seconds: float = 1,
        stale_progress_seconds: float = 120,
        runner: Any = urllib.request.urlopen,
        now: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.job_id = job_id.strip()
        self._url = url.strip()
        self._secret = hmac_secret.strip()
        self._timeout_seconds = max(1, float(timeout_seconds))
        self._poll_interval_seconds = max(0, float(poll_interval_seconds))
        self._supervisor = HealthProgressSupervisor(stale_progress_seconds, now)
        self._runner = runner
        self._now = now
        self._sleeper = sleeper
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.job_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in self.job_id):
            raise RuntimeError("GPTAdmin agent job id must be a safe non-empty identifier")
        parsed = urllib.parse.urlsplit(self._url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.path.startswith("/webhooks/v1/"):
            raise RuntimeError("GPTAdmin agent job URL must be an http(s) webhook route")
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("GPTAdmin agent job URL must use HTTPS unless it is loopback")
        if not self._secret:
            raise RuntimeError("GPTAdmin agent job HMAC secret is required")

    def _signed_request(self, url: str, method: str, body: bytes = b"", idempotency_key: str = "") -> urllib.request.Request:
        timestamp = str(int(self._now()))
        parsed = urllib.parse.urlsplit(url)
        canonical = "\n".join((
            method.upper(),
            parsed.path or "/",
            timestamp,
            idempotency_key,
            hashlib.sha256(body).hexdigest(),
        )).encode()
        signature = "sha256=" + hmac.new(self._secret.encode(), canonical, hashlib.sha256).hexdigest()
        headers = {
            "Accept": "application/json",
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": signature,
        }
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return urllib.request.Request(url, data=body if method == "POST" else None, headers=headers, method=method)

    @staticmethod
    def _read_json(response: Any) -> dict[str, Any]:
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"GPTAdmin agent job returned HTTP {response.status}")
        body = response.read(_RESPONSE_LIMIT + 1)
        if len(body) > _RESPONSE_LIMIT:
            raise RuntimeError("GPTAdmin agent job response exceeds 65536 bytes")
        try:
            result = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("GPTAdmin agent job returned invalid JSON") from error
        if not isinstance(result, dict):
            raise RuntimeError("GPTAdmin agent job response must be an object")
        return result

    @staticmethod
    def _bounded_agent_receipt(job: dict[str, Any]) -> dict[str, Any]:
        """Extract only helper-owned receipt fields from known ShellMCP shapes."""
        result = job.get("result")
        candidates: list[Any] = [result]
        if isinstance(result, dict):
            response = result.get("response")
            candidates.append(response)
            for container in (result, response):
                if not isinstance(container, dict):
                    continue
                nested_receipt = container.get("agent_receipt")
                if isinstance(nested_receipt, dict):
                    candidates.append(nested_receipt)
                structured = container.get("structuredContent")
                if isinstance(structured, dict):
                    candidates.append(structured.get("result"))
        parsed_stdout: dict[str, Any] | None = None

        def looks_like_receipt(candidate: Mapping[str, Any]) -> bool:
            return bool(
                str(candidate.get("session_id") or candidate.get("sessionId") or "")
                or (
                    str(candidate.get("plan_id") or "")
                    and str(candidate.get("observed_state") or candidate.get("verification_id") or "")
                )
            )

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if looks_like_receipt(candidate):
                parsed_stdout = candidate
                break
            stdout = candidate.get("stdout")
            if isinstance(stdout, str):
                for line in reversed([part for part in stdout.splitlines() if part.strip()]):
                    try:
                        decoded = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict) and looks_like_receipt(decoded):
                        parsed_stdout = decoded
                        break
            if parsed_stdout is not None:
                break
        if parsed_stdout is None:
            return {}
        evidence_refs = parsed_stdout.get("evidence_refs")
        bounded_evidence_refs = [_safe_health_ref(item, 128) for item in evidence_refs[:10]] if isinstance(evidence_refs, list) else []
        return {
            "session_id": _safe_health_ref(parsed_stdout.get("session_id") or parsed_stdout.get("sessionId") or "", 128),
            "profile": _safe_health_ref(parsed_stdout.get("profile") or "", 128),
            "harness": _safe_health_ref(parsed_stdout.get("harness") or "", 32),
            "name": _safe_health_ref(parsed_stdout.get("name") or "", 128),
            "plan_id": _safe_health_ref(parsed_stdout.get("plan_id") or "", 64),
            "model": _safe_health_ref(parsed_stdout.get("model") or "", 128),
            "reasoning": _safe_health_ref(parsed_stdout.get("reasoning") or "", 16),
            "topic": _safe_health_ref(parsed_stdout.get("topic") or "", 64),
            "created": parsed_stdout.get("created") is True,
            "delivery": _safe_health_ref(parsed_stdout.get("delivery") or "", 32),
            "step": _safe_health_ref(parsed_stdout.get("step") or "", 128),
            "progress_fingerprint": _safe_health_ref(parsed_stdout.get("progress_fingerprint") or "", 128),
            "evidence_refs": bounded_evidence_refs,
            "trace_refs": [_safe_health_ref(item, 128) for item in parsed_stdout.get("trace_refs", [])[:16]] if isinstance(parsed_stdout.get("trace_refs"), list) else [],
            "correlation_id": _safe_health_ref(parsed_stdout.get("correlation_id") or "", 128),
            "useful_progress": parsed_stdout.get("useful_progress") is True,
            "verification_id": _safe_health_ref(parsed_stdout.get("verification_id") or "", 128),
            "source_id": _safe_health_ref(parsed_stdout.get("source_id") or "", 128),
            "source_fingerprint": _safe_health_ref(parsed_stdout.get("source_fingerprint") or parsed_stdout.get("fingerprint") or "", 128),
            "verifier_id": _safe_health_ref(parsed_stdout.get("verifier_id") or parsed_stdout.get("verification_source_id") or "", 128),
            "observed_state": _safe_health_ref(parsed_stdout.get("observed_state") or "", 32),
        }

    def _open(self, request: urllib.request.Request, timeout: float) -> dict[str, Any]:
        try:
            with self._runner(request, timeout=timeout) as response:
                return self._read_json(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"GPTAdmin agent job returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError("GPTAdmin agent job is unreachable") from error

    def send(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        """Submit safe incident telemetry and return the terminal Hub job receipt."""
        return self.send_with_progress(payload, idempotency_key)

    def send_with_progress(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Submit telemetry while forwarding durable useful-progress snapshots."""
        if not idempotency_key.strip() or len(idempotency_key) > 512:
            raise RuntimeError("GPTAdmin agent job requires a bounded idempotency key")
        event = _agent_job_event(self.job_id, payload)
        body = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        started_at = self._now()
        accepted = self._open(self._signed_request(self._url, "POST", body, idempotency_key), min(20, self._timeout_seconds))
        hub_job_id = str(accepted.get("job_id") or "").strip()
        if not hub_job_id or str(accepted.get("status") or "") not in ("accepted", "running", "completed", "failed"):
            raise RuntimeError("GPTAdmin agent job did not return a durable job identity")

        parsed = urllib.parse.urlsplit(self._url)
        job_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"/webhook-jobs/{urllib.parse.quote(hub_job_id, safe='')}", "", ""))
        deadline = started_at + self._timeout_seconds
        current = accepted
        if progress_callback is not None:
            progress_callback(current)
        while str(current.get("status") or "") not in _TERMINAL_STATES:
            if self._now() >= deadline:
                raise RuntimeError("GPTAdmin agent job did not complete before timeout")
            if self._poll_interval_seconds:
                self._sleeper(self._poll_interval_seconds)
            current = self._open(self._signed_request(job_url, "GET"), min(20, max(1, deadline - self._now())))
            current["supervision"] = self._supervisor.observe(current)
            if self.job_id == "health-remediation" and current["supervision"].get("state") == "stalled":
                raise RuntimeError("health remediation stalled without useful progress")
            if progress_callback is not None:
                progress_callback(current)
        current["agent_receipt"] = self._bounded_agent_receipt(current)
        current["supervision"] = self._supervisor.observe(current)
        elapsed_ms = max(0, int((self._now() - started_at) * 1000))
        return self._bounded_terminal_response(current, elapsed_ms)

    @staticmethod
    def _bounded_terminal_response(job: Mapping[str, Any], elapsed_ms: int) -> dict[str, Any]:
        """Return only the stable terminal envelope; never expose raw Hub result data."""
        response: dict[str, Any] = {
            "job_id": _safe_health_ref(job.get("job_id"), 128),
            "route_id": _safe_health_ref(job.get("route_id"), 128),
            "status": _safe_health_ref(job.get("status"), 32),
            "agent_receipt": job.get("agent_receipt") if isinstance(job.get("agent_receipt"), Mapping) else {},
            "elapsed_ms": max(0, min(86_400_000, int(elapsed_ms))),
        }
        if job.get("error") is not None:
            response["error"] = _safe_health_ref(job.get("error"), 256)
        supervision = job.get("supervision")
        if isinstance(supervision, Mapping):
            response["supervision"] = {
                "state": _safe_health_ref(supervision.get("state"), 32),
                "useful_progress": supervision.get("useful_progress") is True,
                "last_useful_at": _safe_health_ref(supervision.get("last_useful_at"), 64),
                "progress_fingerprint": _safe_health_ref(supervision.get("progress_fingerprint"), 128),
                "evidence_refs": [
                    _safe_health_ref(ref, 128)
                    for ref in supervision.get("evidence_refs", [])[:16]
                ] if isinstance(supervision.get("evidence_refs"), list) else [],
            }
        return response
