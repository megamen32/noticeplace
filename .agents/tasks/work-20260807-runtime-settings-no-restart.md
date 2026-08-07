# Notify runtime settings without restart

Status: work

## Original request

Сделать остальные настройки Notify регулируемыми без перезапуска; перезапуск для оперативного переключателя звонков неудобен.

## Objective

Move live notification policy controls from startup-only systemd environment values into durable runtime settings read by the active Notify worker.

## Business canary

Changing automatic-call state and escalation intervals in admin changes the next queued/scheduled delivery behavior without restarting `notification-center.service`; text routing remains available.

## Confirmed scope

- Durable runtime settings in the Notify database.
- Admin UI writes settings atomically and cancels future call jobs when disabled.
- Delivery worker reads current call/repeat settings during scheduling and delivery.
- One migration restart only to load the new runtime-aware code; subsequent changes are restart-free.

## Explicit exclusions

- No hot-patching of already executing adapter calls.
- No removal of existing environment settings until runtime parity is verified.

## Initial estimate (immutable)

- Optimistic: 60 active minutes
- Likely: 120 active minutes
- Pessimistic: 240 active minutes

## Initial plan (Russian)

1. Добавить runtime settings и тесты красного пути.
2. Перевести toggle звонков и escalation worker на живые значения из SQLite.
3. Проверить без restart на тестовом worker, затем выполнить один migration restart.

## Implementation progress (English)

- Added durable `runtime_settings` SQLite storage and admin persistence for automatic-call enablement plus Matrix/Android/Telegram escalation timers.
- DeliveryWorker now reads the live values while scheduling and before dispatching call channels; disabling calls cancels queued work and prevents a claimed race from reaching the adapter.
- Added focused tests for no-restart call disable, live phone delay, and admin timer persistence.
- Focused verification: `python -m pytest -q tests/test_delivery_worker.py tests/test_admin_console.py tests/test_notification_center.py` -> 31 passed.
- Full verification: 112 passed, 1 unrelated pre-existing routing-policy failure in `tests/test_telegram_controls.py`; recorded in `todo-20260807-telegram-route-test-drift.md`.

## Estimate revision

- Revised likely active time: 150 minutes. Trigger: added the requested website editor for all live call/repeat timers and regression coverage; evidence: implementation and focused verification completed.
