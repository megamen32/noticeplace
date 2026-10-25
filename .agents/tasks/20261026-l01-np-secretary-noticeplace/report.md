# L01 — np-secretary: «секретарь сказал, что Notice Place недоступен»

Дата: 2026-10-26 (UTC). Хост: roomhacker-server-100. Репозиторий: `/home/roomhacker/agents-projects/noticeplace` (канонический checkout, `main`).

## 1. Точный исходный запрос и текущий отказ

### 1.1 Исходный запрос (найден, не пересказан)

Agent Herder-сессия `01a1653b-d774-73b3-9c0a-09cced76ea9e` (codex, lead голосового проекта; сейчас удержана бюджетом владельца), нативный rollout
`/home/roomhacker/.codex/sessions/2026/10/22/rollout-2026-10-22T23-09-43-01a1653b-d774-73b3-9c0a-09cced76ea9e.jsonl`:

- USER[3] (22.10): «по службе уведомления… мы настраивали, что когда я звоню, включается секретарь, а сейчас включается сразу Notify Place… Notify Place очень бесполезный… Сейчас говорит: „Не могу найти su“… не произносит „Ошибка 502“… домены не называют».
- USER[14]: владелец перенаправил задачу на Matrix-звонок секретарю (ссылка на поток `01a158bb…`).
- USER[27] (23.10): «„Голосовой секретарь" и Notice/Notify Place являются ОДНИМ проектом… Ты, существующая сессия 01a1653b… ЕДИНСТВЕННЫЙ ведущий».
- USER[59]/USER[63]: «я ему сейчас позвонил, а мне до сих пор не ответил секретарь… Сегодня мне позвонил и сразу сбросил».

Цепочка, которую владелец описал словами USER[31]: **входящий звонок → секретарь → полезный ответ и действие → переход к Notice Place → возврат к секретарю**. Проверяемый критерий владельца в текущем задании (сессия `01a174ce-7e3c-7a00-8b4d-174cf7ba10ad`, USER[3]): «**Ошибка Notice Place не приводит к бессмысленному звонку или бесконечной эскалации**».

### 1.2 Текущий отказ — измерено на живом runtime, не пересказ

**(а) Сторона секретаря — «Notice Place недоступен» (буквальная причина слов секретаря).**
В живом контейнере `userio-voice` на server-88:

| проверка | факт |
| --- | --- |
| `NOTICEPLACE_DB` в `/etc/userio-voice/environment.json` | отсутствует (0 совпадений) |
| `NOTICEPLACE_DB` в окружении контейнера | `None` |
| путь по умолчанию `/var/lib/notification-center/notify-center.sqlite3` в контейнере | **не существует** (mounts: `/etc/userio-voice`, `/var/lib/agentcall`, `/run/agentcall`) |
| `import notification_center` в voice-образе | `ModuleNotFoundError` |
| фактическое чтение `NoticePlaceVoiceStore(owner_audiences=True).list_incidents()` | **`OperationalError: unable to open database file`** |

Следовательно, у секретаря в режиме инцидентов (`incidents_list` / `incident_read` / `incident_session_read`, `src/universal_userio/voice/noticeplace_voice_context.py`) **каждый** вызов падает. Секретарь произносит «Notice Place недоступен» — это не оборот речи, а пересказ ошибки своего инструмента.

**(б) Сторона Notice Place — доставка в тестовую комнату не работала никогда.**
В живом хранилище меша: `dlv_176ba2cfc49c41eca84b6bebef72d288` (`inc_1d027be8f1fd419f890e67d3e7fc89a8`, проект `noticeplace-qa-stop-20261023b`) — доставка `matrix.message` в изолированную комнату `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` завершилась **`HTTP Error 403: Forbidden`**, `attempt=5`, статус `cancelled`. Причина: отправитель матричного транспорта Notice Place — `@aiadmin:chat.bezrabotnyi.com` (`MATRIX_MESSAGE_ACCESS_TOKEN`, `whoami`), а этот идентификатор **не состоит в изолированной тестовой комнате** (`joined_members` → 403). Комната состоит из тестовых ботов A и B.

**(в) Сторона эскалации — отказ зависимости превращался в звонок и в бесконечный повтор.**
Живой журнал доставок доставленного релиза `20261023T091306Z-b1944cd2b39c-2269751` (ревизия `b1944cd`, без опубликованного исправления `df7e0d6`):

- `dlv_ed13706e1c684f97b33bd4ab3498c02a` — `matrix.call` → `status=unanswered`, `safe_to_fallback=true` → цепочка **перешла на следующий канал**;
- `dlv_19583ccee1e94e` — `telegram.call:voice-chain:1` → `HTTP 409 existing account-bound Telegram relay required`, `uncertain` (звонок владельцу уже был поставлен в очередь).

