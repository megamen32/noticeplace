# Notify active modes and Telegram topics

Status: work

## Original request

Убрать обязательные стандартные предустановленные режимы и сделать так, чтобы бот автоматически создавал в Telegram-группе топики только для активированных режимов. Сейчас пользователю нужны `emergency`, `important` и `log`.

## Objective

Make active notification modes operator-configurable per project and reconcile the configured mode set with Telegram forum topics, preserving unrelated routes and existing incident delivery semantics.

## Business canary

After applying a project configuration with exactly `emergency`, `important`, and `log`, the Telegram group contains/reuses exactly those mode topics, and a notification for each active mode routes to its matching topic; inactive modes create no new topic and do not receive delivery.

## Confirmed scope

- Replace the fixed active preset assumption with an explicit active-mode set.
- Keep the mode catalog extensible; initially configure only `emergency`, `important`, and `log`.
- Reconcile Telegram forum topics through the existing bot/adapter seam, reusing existing topics where possible.
- Add focused tests for mode configuration, topic reconciliation, and routing.

## Explicit exclusions

- No deletion of existing Telegram topics without an explicit cleanup policy.
- No changes to consumer-chain semantics from the previous task.
- No production restart or live Telegram mutation until the code canary and a separate apply approval pass.

## Initial estimate (immutable)

- Optimistic: 90 active minutes
- Likely: 180 active minutes
- Pessimistic: 300 active minutes

## Initial plan (Russian)

1. Найти текущий каталог режимов, хранение активных режимов и Telegram topic-management seam.
2. Добавить явный список активных режимов и reconcile топиков без удаления старых.
3. Добавить красные тесты, реализовать зелёный путь и проверить маршрутизацию `emergency`, `important`, `log`.

## Execution log (English)

- 2026-08-07: Confirmed `log` is an event-kind mode (`event.kind == "log"`), while `important` and `emergency` remain severity modes. The active deploy set is `emergency`, `important`, and `log`.
- Added `telegram_active_modes()` with a small allowlisted catalog, mode-aware routing, inactive-mode cancellation before Telegram send, and an idempotent `TelegramTopicManager` that reuses known thread IDs and creates only missing active topics.
- Added persisted topic-state reconciliation and a Bot API `createForumTopic` seam. Deployment config now opts into auto-topic creation and the active three-mode set; no live restart or Telegram mutation was performed.
- Red evidence: `test_telegram_topics.py` initially failed to import the not-yet-implemented topic manager. Green evidence: topic tests 4 passed, delivery worker 13 passed, Telegram controls 3 passed, HTTP API 11 passed, scoped compile and diff-check passed.
