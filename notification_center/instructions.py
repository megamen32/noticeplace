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
            "event_contract": (
                "JSON-событие: schema='notify.event.v1'; kind ∈ {incident, notification, audit, log} "
                "(другие kind отклоняются); severity ∈ {debug, info, notice, important, critical, emergency}; "
                "обязательны project, recipient, title, body, dedup_key. "
                "Health-карточка — это kind='incident' + event_type='health.degraded' "
                "(НЕ kind='health.degraded')."
            ),
            "recovery": (
                "Закрытие инцидента: POST /v1/events с schema='notify.event.v1', action='resolve', "
                "project, recipient, dedup_key. Отдельных /health/* эндпоинтов НЕТ. "
                "Health-инциденты (event_type='health.*') источник закрыть не может: карточка "
                "закрывается выбором плана по кнопке и независимой верификацией — источник получит "
                "400 'requires explicit plan selection'; не ретраить бесконечно."
            ),
            "disabled_notifications": "GET /v1/mutes?project=...; включить обратно: POST /v1/incidents/{id}/unmute.",
            "human_request_progress": "POST /v1/human-requests/{request_id}/progress с Bearer-токеном исходного проекта; phase=accepted|running|completed|failed; session_url и message необязательны. Обновляет ту же карточку после выбора deep_analysis.",
        },
        "telegram_controls": (
            "Ответьте обычным reply на карточку — ответ привяжется к инциденту, а для health-инцидентов "
            "ещё и уйдёт прямой доставкой в живую сессию агента в Agent Herder (агент увидит его следующим ходом). "
            "После нажатия AI в карточке появляется галочка выбора, а после создания сессии — "
            "ссылка на её ход работы в Agent Herder и кнопка «Открыть сессию». "
            "«Отключить такие» глушит только тот же проект и dedup_key; "
            "на этой же карточке появится «Включить обратно»."
        ),
        "automatic_repair": (
            "Health-карточка: POST /v1/events с kind='incident', event_type='health.degraded', "
            "severity='critical'|'emergency' и обязательными source_id, host_id, signal_type, "
            "correlation_id — они включают автоматический health-diagnosis. Токен проекта должен "
            "разрешать agent job health-diagnosis и быть зарегистрирован в callback-карте. "
            "Дальше ИИ решает сама: уверенный рекомендованный план запускается автоматически "
            "(карточка покажет «ИИ выбрала план — исправление запущено»), а три кнопки планов "
            "«Наблюдать / Исправить / Проверить» показываются только когда ИИ не может решить без человека. "
            "Сессия исправления работает под автопилотом Agent Herder: по концу хода судья "
            "продолжает работу, либо сообщает итог (устранено / не устранено / нужно решение человека) "
            "через Notice Place. Источник закрыть health-инцидент не может (см. recovery)."
        ),
        "identity": "dedup_key стабилен для одной поломки; Idempotency-Key повторяется только при точном повторе того же запроса.",
        "exceptions": "Прямой аварийный канал допустим только для независимого сторожа самого Notice Place.",
    }
