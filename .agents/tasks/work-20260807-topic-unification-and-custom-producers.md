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