То же в другом эпизоде: `dlv_30e2b58f0cf24e` (`matrix.call` → `another Matrix voice call is active`) → `dlv_1541df99b68548`… → `dlv_3e24b8b6e54c45` (`telegram.call`).

Это и есть «бессмысленный звонок» и «эскалация после отказа Notice Place».

**(г) Механизм бесконечного повтора — в коде Notice Place (собственный дефект, закрыт этим листом).**
`notification_center/http_api.py`, `DeliveryWorker.deliver`: любое исключение адаптера (включая окончательный `403 Forbidden`) приводило к `complete_delivery(..., "retry", delay)` с экспоненциальной задержкой **без потолка попыток**. Поле политики `max_repeats` ограничивало только *следующие успешные* шаги и не ограничивало повторяющиеся попытки одной доставки. Отсюда `attempt=5` у доставки выше: повтор шёл, пока инцидент не закрыли вручную.

---

## 2. Что сделано в каноническом checkout

### 2.1 Установлен опубликованный релиз (ремонт, который уже был одобрен и опубликован)

`sudo ./deploy/noticeplace upgrade` с `NOTICEPLACE_PUBLISHED_SHA=e448126992221980bddbebdf9deba549a326f743` (ревизия с исправлением `df7e0d6`):

- релиз до: `/opt/noticeplace-releases/20261023T091306Z-b1944cd2b39c-2269751`;
- релиз после: `/opt/noticeplace-releases/20261025T210500Z-e44812699222-1754156`;
- юнит `noticeplace-mesh-api.service` — `active`, `ActiveEnterTimestamp=2026-10-26 00:05:07 MSK`;
- откат сохранён: `/opt/noticeplace-releases/previous` → прежний релиз; операторская консоль (`notification-center-admin.service`) не трогалась (`inactive` до и после);
- readback: в развёрнутом коде присутствуют `consumer_matrix_scope`, `consumer_policy_is_matrix_only`, `incident_is_matrix_only`, `incident_matrix_destination`, `matrix_route_scope_denied`, `destination_mismatch`, `expected_request_id`, `replace_consumer_policy`, `bind_matrix_reply`;
- `GET https://notify.bezrabotnyi.com/v1/instructions` → `200`.

### 2.2 Аудированная политика существующего QA-потребителя (без нового потребителя и без нового токена)

`AdminConfigStore.update_consumer_policy` → `core.replace_consumer_policy` на живом меше (`consumer_7ad31d4110d744259bde4ee7580890e4`, проект `noticeplace-qa-stop-20261023b`, `max_severity=notice`, профиль `custom`):

| | до | после |
| --- | --- | --- |
| `room_id` | `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` | тот же |
| `participants` | `[]` | `[A, B]` |
| `senders` | `[]` | `[A, B]` |
| `target` | `""` | `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com` |
| `account_ref` | `""` | `matrix:ab023a689e926bf99d924e933ebd` |
| `matrix_only` | true | true |

Один включённый шаг `matrix.message`, `previous_step_id=null`, `retry_interval_seconds=60`, `max_repeats=1`. Аудит-строка `consumer_policy_updated` (actor `l01-np-secretary`) создана.

**Почему шаг `message`, а не `call`:** установленный UserIO не понимает типизированный адресат (п. 3), поэтому шаг `call` до сих пор ушёл бы на фиксированный маршрут владельца. Шаг `call` остаётся подготовленным, но не включён.

### 2.3 Собственный дефект Notice Place: отказ зависимости не должен повторяться бесконечно

`notification_center/http_api.py` + `notification_center/core.py`:

- новый тип `MatrixDeliveryRejected` — окончательный отказ Matrix (401/403/404): «этим аккаунтом эту комнату использовать нельзя», повтор не поможет;
- `MatrixMessageSender.send` различает окончательный отказ (401/403/404) и временный сбой (429, 5xx, таймаут);
- `core.delivery_attempt_ceiling(delivery)` — потолок попыток, который объявляет сама политика шага (`max_repeats`);
- `DeliveryWorker.deliver`: окончательный отказ → доставка завершается `failed` сразу; временный сбой → повтор только если попытки ещё остались по потолку политики. Сбой шага больше не крутится бесконечно.

Тесты (`tests/test_matrix_delivery_failure.py`, 4 теста): окончательный 403 → `failed`, `attempt=1`, второй тик ничего не отправляет; временный сбой → `queued` → `failed` на потолке `max_repeats=2`, третий тик молчит; Matrix-only потребитель → ни одного `telegram.*` и ни одного `*.call`; 429 остаётся временным.

