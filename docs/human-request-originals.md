# Исходные сообщения в уведомлениях UserIO

Владелец текущей доработки: AI-сессия Codex
`01a10bc5-6c86-7011-9962-e1bbd2285921`.

Короткий оригинал виден полностью в самой карточке, отдельно от разбора AI.
Длинное уведомление приходит с одним UTF-8 Markdown-файлом: полный оригинал,
разбор и варианты действий вместе. Раздел оригинала сохраняет исходный текст
без обрезки, удаления пробелов или переписывания Markdown. Заголовки файла
структурируют содержимое; исходный текст остаётся данными, а не инструкциями.

Политика ограничивает исходный текст 100000 UTF-8 байт. У карточки лимит 4096
UTF-16 единиц; choice-карточка заранее оставляет место для выбранного действия.
Persisted `original_text` и его idempotency остаются прежними. Новый состав
документа отличается от исторического raw-MD: старые доказательства скачивания
сырого оригинала сохраняют свою историческую силу.

## Восстановление старой карточки

`POST /v1/human-requests/{request_id}/original` принимает только
`original_text` и необязательный `actor`; нужен тот же project Bearer token.
Продюсер сначала читает точный UserIO event по seq, сверяет request ID и
sender/receipt, затем передаёт полный body. Оригинал нельзя брать из пересказа
LLM или из обрезанного MCP-preview.

API атомарно добавляет отсутствующий оригинал, пишет audit с размером и SHA256
и редактирует **тот же** Telegram message ID. Оно не создаёт новую просьбу,
не запускает AI, не отправляет ответ собеседнику и не вызывает звонок.
Recipient, actors, choices, expiry, сохранённый ответ и его автор неизменны.
У resolved-карточки остаётся выбранная метка; кнопки не возвращаются.
Подписанный callback использует свежий persisted original и проверенный
исходный chat/message receipt, поэтому старый callback snapshot не стирает
восстановленный текст.

Отличающийся повторный оригинал отклоняется. Идентичный Telegram edit безопасно
распознаёт native `message is not modified`. Документ резервируется один раз;
`sending`/`uncertain` не повторяются без независимого reconciliation. Перед
загрузкой файла в forum требуется исходный thread из edit receipt: текущий
project route не заменяет старую тему.

## Проверка и текущая граница

Source checks: formatter 9, HumanRequest 20, backfill 11. Включены same-ID
HTTP route, immutable source/project scope, сохранённый выбранный ответ,
stale callback, native HTTP400 replay, strict receipt, unknown send fence и
route A/current B red→green regression.

## Реальная доставка принята 2026-10-06

Reviewed исходник `a7cfef7` вошёл в coherent release `daa39d2`; один managed
upgrade создал `20261006T150303Z-daa39d2ceaae-953960`. Main/admin PID
`955716/955719`, active/NRestarts 0. Девять необходимых source/runtime модулей,
включая новый formatter, совпали побайтно. EnvironmentFile и все runtime
settings сохранены. Общая suite владельца phone slice: 411 passed.

Три карточки со скриншота реально отредактированы на **прежних** местах:
`5842/userio-39461`, `5845/userio-39472`, `5849/userio-39515`. Их UserIO body
извлечены по точному seq; размеры 7/2/330 символов. Recipient Helper на аккаунте
`11/Careviolan` прочитал полный оригинал в каждой карточке. У `5849` сохранены
resolved/«Отправить 1»; recipient, actors, choices, expiry, ответ и время ответа
сверены до/после. Claims, triage и Send1 повторно не выполнялись.

Один benign длинный notify без choices подтвердил новый документный контракт:
request `userio-original-composite-proof-20261006`, card `5899`, document `5900`,
topic `5764`. Recipient Helper получил файл `text/markdown`, 7955 байт,
SHA256 `cb32f77313df8f5364a33b570c740374902e3e57a7c08d5f2eb110d3728ef96a`.
Независимое скачивание дало те же байты и hash; сверены полный original
(4000 символов), весь persisted AI card text и оба Markdown-раздела.
Исторический raw-MD `5862` не менялся.

Helper account не переключался. Звонков, DM собеседникам, AI job/session
launch и повторного выбора не было. Acceptance END передан владельцам,
Notice больше не перезапускается в этом цикле. Protected доказательства:
`.tmp/userio-original-repair-20261006/acceptance-result.json` и
`received-md-proof.json`; личные тексты и секреты не помещаются в Git.
