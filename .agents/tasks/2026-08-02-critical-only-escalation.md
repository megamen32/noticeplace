# Задача: привести эскалации Notify Center к Critical-only

## Исходный запрос

«…баны fail2ban это не то чтобы ультра важно… подтверждаю только Critical и только он мне звонит если я его не подтверждаю. Important — не обязательно подтверждать. какие вообще у нас сейчас уровни?»

## Цель

Сделать так, чтобы только `critical` требовал подтверждения и мог эскалироваться в звонок; `important` доставляется как обычное уведомление без обязательного ACK и без звонка.

## Бизнес-канарейка

Реальные/контрактные critical и important incidents подтверждают: only critical получает кнопки подтверждения/возможную escalation; important не создаёт call delivery и не остаётся в обязательном pending state.

## Подтверждённый scope

- Notify Center event severities, Telegram presentation, ACK policy, Matrix/Android call scheduling.
- Existing fail2ban producer classification only after policy evidence.

## Явные исключения

- Менять телефонию/Matrix transport, номера, VPN2 watchdog и несвязанные producer semantics.

## Оценка (неизменяемая)

- optimistic: 10 active minutes
- likely: 22 active minutes
- pessimistic: 45 active minutes

## Первоначальный план

1. Инвентаризировать уровни и действующие условия Telegram/Matrix/Android escalation.
2. Сопоставить fail2ban и другие producers с уровнями, показать пользователю фактическую политику.
3. Внести минимальную Critical-only политику с регрессионными тестами.
4. Проверить queue/audit и отсутствие звонков для Important.

## Execution log

- Confirmed current severity order: `debug`, `info`, `notice`, `important`, `critical`, `emergency`.
- Confirmed live calls were already exact-critical-only; fail2ban producer scopes are capped at `important`. The UI, however, exposed ACK and Snooze controls on every severity.
- Added and deployed a critical-only Telegram control contract: all severities retain Ask; only exact critical adds ACK and Snooze. Emergency remains non-calling per the explicit policy.
- Full suite passed: 54 tests. A real unique important queue canary delivered only `telegram.main:sent`, created zero call deliveries, and was resolved. Published as `f783786`.
