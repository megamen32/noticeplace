"""Edit analysis progress on the immutable HumanRequest Telegram card."""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Mapping

from .human_request_original import render_human_request_original


def progress_keyboard(request: Mapping[str, Any]) -> dict[str, Any]:
    """Retain the session link after completion without decision callbacks."""
    progress = request.get("progress") or {}
    url = progress.get("session_url")
    return {"inline_keyboard": [[{"text": "Открыть сессию", "url": url}]] if url else []}


def edit_progress_card(request: Mapping[str, Any], api: Any) -> None:
    """Retry-safe text edit, checked against the original card's receipt."""
    chat_id, message_id = str(request["telegram_chat_id"]), int(request["telegram_message_id"])
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": render_human_request_original(request)[0],
               "reply_markup": json.dumps(progress_keyboard(request), ensure_ascii=False)}
    try:
        response = api("editMessageText", payload)
    except Exception as error:
        description = str(error)
        if isinstance(error, urllib.error.HTTPError) and error.code == 400:
            try:
                failure = json.loads(error.read(4096))
                if failure.get("ok") is False and failure.get("error_code") == 400:
                    description = str(failure.get("description") or "")
            except (ValueError, AttributeError):
                pass
        if not description.lower().startswith("bad request: message is not modified"):
            raise
        return
    edited = response.get("result") if isinstance(response, dict) else None
    if (not isinstance(response, dict) or response.get("ok") is not True
            or not isinstance(edited, dict) or edited.get("message_id") != message_id
            or str(edited.get("chat", {}).get("id")) != chat_id):
        raise RuntimeError("Telegram progress edit returned a mismatched card receipt")
