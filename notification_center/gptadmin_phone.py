"""Narrow no-ADB cellular-call transport through one fixed GPTAdmin command."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


# The matching Termux script owns the allowed phone number.  This module never
# accepts a command, a number, or incident text from an event payload.
FIXED_PHONE_COMMAND = "exec /data/data/com.termux/files/home/.local/bin/notify-phone-call"


class GptAdminPhoneAdapter:
    """Invoke the S21's fixed Termux call script through its ShellMCP target."""

    def __init__(self, url: str, token: str, timeout_seconds: float = 20, runner: Any = urllib.request.urlopen) -> None:
        self._url = url.strip()
        self._token = token.strip()
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    @property
    def can_phone_call(self) -> bool:
        return bool(self._url and self._token)

    def phone_call(self, _payload: dict[str, Any]) -> None:
        """Call the fixed on-phone command without forwarding payload fields."""
        if not self.can_phone_call:
            raise RuntimeError("GPTAdmin phone adapter is not configured")
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"cmd": FIXED_PHONE_COMMAND}, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        with self._runner(request, timeout=self._timeout_seconds) as response:
            if not 200 <= int(response.status) < 300:
                raise RuntimeError(f"GPTAdmin phone adapter returned HTTP {response.status}")

    def telegram_call(self, _payload: dict[str, Any]) -> None:
        raise RuntimeError("GPTAdmin phone adapter only supports cellular calls")
