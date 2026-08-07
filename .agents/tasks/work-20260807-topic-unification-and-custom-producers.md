# Unified Telegram topics and custom producer topics

Status: work

## Original request

Перезапустить Notify, оставить в общей супергруппе обычные топики `Emergency`, `Important`, `Log`, направлять всё остальное в `Log`, удалить неактивные старые топики и создавать отдельный обычный топик для каждого кастомного consumer/producer.

## Objective

Activate the unified topic model in `LogsNotifications`: three default modes plus ordinary custom producer topics in the same forum, with inactive legacy topics removed only after exact target verification.

## Business canary

After restart, a new emergency event lands in `Emergency`, an important event in `Important`, every other standard event in `Log`, and a custom producer event lands in that producer's own topic.

## Confirmed scope

- Restart the Notify service after verifying the staged configuration.
- Reconcile the three active default topics and custom producer topics in the same Telegram supergroup.
- Remove only exact inactive legacy topics after a read-only inventory identifies them.
- Verify routing with a bounded business canary and service evidence.

## Explicit exclusions

- No deletion of a topic whose identity/name cannot be proven to be inactive or legacy.
- No deletion of custom producer topics.
- No changes to Matrix, phone, or consumer-chain delivery semantics.

## Initial estimate (immutable)

- Optimistic: 45 active minutes
- Likely: 90 active minutes
- Pessimistic: 180 active minutes

## Initial plan (Russian)

1. Проверить фактические env/routes/state и доступный Telegram topic inventory.
2. Применить reconcile общей модели и удалить только подтверждённые неактивные топики.
3. Перезапустить сервис и проверить emergency/important/log плюс custom producer topic.

## Execution log (English)

- 2026-08-07: Read-only production inspection found the old `/etc` route file and no topic state. The first restart failed because `TELEGRAM_CHAT_ID` was a personal chat while forum routes use `-1004322359393`; fixed topic-chat selection to prefer active route chat IDs.
- Restart succeeded after the fix. `/var/lib/notification-center/telegram-topics.json` now records `Emergency=7`, `Important=5`, `Log=122`; Notify health is `ok`, storage and dispatcher are ready, and the outbox is empty.
- Deleted exact confirmed inactive legacy topics `notice=4` and `critical=6` through Telegram Bot API. Unknown topics were not guessed or deleted; Telegram Bot API did not provide a topic inventory endpoint in this path.
- Applied the active route file to `/etc`, restarted `notification-center.service` again, and restarted `notification-center-admin.service` so custom producer topic creation code is loaded. No custom producer was created during this canary, so its live topic creation remains unexercised.
