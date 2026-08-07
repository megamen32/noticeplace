# Unified topic editor

Status: work

## Original request

Сделать на сайте единый красивый flow создания и редактирования всех Telegram-топиков: preset и custom должны быть одинаковыми объектами и использовать одну форму.

## Objective

Provide one live topic registry and one admin create/edit form for preset and custom Telegram forum topics.

## Business canary

The protected admin page shows the existing Emergency/Important/Log topics and custom topics in one table; saving a preset or creating a custom topic updates the live Telegram destination without restarting Notify.

## Confirmed scope

- One topic model with name, key, chat ID, forum topic ID, active flag, and preset marker.
- One create/edit form and live SQLite persistence.
- Existing env routes remain the migration fallback.

## Explicit exclusions

- Deletion removes Notify routing only; Telegram forum history is not deleted.
- No changes to the unrelated inactive-mode route test.

## Initial estimate (immutable)

- Optimistic: 45 active minutes
- Likely: 90 active minutes
- Pessimistic: 180 active minutes

## Initial plan (Russian)

1. Унифицировать модель topic и live route lookup.
2. Добавить единый create/edit UI для preset и custom.
3. Проверить protected admin flow и доставку без restart.

## Implementation progress (English)

- Added a unified live topic registry backed by `runtime_settings.telegram_topics_json`.
- Preset and custom topics now share one model and one protected admin create/edit flow with name, chat, topic ID, and active state.
- Existing environment routes remain the fallback; TelegramSender reads the registry on each send.
- Optional Telegram forum creation and topic rename use the Bot API when credentials and auto-create are enabled.
- Any topic, including the initially configured three, can be edited or removed from live Notify routing; no preset distinction is exposed in the UI.
- Focused verification: `python -m pytest -q tests/test_admin_console.py tests/test_delivery_worker.py tests/test_http_api.py` -> 32 passed; Python compilation passed.

## Estimate revision

- Revised likely active time: 120 minutes. Trigger: unified topic CRUD also required live sender lookup and Telegram forum create/rename seams; evidence: implementation and focused verification completed.
