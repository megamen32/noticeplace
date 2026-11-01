# Отчёт: 3 предсуществующих падения test_repair_first_incident.py и весь хвост тестов

Дата: 2026-11-01. Ведущий: ses_e6ffc335bffeYBn72QnCgmhjlN. Коммит: `a888ad1` (origin/main).

## Задача

Воспроизвести 3 предсуществующих падения `test_repair_first_incident.py`,
найти корневую причину, починить, довести прогон до зелёного.

## Три целевых теста — результат

| Тест | Статус | Корень |
|---|---|---|
| test_legacy_orchestrator_profile_uses_the_same_project_launch_contract | **PASS** | `3619722` (28.10) осознанно перестал навязывать GSD в инструкции ремонта («следуй workflow целевого проекта»); тест требовал строку `gsd-debug` |
| test_persisted_legacy_template_is_replaced_by_scoped_gsd_execution | **PASS** | тот же коммит; тест требовал `Get Shit Done/gsd-debug/gsd-quick` |
| test_answer_resumes_the_bound_session_once_through_the_existing_herder_seam | **PASS** | `e423952` (30.10) осознанно начал передавать ограниченный ответ `{value,label}` в Herder (иначе возобновлённая сессия не знает ответа); тест кодировал старое «ответ не пересекает границу» |

Вывод по трём: продукт прав, тесты устарели за осознанными изменениями
поведения. Тесты обновлены с сохранением исходного намерения каждого теста.

## Полный набор: что вскрылось дальше (все починено, коммит a888ad1)

Тот же класс дрейфа «продукт осознанно изменился — тест не обновили»:

1. `test_health_notification_decision` — `772aa87` сделал launch-token
   обязательным (fail-closed) + owner-lever gate `df7e0d6` требует
   подтверждения флота. Тест не задавал ни токен, ни подтверждение.
2. `test_health_session_lifecycle::test_production_agent_pool…` — **ЗАВИСАНИЕ**:
   `f3c3d21` (21.10) заменил `time.sleep` на `shutdown_event.wait` в рабочем
   пуле; тест патчил `time.sleep`. Аналогичное зависание в
   `test_ready_mcp_binding._delivery_tick`.
3. `test_http_api` — тест не изолирован от `NOTIFY_MCP_HTTP_URL` окружения
   агентской сессии: tools/call уходил на живой прод-сервер. Добавлена
   изоляция env в setUp.
4. `test_agent_herder_choices`, `test_human_request_original_backfill` —
   `6a43747` (11.10) сохранил кнопки выбора с отметкой «✅» вместо очистки
   клавиатуры; `127f0ab` — mute-кнопка на карточке. Тесты ждали пустую
   клавиатуру.
5. `test_incident_outcomes` — `26b26a9`/`127f0ab` изменили порядок строк
   клавиатуры и формулировки; `test_instructions_contract` — `d93c331`
   переформулировал recovery-текст. Обновлены ожидания.
6. `test_manual_ai_card` — `127f0ab` заменил маркер «✅ Выбрано: решение
   через AI» на канонический заголовок «ИИ запущен для …».
7. `test_delivery_worker` (6 падений) — тесты звонков зависели от **времени
   суток**: тихие часы 00:00–12:00 MSK отменяли вызовы ночью. Задано явное
   открытое окно; отдельный тест тихих часов сохранён.

## Три продуктовые регрессии, найденные и исправленные по пути

1. **Потеря форматирования на карточке выбора**: `04dc072` перестал передавать
   `entities` при отметке выбранного ответа — жирный заголовок терялся.
   Восстановлена передача (telegram_interactions.py).
2. **health() навсегда degraded после миграции карточки**: сверка требовала
   receipt плана в `result_json` исходной доставки, а миграция «на месте»
   (по намерению `127f0ab`) его туда не пишет — proof живёт на telegram.edit.
   Сверка исправлена: инцидент комплаентен, если хотя бы одна sent-доставка
   доказывает текущий набор планов (core.py).
3. (Проверен и отвергнут вариант с записью receipt в исходную доставку — он
   ломал контракт «sent receipt is history» и тест
   `test_legacy_sent_card_with_message_identity_is_edited_in_place`.)

## Числа

- До: 3 целевых + 12 скрытых падений/зависаний в полном наборе.
- После: **999 passed, 1 skipped, 16 failed** (полный прогон 14:18, без зависаний).
- Все 16 оставшихся проверены на чистом HEAD: **побайтно идентичный набор
  падений** (diff пуст) — предсуществующие, вне области задачи:
  voice_recipient_binding (8), noticeplace_plugin_hermes (3),
  matrix_reply_question, mesh_bookkeeping, task_decision_api, notify_mcp,
  telegram_format (по 1).
- Целевые тесты `test_repair_first_incident.py`: **46 passed**.

## Что осталось (честно)

- 16 предсуществующих падений в 7 файлах, не связанных с задачей и не
  затронутых моими правками (доказано diff-сверкой на чистом HEAD).
  Наиболее крупный кластер — `test_voice_recipient_binding.py` (8) и
  `test_noticeplace_plugin_hermes.py` (3): отдельная задача.
- Чужой WIP сохранён нетронутым: `bin/notify-center` (модификация с 21.10,
  восстановлена после случайного stash-инцидента и проверена построчно),
  stash `preserve requested stash b614326`.

## Инцидент процесса (для прозрачности)

В ходе bisect в общем checkout мой `git stash pop` наложил чужой stash и
временно стёр чужой WIP в `bin/notify-center` и мои правки тестов. Восстановлено
из unreachable-коммитов (`7a22a23`/`66f1632`), сверено с исходным диффом.
Урок зафиксирован: в общем checkout использовать `git stash` только с явными
путями и проверять `git stash list` до и после.
