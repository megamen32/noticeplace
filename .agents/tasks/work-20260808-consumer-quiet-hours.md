# Per-consumer quiet hours

Status: work

## Original request

Добавить тихие часы отдельно под каждую вещь: пока для всех consumer’ов
`01:00–09:00`, сообщения продолжать отправлять, звонки не выполнять.

## Objective

Persist quiet-hour policy per consumer and defer only call deliveries during
the configured window, without introducing a global quiet-hours rule.

## Business canary

A consumer message is delivered during quiet hours while its call delivery is
durably moved to 09:00 Europe/Moscow.

## Confirmed scope

- SQLite consumer policy migration and default policy.
- Delivery claim boundary for consumer call channels.
- Admin visibility and regression coverage.

## Explicit exclusions

- No global quiet-hours inheritance.
- No suppression of text messages.

## Initial estimate (immutable)

- Optimistic: 25 active minutes
- Likely: 45 active minutes
- Pessimistic: 90 active minutes
