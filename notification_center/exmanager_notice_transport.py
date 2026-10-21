"""Authenticated adapter for the existing ExManager same-client question API.

The adapter never owns a Telegram client. It sends one stable operation to the
Node endpoint and exposes a query-only reconciliation path for uncertain sends.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping


CHAT_ID = "-1004352022764"
SENDER_ID = "8810909089"
USERNAME = "@deus_excode"
BASE_URL = "https://notify.bezrabotnyi.com/_exmanager"
MAX_RESPONSE_BYTES = 16_384


class ExManagerNoticeTransportError(RuntimeError):
    """A bounded transport/protocol error without response or credential data."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class ExManagerNoticeTransport:
    """Call the fixed, authenticated ExManager question routes."""

    def __init__(
        self,
        base_url: str,
        bearer: str,
        *,
        timeout: float = 5.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        configured = str(base_url or "").strip().rstrip("/")
        if configured != BASE_URL:
            raise ExManagerNoticeTransportError("unsupported ExManager Notice endpoint")
        if not isinstance(bearer, str) or not bearer.strip():
            raise ExManagerNoticeTransportError("ExManager Notice capability is unavailable")
        self._base_url = configured
        self._bearer = bearer.strip()
        self._timeout = max(0.1, min(float(timeout), 15.0))
        self._opener = opener

    def _open(self, request: urllib.request.Request) -> Mapping[str, Any]:
        try:
            if self._opener is None:
                response = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), _NoRedirect(),
                ).open(request, timeout=self._timeout)
            else:
                response = self._opener(request, timeout=self._timeout)
            with response:
                status = int(response.status if hasattr(response, "status") else response.getcode())
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise ExManagerNoticeTransportError(f"ExManager Notice HTTP {error.code}") from None
        except ExManagerNoticeTransportError:
            raise
        except Exception as error:
            raise ExManagerNoticeTransportError(
                f"ExManager Notice transport failed ({error.__class__.__name__})"
            ) from None
        if status < 200 or status >= 300 or len(body) > MAX_RESPONSE_BYTES:
            raise ExManagerNoticeTransportError("ExManager Notice returned an invalid response")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ExManagerNoticeTransportError("ExManager Notice returned invalid JSON") from None
        if not isinstance(value, Mapping):
            raise ExManagerNoticeTransportError("ExManager Notice returned an invalid object")
        return value

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {"Authorization": f"Bearer {self._bearer}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._base_url + path, data=data, headers=headers, method=method)
        return self._open(request)

    def readiness(self) -> Mapping[str, Any]:
        result = self._request("GET", "/internal/notice/readiness")
        if (result.get("ok") is not True or result.get("accountId") != SENDER_ID
                or result.get("username") != USERNAME or result.get("peerId") != CHAT_ID
                or result.get("peerAddressable") is not True):
            raise ExManagerNoticeTransportError("ExManager Notice sender or destination is not ready")
        return {"ok": True, "accountId": SENDER_ID, "username": USERNAME,
                "peerId": CHAT_ID, "peerAddressable": True}

    @staticmethod
    def _receipt(result: Mapping[str, Any]) -> dict[str, Any]:
        if result.get("ok") is not True or result.get("outcome") != "accepted":
            raise ExManagerNoticeTransportError("ExManager Notice receipt is invalid")
        sender_id = str(result.get("senderId") or "")
        chat_id = str(result.get("chatId") or "")
        raw_message_id = result.get("messageId")
        raw_accepted_at = result.get("acceptedAt")
        if (sender_id != SENDER_ID or chat_id != CHAT_ID
                or isinstance(raw_message_id, bool)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", str(raw_message_id or ""))
                or isinstance(raw_accepted_at, bool)
                or not isinstance(raw_accepted_at, int)
                or raw_accepted_at <= 0):
            raise ExManagerNoticeTransportError("ExManager Notice receipt identity is invalid")
        return {"outcome": "accepted", "sender_id": sender_id, "chat_id": chat_id,
                "message_id": int(raw_message_id), "accepted_at": float(raw_accepted_at)}

    @staticmethod
    def _outcome(result: Mapping[str, Any], *, query: bool) -> dict[str, Any]:
        outcome = result.get("outcome")
        if (result.get("ok") is not True or not isinstance(outcome, str)
                or outcome not in {"accepted", "unknown", "sending", "definitive_failure", "not_found"}):
            raise ExManagerNoticeTransportError("ExManager Notice operation result is invalid")
        if outcome == "sending":
            # A claimed provider operation is uncertain; callers may query again
            # but must never submit it a second time.
            return {"outcome": "unknown"}
        if outcome == "accepted":
            return ExManagerNoticeTransport._receipt(result)
        if outcome == "not_found" and not query:
            raise ExManagerNoticeTransportError("ExManager Notice submit returned an invalid outcome")
        if outcome == "definitive_failure":
            error_code = str(result.get("errorCode") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error_code):
                raise ExManagerNoticeTransportError("ExManager Notice failure code is invalid")
            return {"outcome": outcome, "error_code": error_code}
        return {"outcome": str(outcome)}

    def submit_question(self, request_id: str, operation_id: str, text: str) -> dict[str, Any]:
        result = self._request("POST", "/internal/notice/questions", {
            "requestId": request_id, "operationId": operation_id, "chatId": CHAT_ID, "text": text,
        })
        return self._outcome(result, query=False)

    def query_question(self, request_id: str, operation_id: str) -> dict[str, Any]:
        path = "/internal/notice/questions/" + urllib.parse.quote(request_id, safe="") + "/" + urllib.parse.quote(operation_id, safe="")
        result = self._request("GET", path)
        return self._outcome(result, query=True)


def render_task_question(request: Mapping[str, Any]) -> str:
    """Render every exact allowed value for a reply-based question."""
    message = str(request.get("message") or "").strip()
    choices = request.get("choices")
    if not message or not isinstance(choices, list) or not 2 <= len(choices) <= 4:
        raise ExManagerNoticeTransportError("task question content is invalid")
    lines = [f"{choice['value']} — {choice['label']}" for choice in choices
             if isinstance(choice, Mapping) and str(choice.get("value") or "").strip()
             and str(choice.get("label") or "").strip()]
    if len(lines) != len(choices):
        raise ExManagerNoticeTransportError("task question choices are invalid")
    text = message + "\n\nОтветьте точным значением:\n" + "\n".join(lines)
    if len(text) > 4000:
        raise ExManagerNoticeTransportError("task question exceeds the Telegram text limit")
    return text
