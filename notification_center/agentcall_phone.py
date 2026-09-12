"""Local Unix-socket adapter for prepared interactive AI incident calls."""

from __future__ import annotations

import json
import socket
from typing import Any, Callable


Requester = Callable[[str, bytes, float], bytes]


def _unix_request(path: str, payload: bytes, timeout: float) -> bytes:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(path)
        client.sendall(payload + b"\n")
        response = bytearray()
        while b"\n" not in response and len(response) <= 65_536:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response).split(b"\n", 1)[0]
    finally:
        client.close()


class AgentCallPhoneAdapter:
    """Ask the local AgentCall bridge to pre-synthesize and originate one call."""

    def __init__(self, socket_path: str, timeout_seconds: float = 30, requester: Requester = _unix_request) -> None:
        self._socket_path = socket_path.strip()
        self._timeout_seconds = timeout_seconds
        self._requester = requester

    @property
    def can_phone_call(self) -> bool:
        return bool(self._socket_path)

    def phone_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.can_phone_call:
            raise RuntimeError("AgentCall control socket is not configured")
        incident = payload.get("incident") if isinstance(payload, dict) else None
        if not isinstance(incident, dict):
            raise RuntimeError("AgentCall delivery has no incident")
        title = " ".join(str(incident.get("title") or "Инцидент").split())[:500]
        body = " ".join(str(incident.get("body") or "Подробности отсутствуют").split())[:1200]
        severity = str(incident.get("severity") or "critical")
        request = {
            "message": f"Внимание. Сломалось что-то. {title}. {body}",
            "context": f"Уровень {severity}. {title}. {body}",
            "repeat": 2,
            "incident_id": str(incident.get("id") or "")[:128],
        }
        raw = self._requester(
            self._socket_path,
            json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode(),
            self._timeout_seconds,
        )
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("AgentCall bridge returned invalid JSON") from error
        if not isinstance(result, dict) or result.get("ok") is not True:
            reason = result.get("error") if isinstance(result, dict) else None
            raise RuntimeError(f"AgentCall bridge rejected the call: {reason or 'unknown error'}")
        return result

    def telegram_call(self, _payload: dict[str, Any]) -> None:
        raise RuntimeError("AgentCall adapter only supports cellular calls")
