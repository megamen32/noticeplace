"""One receipt-driven updater for a selected Notice Place Telegram card.

Telegram button presses and any other resolver (voice, phone, API) end in the
same durable selection receipt and therefore update the same message. The
canonical accepted ``choice_id`` from the receipt wins over a later requested
option, so a conflicting repeat never re-marks the card with another answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
from typing import Any, Mapping

_SELECTED_PREFIX = "✅ Выбрано:"
_CHOICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def canonical_choice_ref(value: str) -> str:
    """Return a stable, bounded ASCII identity for any accepted answer.

    Option ids stay verbatim. A free-form answer has no such id, so its receipt
    carries a deterministic opaque reference while the human-readable label
    stays in the receipt and on the card.
    """
    text = str(value or "").strip()
    if _CHOICE_ID_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"answer-{digest}"


def selected_marker(label: str, prefix: str = _SELECTED_PREFIX) -> str:
    """Render the single accepted-answer marker for a card."""
    text = " ".join(str(label or "").replace("\x00", "").splitlines()).strip()
    return f"{prefix} {text[:128]}"


def selection_keyboard(buttons: list[Mapping[str, Any]], choice_id: str) -> dict[str, Any]:
    """Retain each signed callback and mark only the canonical option."""
    return {"inline_keyboard": [[{
        "text": (("✅ " if str(button["choice_id"]) == choice_id else "") + str(button["label"]))[:80],
        "callback_data": str(button["callback_data"]),
    }] for button in buttons]}


def selected_card_text(base_text: str, label: str, prefix: str = _SELECTED_PREFIX) -> str:
    """Append the accepted marker once, keeping the message inside Telegram limits."""
    marker = selected_marker(label, prefix)
    lines = str(base_text or "").rstrip().splitlines()
    while lines and lines[-1].startswith(prefix):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    base = "\n".join(lines).rstrip()
    limit = 4096 - len(marker.encode("utf-16-le")) // 2 - 2
    if len(base.encode("utf-16-le")) // 2 > max(0, limit):
        base = base.encode("utf-16-le")[:max(0, limit) * 2].decode("utf-16-le", errors="ignore").rstrip()
    return f"{base}\n\n{marker}" if base else marker


def _bounded_entities(entities: Any, text: str) -> list[dict[str, Any]] | None:
    if not isinstance(entities, list):
        return None
    limit = len(text.encode("utf-16-le")) // 2
    return [
        entity for entity in entities
        if isinstance(entity, dict)
        and isinstance(entity.get("offset"), int)
        and isinstance(entity.get("length"), int)
        and 0 <= entity["offset"] < entity["offset"] + entity["length"] <= limit
    ]


def _unchanged(error: BaseException) -> bool:
    """Detect Telegram's identical-edit rejection without inventing a retry."""
    description = str(error)
    if isinstance(error, urllib.error.HTTPError) and error.code == 400:
        try:
            failure = json.loads(error.read(4096))
            if isinstance(failure, dict) and failure.get("ok") is False:
                description = str(failure.get("description") or "")
        except (ValueError, AttributeError):
            pass
    return description.lower().startswith("bad request: message is not modified")


def apply_selected_card(
    api: Any,
    *,
    chat_id: str,
    message_id: Any,
    base_text: str,
    label: str,
    reply_markup: Mapping[str, Any] | None = None,
    entities: Any = None,
) -> dict[str, Any]:
    """Mark one accepted choice on one card with its supplied keyboard.

    Returns a receipt with ``edited`` so a replayed resolver can stay silent.
    A rejected text edit falls back to the same keyboard-only update. Choice
    callbacks remain visible; canonical completion owns their repeat handling.
    """
    if not str(chat_id or "") or not isinstance(message_id, int) or message_id <= 0:
        return {"edited": False, "reason": "card_is_not_bound"}
    text = selected_card_text(base_text, label)
    payload: dict[str, Any] = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "text": text[:4096],
        "reply_markup": json.dumps(
            dict(reply_markup or {"inline_keyboard": []}), ensure_ascii=False, separators=(",", ":")
        ),
    }
    bounded = _bounded_entities(entities, payload["text"])
    if bounded is not None:
        payload["entities"] = json.dumps(bounded, ensure_ascii=False)
    try:
        response = api("editMessageText", payload)
    except Exception as error:
        if _unchanged(error):
            # A replayed resolver renders the same accepted answer; Telegram
            # rejects the identical edit, which already proves the marker.
            return {"edited": True, "text": text, "reason": "already_marked"}
        api("editMessageReplyMarkup", {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "reply_markup": payload["reply_markup"],
        })
        return {"edited": False, "text": text, "reason": "keyboard_only_retry"}
    edited = response.get("result") if isinstance(response, dict) else None
    matched = (
        isinstance(response, dict) and response.get("ok") is True
        and isinstance(edited, dict) and edited.get("message_id") == message_id
        and str(edited.get("chat", {}).get("id")) == str(chat_id)
    )
    return {"edited": matched, "text": text}


def apply_selection_receipt(
    center: Any,
    api: Any,
    *,
    request_id: str,
    incident_id: str,
    choice_id: str,
    label: str,
    actor: str,
    source: str,
    base_text: str = "",
    reply_markup: Mapping[str, Any] | None = None,
    choice_buttons: list[Mapping[str, Any]] | None = None,
    entities: Any = None,
    chat_id: str = "",
    message_id: Any = None,
) -> dict[str, Any]:
    """Record the canonical answer once and render it on the bound card.

    The stored receipt decides the marker text: a conflicting repeat reports
    ``conflict`` together with the already accepted choice and never edits the
    card a second time.
    """
    receipt = center.record_selection_receipt(
        request_id=request_id,
        incident_id=incident_id,
        choice_id=choice_id,
        label=label,
        actor=actor,
        source=source,
        chat_id=str(chat_id or ""),
        message_id=message_id if isinstance(message_id, int) and message_id > 0 else None,
    )
    card = center.selection_receipt_card(request_id)
    result: dict[str, Any] = {"selection": receipt, "card": None}
    if card is None:
        return result
    result["card"] = apply_selected_card(
        api,
        chat_id=card["chat_id"],
        message_id=card["message_id"],
        base_text=base_text,
        label=str(card["receipt"]["label"]),
        reply_markup=(selection_keyboard(choice_buttons, str(card["receipt"]["choice_id"]))
                      if choice_buttons is not None else reply_markup),
        entities=entities,
    )
    return result
