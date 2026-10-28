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
                "project, recipient, dedup_key. Для обычных инцидентов отдельных /health/* эндпоинтов НЕТ. "
                "Health-инцидент закрывает исходный независимый watchdog/source, а не исполнитель. "
                "Исполнитель публикует work-finished: попытка завершена, инцидент остаётся открыт. "
                "Источник проверяет актуальное состояние; degraded продолжает ту же сессию, "
                "unknown оставляет проверку ожидающей и не запускает нового агента."
            ),
            "work_finished": "POST /v1/incidents/{id}/health/work-finished + Idempotency-Key: attempt_id, session_id, harness, evidence_refs. Не закрывает инцидент; возвращает request_id, generation, verification_due.",
            "source_check": "GET /v1/verification-requests?project=...&source_id=...; POST /v1/incidents/{id}/health/check-result + Idempotency-Key: request_id, generation, observed_state=healthy|degraded|unknown, evidence_refs. Право источника задаётся в серверном NOTIFY_CENTER_TOKENS_JSON: principal_id, verification_sources=[source_id], incident_role=verifier; отдельный executor credential с incident_role=executor не вправе проверять или закрывать.",
            "health_identity": "consumer_id из серверного scoped incident (иначе project+source_id), host_id, problem_type (иначе signal_type); correlation_id определяет эпизод. Legacy recovery без generation допускается только без неоднозначности: при repeated degraded нужно observed_at позже последнего измерения; после work_finished обязателен актуальный request_id/generation.",
            "disabled_notifications": "GET /v1/mutes?project=...; включить обратно: POST /v1/incidents/{id}/unmute.",
            "human_request_progress": "POST /v1/human-requests/{request_id}/progress с Bearer-токеном исходного проекта; phase=accepted|running|completed|failed; session_url и message необязательны. Обновляет ту же карточку после выбора deep_analysis.",
            "pending_questions": "GET /v1/human-requests?state=pending — краткий текст вопроса, варианты ответа и точная нативная сессия, в которую вернётся ответ.",
            "answer_question": "POST /v1/human-requests/{request_id}/resolve с Bearer-токеном проекта; value=принятый ответ. Ответ отмечает галочкой ту же карточку.",
            "answer_choice": "POST /v1/incidents/{incident_id}/choice-result с Bearer-токеном проекта; request_id и choice_id из карточки выбора. Возвращает канонический choice_id, label и результат правки карточки.",
            "incident_progress": "POST /v1/incidents/{incident_id}/health/progress и /health/verification с Idempotency-Key; обязательны реальные evidence_refs, а для проверки — source_id, verifier_id, verification_id, observed_state.",
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
            "Стандартный путь — repair-first: одна сессия OpenCode по подписке чинит до восстановления "
            "потребителя под автопилотом Agent Herder и оставляет проверенный итог; отдельный "
            "планировщик и обязательная тройка планов больше не требуются. "
            "Вопрос оператору задаёт сама эта сессия через Agent Herder human_request_create, "
            "привязанный к её же нативной сессии: ответ возвращается туда же, а та же карточка "
            "получает галочку принятого варианта. Кнопки выбора «Наблюдать / Исправить / Проверить» "
            "остаются только как совместимость для старых карточек."
        ),
        "identity": "dedup_key стабилен для одной поломки; Idempotency-Key повторяется только при точном повторе того же запроса.",
        "exceptions": "Прямой аварийный канал допустим только для независимого сторожа самого Notice Place.",
    }
