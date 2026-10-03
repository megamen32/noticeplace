"""Short self-documenting producer contract shared by HTTP and MCP."""

from __future__ import annotations

from typing import Any


MCP_INSTRUCTIONS = (
    "Автоматические аварии и восстановления отправляйте только через Notice Place. "
    "Вызовите noticeplace_instructions перед первой интеграцией; обычные вопросы человеку "
    "задавайте через ask_human. Текст уведомления должен кратко назвать сервис, поломку, "
    "влияние, текущее состояние и требуемое действие — по-русски и без сырых машинных полей."
)


def noticeplace_instructions() -> dict[str, Any]:
    """Return the concise, secret-free integration contract."""
    return {
        "schema": "noticeplace.instructions.v1",
        "summary": "Автоматические аварии и восстановления отправляются через Notice Place, а не напрямую в Telegram, notify или AskHuman.",
        "message_format": [
            "Кто сломался: понятное название сервиса.",
            "Как сломался и чем это мешает.",
            "Что происходит сейчас и нужно ли действие человека.",
        ],
        "api": {
            "instructions": "GET /v1/instructions",
            "create_or_update": "POST /v1/events с Bearer-токеном проекта и Idempotency-Key",
            "recovery": "Для health-инцидента сначала POST .../health/verification, затем POST .../health/resolve.",
        },
        "automatic_repair": (
            "Для кнопок «Наблюдать / Исправить / Проверить» отправьте health.degraded "
            "с source_id, host_id, signal_type и correlation_id. Токен проекта должен разрешать "
            "agent job health-diagnosis и быть зарегистрирован в callback-карте; выбранный план "
            "выполняет health-remediation."
        ),
        "identity": "dedup_key стабилен для одной поломки; Idempotency-Key повторяется только при точном повторе того же запроса.",
        "exceptions": "Прямой аварийный канал допустим только для независимого сторожа самого Notice Place.",
    }
