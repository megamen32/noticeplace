"""Signed, idempotent GPTAdmin webhook adapter for allowlisted agent jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


_INCIDENT_FIELDS = ("id", "project", "severity", "title", "body", "dedup_key", "occurrences")
_INCIDENT_LIMITS = {"id": 128, "project": 128, "severity": 32, "title": 500, "body": 3000, "dedup_key": 500, "occurrences": 32}
_TERMINAL_STATES = {"completed", "failed"}
_RESPONSE_LIMIT = 64 * 1024


class GptAdminAgentJobAdapter:
    """Submit one fixed job route and follow its durable result to completion."""

    def __init__(
        self,
        job_id: str,
        url: str,
        hmac_secret: str,
        timeout_seconds: float = 90,
        poll_interval_seconds: float = 1,
        runner: Any = urllib.request.urlopen,
        now: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.job_id = job_id.strip()
        self._url = url.strip()
        self._secret = hmac_secret.strip()
        self._timeout_seconds = max(1, float(timeout_seconds))
        self._poll_interval_seconds = max(0, float(poll_interval_seconds))
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
                structured = container.get("structuredContent")
                if isinstance(structured, dict):
                    candidates.append(structured.get("result"))
        parsed_stdout: dict[str, Any] | None = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("session_id") or candidate.get("sessionId") or ""):
                parsed_stdout = candidate
                break
            stdout = candidate.get("stdout")
            if isinstance(stdout, str):
                for line in reversed([part for part in stdout.splitlines() if part.strip()]):
                    try:
                        decoded = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict) and str(decoded.get("session_id") or decoded.get("sessionId") or ""):
                        parsed_stdout = decoded
                        break
            if parsed_stdout is not None:
                break
        if parsed_stdout is None:
            return {}
        return {
            "session_id": str(parsed_stdout.get("session_id") or parsed_stdout.get("sessionId") or "")[:128],
            "profile": str(parsed_stdout.get("profile") or "")[:128],
            "harness": str(parsed_stdout.get("harness") or "")[:32],
            "name": str(parsed_stdout.get("name") or "")[:128],
            "created": parsed_stdout.get("created") is True,
            "delivery": str(parsed_stdout.get("delivery") or "")[:32],
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
        if not idempotency_key.strip() or len(idempotency_key) > 512:
            raise RuntimeError("GPTAdmin agent job requires a bounded idempotency key")
        incident = payload.get("incident")
        if not isinstance(incident, dict):
            raise RuntimeError("GPTAdmin agent job payload is missing incident telemetry")
        bounded_incident: dict[str, str] = {}
        for field in _INCIDENT_FIELDS:
            value = str(incident.get(field) if incident.get(field) is not None else "")
            bounded_incident[field] = " ".join(value.replace("\x00", "").splitlines())[:_INCIDENT_LIMITS[field]]
        event = {
            "schema": "notify.agent-job.v1",
            "job_id": self.job_id,
            "incident": bounded_incident,
        }
        body = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        accepted = self._open(self._signed_request(self._url, "POST", body, idempotency_key), min(20, self._timeout_seconds))
        hub_job_id = str(accepted.get("job_id") or "").strip()
        if not hub_job_id or str(accepted.get("status") or "") not in ("accepted", "running", "completed", "failed"):
            raise RuntimeError("GPTAdmin agent job did not return a durable job identity")

        parsed = urllib.parse.urlsplit(self._url)
        job_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"/webhook-jobs/{urllib.parse.quote(hub_job_id, safe='')}", "", ""))
        deadline = self._now() + self._timeout_seconds
        current = accepted
        while str(current.get("status") or "") not in _TERMINAL_STATES:
            if self._now() >= deadline:
                raise RuntimeError("GPTAdmin agent job did not complete before timeout")
            if self._poll_interval_seconds:
                self._sleeper(self._poll_interval_seconds)
            current = self._open(self._signed_request(job_url, "GET"), min(20, max(1, deadline - self._now())))
        current["agent_receipt"] = self._bounded_agent_receipt(current)
        return current
