"""Telegram inline controls handled inside the NoticePlace process."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from .core import NotificationCenter, ValidationError


class TelegramActionCodec:
    """Sign compact callback data so an incident action cannot be forged."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Telegram callback secret must be at least 16 characters")
        self._secret = secret.encode()

    def encode(self, action: str, incident_id: str) -> str:
        unsigned = f"n:{action}:{incident_id}"
        signature = hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()[:12]
        return f"{unsigned}:{signature}"

    def decode(self, value: str) -> tuple[str, str] | None:
        parts = value.split(":")
        if len(parts) != 4 or parts[0] != "n":
            return None
        action, incident_id, signature = parts[1:]
        expected = self.encode(action, incident_id).rsplit(":", 1)[1]
        if not hmac.compare_digest(signature, expected):
            return None
        return action, incident_id


TelegramApi = Callable[[str, dict[str, Any]], dict[str, Any]]


def telegram_api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call one Bot API method without logging request bodies or the token."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urllib.parse.urlencode(payload).encode(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} failed")
    return result


class TelegramInteractionPoller:
    """Poll Bot API callbacks in-process; no public webhook or second daemon exists."""

    def __init__(self, center: NotificationCenter, token: str, allowed_user_ids: set[str], codec: TelegramActionCodec, api: TelegramApi | None = None) -> None:
        self._center = center
        self._token = token
        self._allowed_user_ids = allowed_user_ids
        self._codec = codec
        self._api = api or (lambda method, payload: telegram_api(token, method, payload))

    def _answer(self, callback_id: str, text: str) -> None:
        self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def _message(self, chat_id: str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:1200]})

    def _allowed(self, user_id: Any) -> bool:
        return str(user_id) in self._allowed_user_ids

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        actor_id = callback.get("from", {}).get("id")
        if not self._allowed(actor_id):
            self._answer(callback_id, "Not authorized")
            return
        parsed = self._codec.decode(str(callback.get("data") or ""))
        if parsed is None:
            self._answer(callback_id, "Invalid action")
            return
        action, incident_id = parsed
        result = self._center.apply_telegram_action(incident_id, action, f"telegram:{actor_id}")
        if action == "ask" and result["state"] != "inactive":
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id") or "")
            if chat_id:
                self._message(chat_id, f"Reply with /ask {incident_id} followed by your question.")
        self._answer(callback_id, f"{action}: {result['state']}")

    def _handle_message(self, message: dict[str, Any]) -> None:
        actor_id = message.get("from", {}).get("id")
        text = str(message.get("text") or "").strip()
        if not self._allowed(actor_id) or not text.startswith("/ask "):
            return
        _, incident_id, question = text.split(maxsplit=2) if len(text.split(maxsplit=2)) == 3 else ("", "", "")
        if not incident_id or not question:
            return
        self._center.record_telegram_ask(incident_id, f"telegram:{actor_id}", question)
        chat_id = str(message.get("chat", {}).get("id") or "")
        if chat_id:
            self._message(chat_id, "Ask recorded.")

    def poll_once(self) -> int:
        if not self._token or not self._allowed_user_ids:
            return 0
        response = self._api("getUpdates", {"timeout": 0, "allowed_updates": json.dumps(["callback_query", "message"])})
        processed = 0
        for update in response.get("result", []):
            update_id = int(update.get("update_id", -1))
            if update_id < 0 or not self._center.claim_telegram_update(update_id):
                continue
            if isinstance(update.get("callback_query"), dict):
                self._handle_callback(update["callback_query"])
            elif isinstance(update.get("message"), dict):
                self._handle_message(update["message"])
            processed += 1
        return processed