Регрессия: 161 тест проходит; 9 падений (`test_delivery_worker.py` × 6, `test_http_api.py` × 3) — **предсуществующие на `HEAD`**, проверено откатом моих двух файлов и повторным прогоном (тот же набор).

### 2.4 Транспортная возможность ответа

В `/etc/noticeplace-mesh-api.env` добавлен `NOTIFY_MATRIX_REPLY_TOKEN` (0644→0600, значение не печатается). Без него `POST /v1/matrix/reply` отвечает `403 the matrix reply capability is not configured`, то есть привязка ответа к инциденту была бы недоступна.

---

## 3. Граница: конкретное столкновение с UserIO (файлы и блокеры)

Ни один файл UserIO не менялся. Требуются ровно следующие вещи:

1. **`/home/roomhacker/agents-projects/universal-userio/src/universal_userio/voice/noticeplace_voice_context.py`** — источник данных секретаря. Сейчас класс `NoticePlaceVoiceStore` читает только локальный файл/сокет. Установленный `userio-voice` на 88 не имеет ни `NOTICEPLACE_DB`, ни пакета `notification_center` в образе, ни `sqlite3`-файла, доступного контейнеру. Нужен либо доступный из контейнера 88 маршрут к живому хранилищу Notice Place, либо чтение через HTTP-API Notice Place по проектному токену.
2. **Установленный UserIO не понимает типизированный адресат.** В контейнере `userio-backend` (образ `universal-userio-backend:957baa04be34`) файл `src/universal_userio/noticeplace_calls.py` имеет sha256 `0fcaef0facab27105ca8333628ca16334f5eb1467f882e5b7a56bd1973ecc138` и **не содержит** `_binding`/`_policy_destination` — то есть поле `destination` из тела запроса Notice Place игнорируется, и используется фиксированный маршрут:
   `USERIO_MATRIX_NOTICE_ACCOUNT=matrix:3a38caa1e8ac2eeb77443b275466`, `USERIO_MATRIX_NOTICE_ROOM=!IfwZsTlPzouoIDHQez:chat.bezrabotnyi.com`, `USERIO_MATRIX_NOTICE_TARGET=@bezrabotnyi:chat.bezrabotnyi.com` — **личный идентификатор владельца**. Поддержка адресата опубликована в UserIO (`7c3793b` `_binding`/`_policy_destination`, затем `dbbda34` — маршрут через primary88), но в установленном образе её нет, а локальный checkout UserIO отстаёт от `origin/main` на 11 коммитов. Живой путь вызова: `MATRIX_CALL_URL=http://192.168.2.100:18093/v1/calls/matrix/noticeplace`.
3. **Нет файла маршрутов** `USERIO_MATRIX_NOTICE_ROUTES_FILE` — в системе нет ни одного файла с таким содержимым; записи `USERIO_MATRIX_NOTICE_*` существуют только в `/etc/userio-containers/processes.json` (фиксированный маршрут владельца).
4. **Нет привязки ответа к инциденту на стороне UserIO.** В `universal-userio/src/` нет ни одной ссылки на `/v1/matrix/reply` и на `NOTIFY_MATRIX_REPLY_TOKEN`: транспорт, который должен доставить ответ из комнаты в Notice Place, не реализован.
5. **Отправитель Notice Place не состоит в изолированной комнате** (`@aiadmin` → 403). Чтобы уведомление дошло до A и B, отправитель должен состоять в `!ZatzpywRZSemgAaPju` (или доставка должна идти аккаунтом тестового бота).

**Поэтому звонок не выполнялся.** Это подтверждает и указание lead: «do not place a call until a non-owner QA binding is proven by installed runtime readback» — установленный readback доказывает, что не-владельческого маршрута в установленном UserIO нет.

---

## 4. Что доказано на живом пути

(см. §5 — заполняется после установки второго релиза и живой прогона)

---

## 5. Границы и честность

- Личный телефон владельца не использовался; реальных телефонных наборов — ноль; голосовой звонок не выполнялся.
- Ни один файл UserIO не изменён. Сессия `mvs_2b489b9ad2364fcaa03f4687420699a1` (старый L09-L10) не будилась и не опрашивалась.
- Историческая папка `.agents/tasks/20261024-secretary-recovery-fullcycle/**` не редактировалась и её очередь не возобновлялась; её находки использованы только после живой проверки.
- Чужие изменения в checkout сохранены: `bin/notify-center` (правка `HerderActionOwner`), `.tmp/ui-screens/**`, неотслеживаемые каталоги задач — не тронуты, не закоммичены, не сброшены.
- «Квитанция звонка» не выдаётся за полезный ответ: голосового ответа на этот лист нет и не заявляется.
