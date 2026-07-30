"""Independent vpn2 health watchdog that alerts directly instead of using the primary."""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class WatchdogConfig:
    """Bounded health and alerting policy for the separate vpn2 failure domain."""

    failure_threshold: int = 3
    recovery_threshold: int = 2
    alert_retry_seconds: int = 60


class Watchdog:
    """Persist failure counters and direct-alert state without importing the primary core.

    Args:
        config: Consecutive probe and alert retry limits.
        state_path: Watchdog-local JSON state, normally under StateDirectory.
        probe: Independent function returning whether the external health contract passes.
        direct_sender: Direct channel function, normally a separate Telegram bot.
        clock: Injectable epoch clock used to make behavior deterministic in tests.
    """

    def __init__(self, config: WatchdogConfig, state_path: Path, probe: Callable[[], bool], direct_sender: Callable[[str], None], clock: Callable[[], float] = time.time) -> None:
        """Load prior state so cooldowns and recovery survive service restarts."""
        self._config = config
        self._state_path = state_path
        self._probe = probe
        self._direct_sender = direct_sender
        self._clock = clock
        self._state = self._load()

    def _load(self) -> dict[str, object]:
        """Load valid state or return an empty safe baseline when first started."""
        try:
            saved = json.loads(self._state_path.read_text())
            if isinstance(saved, dict):
                return {**self._empty_state(), **saved}
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, object]:
        """Return default persisted fields with no active failure or credentials."""
        return {"failure_count": 0, "success_count": 0, "down": False, "alert_pending": False, "alert_sent": False, "last_alert_attempt": 0.0, "down_since": None, "last_success": None}

    def _save(self) -> None:
        """Atomically replace local state; raises OSError when persistence is unavailable."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".watchdog-", dir=str(self._state_path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self._state, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _can_attempt_alert(self, now: float) -> bool:
        """Return whether retry cooldown allows another direct alert attempt."""
        return now - float(self._state["last_alert_attempt"] or 0) >= self._config.alert_retry_seconds

    def _send(self, message: str, now: float) -> bool:
        """Attempt direct transport, retaining pending state on failure."""
        self._state["last_alert_attempt"] = now
        try:
            self._direct_sender(message)
        except Exception:
            self._state["alert_pending"] = True
            return False
        self._state["alert_pending"] = False
        self._state["alert_sent"] = True
        return True

    def run_once(self) -> dict[str, object]:
        """Probe exactly once, update durable state, and send bounded direct alerts."""
        now = self._clock()
        healthy = False
        try:
            healthy = bool(self._probe())
        except Exception:
            healthy = False
        if healthy:
            self._state["failure_count"] = 0
            self._state["success_count"] = int(self._state["success_count"]) + 1
            self._state["last_success"] = now
            if bool(self._state["down"]) and int(self._state["success_count"]) >= self._config.recovery_threshold:
                if self._can_attempt_alert(now):
                    self._send("NOTIFICATION CENTER RECOVERED: external health contract is valid again.", now)
                if not bool(self._state["alert_pending"]):
                    self._state = self._empty_state() | {"last_success": now}
        else:
            self._state["success_count"] = 0
            self._state["failure_count"] = int(self._state["failure_count"]) + 1
            if int(self._state["failure_count"]) >= self._config.failure_threshold:
                if not bool(self._state["down"]):
                    self._state["down"] = True
                    self._state["down_since"] = now
                if (not bool(self._state["alert_sent"]) or bool(self._state["alert_pending"])) and self._can_attempt_alert(now):
                    self._send("NOTIFICATION CENTER DOWN: external health contract failed from independent vpn2 watchdog.", now)
        self._save()
        return self.state()

    def state(self) -> dict[str, object]:
        """Return a copy of safe state for local status/testing, never configuration secrets."""
        return dict(self._state)


def probe_health(url: str, expected_service: str, timeout_seconds: float = 8, token: str | None = None) -> bool:
    """Validate the public health JSON contract over HTTPS without redirects.

    Returns false for malformed JSON, unexpected service identity, non-200 HTTP,
    TLS/DNS errors, and timeouts. It never calls the primary event API.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read(16_384))
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
        return False
    return bool(isinstance(body, dict) and body.get("schema") == "notify.health.v1" and body.get("service") == expected_service and body.get("status") == "ok" and body.get("storage_ready") is True and body.get("dispatcher_ready") is True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so vpn2 proves the exact configured public route is healthy."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Suppress redirect following; urllib then reports the response as an error."""
        return None
