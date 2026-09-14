# Задача: severity-policy, повторные уведомления и Telegram supergroup routing

## Исходный запрос

«…emergency звонит тогда. critical — нет, но повторяет уведомления пока не нажму ок. Important просто уведомляет. Для всего нужна своя супергруппа в ТГ, чтобы разные типы уведомлений были в разных чатах»

## Цель

Спроектировать и после выбора пользователя реализовать severity policy: emergency звонит, critical повторяет уведомления до ACK без звонка, important только уведомляет; добавить маршрутизацию уведомлений в отдельные чаты Telegram supergroup и полезный контракт Ask.

## Бизнес-канарейка

Important приходит один раз в назначенный чат; critical повторяется до ACK в назначенном чате без звонка; emergency вызывает только заданный call adapter; Ask создаёт безопасный, адресный запрос с понятным ответом.

## Подтверждённый scope

- Notify Center severity policy, durable delivery queue/retry/audit, Telegram UI and chat routing, Matrix/Android call adapters.

## Явные исключения

- Создание или изменение Telegram-чата/приглашение участников без явного подтверждения, изменение VPN2 watchdog и несвязанных producers.

## Оценка (неизменяемая)

- optimistic: 35 active minutes
- likely: 75 active minutes
- pessimistic: 150 active minutes

## Первоначальный план

1. Инвентаризировать Ask, severity state machine, queue и текущий Telegram target.
2. Подготовить три варианта routing/repeat/call policy и получить выбор.
3. После выбора добавить тесты, миграцию конфигурации и rollout.
4. Провести canary по каждому уровню и всем назначенным чатам.

## Execution log

- Current Ask is audit-only: the callback records `telegram_ask_requested`; a separate `/ask <incident_id> <question>` stores text but no LLM/job/reply lifecycle exists.
- Current queue is one initial Telegram delivery plus optional one-shot call escalations. ACK/resolve safely cancel queued/claimed delivery rows; critical recurrence must therefore create durable, uniquely keyed repeat rows atomically.
- Current Telegram sender uses one configured chat ID and cannot route to a supergroup topic (`message_thread_id`). Important is already call-free; emergency currently has no call because predicates are exact critical-only.
- User selected policy values: fail2ban emits notice; important is single-shot; critical repeats every 10 minutes and calls after 60 minutes without ACK; emergency calls after 10 minutes without ACK. Call adapter and Telegram supergroup identifiers remain material external choices.
- Implemented and deployed the selected queue policy: Matrix-after-deadline then S21-on-unanswered, durable unique critical repeat rows, and `notice` fail2ban producer. Full suite: 59 tests. Live critical canary created 601s repeat and 3601s Matrix rows, then resolve cancelled both; no call was made. Commit `e0cf61a`.
- Telegram Bot API rejected provided group ID with `Bad Request: chat not found`, so no topics were created and routing was deliberately not switched. The bot must be added as a forum admin with Manage Topics, then group ID must be supplied from that chat.
- Corrected the canonical Bot API group ID from the user screenshots and verified that the chat is now a forum. Deployed it as the default Telegram route; group canary delivered `telegram.main:sent` with zero call rows. Bot still lacks `can_manage_topics`, so topic creation and the severity-to-topic map remain pending. Commit `c361e30`.
- After the user granted Manage Topics, created Notice=4, Important=5, Critical=6, Emergency=7. Deployed the persistent severity map and a real notice canary delivered successfully with zero call rows. Commit `ada8e64`.
- User authorized `fleet-health` as the sole emergency producer. Its live token scope is now `emergency`; a real emergency canary reached Telegram immediately, scheduled Matrix at 601s, and resolve cancelled Matrix before any call.
