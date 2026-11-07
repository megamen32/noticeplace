"""HTTP client for creating and observing NoticePlace incidents."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Mapping


class NotificationCenterError(RuntimeError):
    """A safe error raised for invalid API input or a rejected API request."""

    def __init__(self, message: str, *, status: int | None = None, payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = dict(payload or {})


class WaitTimeoutError(NotificationCenterError):
    """The incident remained open/snoozed until the caller's deadline."""

    def __init__(self, incident: Mapping[str, Any] | None) -> None:
        super().__init__("timed out waiting for an operator acknowledgement or resolution", payload=incident)
        self.incident = dict(incident or {})


class NotificationCenterClient:
    """Publish scoped events and optionally wait for an operator response."""

    response_states = frozenset(("acknowledged", "resolved"))

    def __init__(self, event_url: str, token: str, *, request_timeout: float = 8.0) -> None:
        self.event_url = event_url.rstrip("/")
        self.token = token
        self.request_timeout = request_timeout
        if not self.event_url.endswith("/v1/events"):
            raise ValueError("event_url must end with /v1/events")
        if not self.token:
            raise ValueError("token is required")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.incidents_url = self.event_url[: -len("/v1/events")] + "/v1/incidents"

    @classmethod
    def from_environment(cls, *, request_timeout: float = 8.0) -> "NotificationCenterClient":
        """Read the standard producer identity without printing its token."""
        event_url = os.environ.get("NOTIFY_CENTER_EVENT_URL", "")
        token = os.environ.get("NOTIFY_CENTER_TOKEN", "")
        if not event_url or not token:
            raise ValueError("NOTIFY_CENTER_EVENT_URL and NOTIFY_CENTER_TOKEN are required")
        return cls(event_url, token, request_timeout=request_timeout)

    def emit(
        self,
        *,
        project: str,
        severity: str,
        title: str,
        dedup_key: str,
        recipient: str = "me",
        body: str = "",
        kind: str = "incident",
        idempotency_key: str | None = None,
        wait_for_response: bool = False,
        wait_timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 10.0,
        event_type: str | None = None,
        source_id: str | None = None,
        host_id: str | None = None,
        signal_type: str | None = None,
        problem_type: str | None = None,
        correlation_id: str | None = None,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        """Create one event, optionally waiting for ACK or resolution.

        For typed health events the caller persists an explicit idempotency key
        before submission and reconciles uncertain outcomes without replay.
        """
        key = idempotency_key or str(uuid.uuid4())
        payload = {
            "schema": "notify.event.v1",
            "project": project,
            "recipient": recipient,
            "kind": kind,
            "severity": severity,
            "title": title,
            "body": body,
            "dedup_key": dedup_key,
        }
        identity = {"source_id": source_id, "host_id": host_id, "signal_type": signal_type,
                    "problem_type": problem_type, "correlation_id": correlation_id}
        if event_type is not None or any(value is not None for value in identity.values()) or observed_at is not None:
            if event_type != "health.degraded" or not idempotency_key:
                raise ValueError("typed health requires health.degraded and a persisted idempotency key")
            if any(not isinstance(value, str) or not value.strip() or len(value) > 128 or
                   any(ord(char) < 32 or ord(char) == 127 for char in value)
                   for value in identity.values()):
                raise ValueError("typed health requires bounded source identity fields")
            if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)) or not math.isfinite(observed_at) or observed_at <= 0:
                raise ValueError("typed health requires a positive finite observed_at")
            payload.update(event_type=event_type, observed_at=observed_at, **identity)
        accepted = self._request("POST", self.event_url, payload=payload, idempotency_key=key)
        accepted["idempotency_key"] = key
        if not wait_for_response:
            return accepted
        return self.wait_for_response(
            str(accepted["incident_id"]),
            timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Read the authoritative incident state with the same producer scope."""
        if not incident_id:
            raise ValueError("incident_id is required")
        return self._request("GET", f"{self.incidents_url}/{incident_id}")

    def wait_for_response(
        self,
        incident_id: str,
        *,
        timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Poll until an operator acknowledges or resolves this incident."""
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("timeout_seconds and poll_interval_seconds must be positive")
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] | None = None
        while True:
            last = self.get_incident(incident_id)
            if last.get("state") in self.response_states:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WaitTimeoutError(last)
            time.sleep(min(poll_interval_seconds, remaining))

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                return self._decode(response.read())
        except urllib.error.HTTPError as error:
            decoded = self._decode(error.read())
            message = str(decoded.get("error") or f"NoticePlace returned HTTP {error.code}")
            raise NotificationCenterError(message, status=error.code, payload=decoded) from None
        except urllib.error.URLError as error:
            raise NotificationCenterError("NoticePlace request failed") from error

    @staticmethod
    def _decode(body: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(body)
        except (TypeError, json.JSONDecodeError) as error:
            raise NotificationCenterError("NoticePlace returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise NotificationCenterError("NoticePlace returned an invalid response envelope")
        return decoded
