"""Render HumanRequest originals for Telegram cards and Markdown documents."""

from __future__ import annotations

from typing import Any, Mapping


TELEGRAM_TEXT_LIMIT = 4096
ORIGINAL_PREVIEW_LIMIT = 800


def utf16_length(text: str) -> int:
    """Return the number of UTF-16 code units used by Telegram text limits."""
    return len(text.encode("utf-16-le")) // 2


def truncate_utf16(text: str, limit: int) -> str:
    """Truncate text without splitting a non-BMP Unicode character."""
    if limit <= 0:
        return ""
    used = 0
    characters: list[str] = []
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if used + width > limit:
            break
        characters.append(character)
        used += width
    return "".join(characters)


def requires_document(message: str, original_text: Any) -> bool:
    """Return whether the complete original and analysis exceed one card."""
    if not isinstance(original_text, str) or not original_text:
        return False
    return utf16_length(
        f"Исходное сообщение:\n{original_text}\n\nРазбор:\n{message}"
    ) > TELEGRAM_TEXT_LIMIT


def _resolution_text(request: Mapping[str, Any]) -> str:
    state = str(request.get("state") or "pending")
    if state == "resolved":
        response_value = request.get("response_value")
        choices = request.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                if choice.get("value") == response_value:
                    label = str(choice.get("label") or "").strip()
                    if label:
                        return f"\n\n✅ Выбрано: {label}"
        return "\n\n✅ Ответ получен."
    if state == "expired":
        return "\n\n⌛ Срок ответа истёк."
    if state == "cancelled":
        return "\n\n🚫 Запрос отменён."
    return ""


def _choice_resolution_reservation(request: Mapping[str, Any]) -> str:
    """Reserve the longest possible choice result before a callback arrives."""
    if str(request.get("state") or "pending") != "pending" or request.get("mode") != "choice":
        return ""
    choices = request.get("choices")
    if not isinstance(choices, list):
        return ""
    suffixes = []
    for choice in choices:
        if not isinstance(choice, Mapping) or choice.get("value") is None:
            continue
        label = str(choice.get("label") or "").strip()
        if label:
            suffixes.append(f"\n\n✅ Выбрано: {label}")
    return max(suffixes, key=utf16_length, default="")


def _bounded_with_suffix(text: str, suffix: str) -> str:
    """Keep a terminal status visible while bounding a Telegram card."""
    if utf16_length(text + suffix) <= TELEGRAM_TEXT_LIMIT:
        return text + suffix
    available = TELEGRAM_TEXT_LIMIT - utf16_length(suffix)
    if available <= 0:
        return truncate_utf16(suffix, TELEGRAM_TEXT_LIMIT)
    marker = "…"
    body = truncate_utf16(text, max(0, available - utf16_length(marker))).rstrip()
    return body + marker + suffix


def render_human_request_original(request: Mapping[str, Any]) -> tuple[str, str | None]:
    """Render a bounded Telegram card and an optional complete Markdown file."""
    message = str(request.get("message") or "")
    original_text = request.get("original_text")
    resolution = _resolution_text(request)
    resolution_reservation = resolution or _choice_resolution_reservation(request)

    if not isinstance(original_text, str) or not original_text:
        return _bounded_with_suffix(message, resolution), None

    inline = f"Исходное сообщение:\n{original_text}\n\nРазбор:\n{message}"
    # A terminal label is part of the visible card.  If it alone pushes an
    # otherwise fitting original over Telegram's limit, retain the complete
    # original in the Markdown document instead of truncating it inline.
    if utf16_length(inline + resolution_reservation) <= TELEGRAM_TEXT_LIMIT:
        return _bounded_with_suffix(inline, resolution), None

    document = (
        "# Полное уведомление\n\n"
        "## Исходное сообщение\n\n"
        f"{original_text}\n\n"
        "## Разбор и варианты действий\n\n"
        f"{message}{resolution}"
    )
    preview = truncate_utf16(original_text, ORIGINAL_PREVIEW_LIMIT)
    if preview != original_text:
        preview = preview.rstrip() + "…"
    prefix = (
        f"Исходное сообщение (начало):\n{preview}\n\n"
        "📎 Полное исходное сообщение и разбор приложены Markdown-файлом.\n\n"
        "Разбор:\n"
    )
    return _bounded_with_suffix(prefix + message, resolution), document
