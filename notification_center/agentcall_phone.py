"""Local Unix-socket adapter for prepared interactive AI incident calls."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import socket
import threading
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
        event_type = str(incident.get("event_type") or "")
        call_test = event_type == "operator.call_test"
        opening = (
            "Проверка связи." if call_test else
            "Новая заявка." if event_type == "lead.created" else
            "Внимание. Сломалось что-то."
        )
        request = {
            "message": f"{opening} {title}. {body}",
            "context": (
                f"Уровень {severity}. {title}. {body}\n"
                "У голосового ассистента нет инструментов запуска работ или управления агентами. "
                "Не обещай, что начал проверку или исправление. Объясняй только сведения из уведомления. "
                "Фактический запуск и прогресс работы подтверждаются сессией Agent Herder в карточке Notice Place."
            ),
            "repeat": 1 if call_test else 2,
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

    def acknowledge_voice_event(self, center: Any, event: dict[str, Any]) -> bool:
        """Treat an explicit remote confirmation as receipt, never as repair."""
        if event.get("event") != "transcript_final" or event.get("speaker") != "remote":
            return False
        words = re.findall(r"[а-яё]+", str(event.get("text") or "").casefold().replace("ё", "е"))
        phrase = " ".join(words)
        if not re.fullmatch(
            r"(?:(?:да|ага|окей|хорошо|спасибо|я|все|это|тебя|вас|уже) )*"
            r"(?:услышал|услышала|понял|поняла|принял|приняла)"
            r"(?: (?:да|ага|хорошо|спасибо|все|вас|тебя))*", phrase,
        ):
            return False
        call_id = str(event.get("callId") or "")
        return center.acknowledge_phone_receipt(call_id) is not None

    def watch_voice_acknowledgements(self, center: Any, stop: threading.Event | None = None) -> None:
        """One bounded subscriber to the gateway's existing redacted event stream."""
        stop = stop or threading.Event()
        event_path = str(Path(self._socket_path).with_name("gatewayd.sock"))
        while not stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(10)
                    connection.connect(event_path)
                    connection.sendall(b'{"id":"noticeplace-voice-ack","method":"events","args":{}}\n')
                    with connection.makefile("rb") as stream:
                        greeting = json.loads(stream.readline(65_537))
                        if greeting.get("result", {}).get("subscribed") is not True:
                            raise ValueError("voice event subscription rejected")
                        connection.settimeout(None)
                        while not stop.is_set():
                            line = stream.readline(65_537)
                            if not line or len(line) > 65_536 or not line.endswith(b"\n"):
                                raise ConnectionError("voice event stream closed")
                            event = json.loads(line).get("event")
                            if isinstance(event, dict):
                                self.acknowledge_voice_event(center, event)
            except (OSError, ValueError, ConnectionError):
                logging.getLogger(__name__).warning("Voice confirmation subscription reconnecting")
                stop.wait(3)
