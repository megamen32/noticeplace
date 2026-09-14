# Live Notify no-ADB phone canary

Status: active

## Original request

"Ну так что, позвонишь мне разочек через новый способ без ADB, через Notify. Я проверю, что все работает."

## Objective

Deliver one user-authorized test call through Notify Center's fixed
`Notify → GPTAdmin → S21 ShellMCP → Termux:API` route, with no ADB command in
the live call path.

## Business canary

One Notify test event invokes the GPTAdmin phone adapter, GPTAdmin reports the
fixed Android command succeeded, and the user receives the cellular call.

## Confirmed scope

- Configure and validate only the existing fixed command adapter.
- Use the existing phone recipient configured on S21; do not accept a supplied
  number as request data.
- Restart only the Notify service if its dedicated GPTAdmin configuration
  changes.

## Explicit exclusions

- No arbitrary ShellMCP commands or generic shell adapter.
- No ADB command in the production call path.
- No public tunnel, credential disclosure, account changes, or source edits.

## Initial estimate

- Optimistic: 12 active minutes.
- Likely: 30 active minutes.
- Pessimistic: 65 active minutes.

## Initial plan

1. Проверить готовность Hub, ShellMCP, fixed Termux-команды и Notify-конфига без раскрытия токенов.
2. Настроить ровно dedicated adapter; до звонка доказать, что он отправляет только immutable command.
3. Отправить один тестовый Notify-event уровня Emergency и проверить receipt в Hub и факт звонка у пользователя.
4. Зафиксировать результат и rollback для конфигурации.

## Progress

- 2026-08-02: Started live readiness audit. Notify source adapter is committed, but deployed service still has only the legacy Android ADB variables; live no-ADB route is not configured yet.
