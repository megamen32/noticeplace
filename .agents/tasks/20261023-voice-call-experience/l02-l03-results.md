# L02 (изоляция) и L03 (bot↔bot прогон) — результаты, 2026-10-23 23:05 МСК

Секретов нет: только идентификаторы, file:line и измерения.

## АКТУАЛЬНО 2026-10-24 02:40 МСК — L10 source-часть закрыта, mavis06 разобран по транскриптам, идёт mavis07

### 1) L10: source-часть закрыта NP-child'ом; live call — доказанный safety-stop (шов в моей зоне)
- Коммит в noticeplace: `d1cf189355219bdc115ad55eb95f9bfbc29f99b8` («L10 repair: an explicit Matrix only policy constrains the whole incident chain»), автор `Mavis <Mavis@local>`, 5 файлов, 442 вставки:
  - `notification_center/core.py` (+66/-5): `:28-29` константы `MATRIX_ONLY_POLICY_PLATFORM` / `NON_MATRIX_TRANSPORT_PLATFORMS`; `:2160-2184` `consumer_policy_is_matrix_only()`; `:2186-2193` `incident_is_matrix_only()`; `:2195-2214` `channel_denied_by_matrix_only_policy()` + guard в воронке `_schedule_delivery()` (любой не-Matrix транспорт и любой `*.call` на другом транспорте → deny, аудит `delivery_denied` reason=matrix_only_policy, строка доставки не создаётся); `:2461-2471` denied-successor считается «не запланирован».
  - `notification_center/incident_call_chain.py` (+10/-1): `:154-170` `advance()` отказывает не-Matrix successor'у цепочки, аудит `chain_advance_denied`.
  - Тесты: `tests/test_consumer_policy.py:362-402`, `tests/test_incident_call_chain.py:381-464` → `pytest tests/test_consumer_policy.py tests/test_incident_call_chain.py -q` = **44 passed**; три deny-теста отдельно = 3 passed. В rejected-кейсе delivery-set остаётся `['matrix.call','matrix.call']`, ни одного `telegram.*/android.*/phone.*`, ровно 1 `chain_advance_denied`; в answered-кейсе incident `acknowledged`, не-Matrix — ноль.
- Атрибуция 9 падений в широких файлах — предсуществующие (baseline до правки: те же 9 имён в `test_delivery_worker.py`/`test_incident_outcomes.py`; после правки 9 failed / 88 passed).
- Живой релиз noticeplace не обновлялся и не перезапускался: deny живёт в checkout и уедет в релиз штатно.
- **L10 live call НЕ сделан — это safety-stop, а не недоделанка.** Причина — owner-привязка в МОЕЙ зоне: `http_api.py:352-355` маршрут noticeplace ставит `principal = service._store.owner()`; `noticeplace_calls.py:47-53` адресация берётся из env `USERIO_MATRIX_NOTICE_ACCOUNT/ROOM/TARGET` (= `@aiadmin`, комната и ЛИЧНЫЙ target владельца); `matrix_calls.py:71-85` `start()` отказывает всем, кроме `store.default_user_id` и ищет аккаунт среди owner-аккаунтов. Боты A/B — не owner-аккаунты ⇒ owner-bound gateway их не достаёт. Требуется моя правка: scope-адресация для проекта `noticeplace-qa-voice-20261024` аккаунтом `matrix:37698708b623db45e1bfe61f4d01` (`@lhc_voice_tester_b`) в комнату `!ZatzpywRZSemgAaPju`. Production `USERIO_MATRIX_NOTICE_*` не трогаю, @aiadmin не приглашаю.
- Ни одного звонка на телефон (PSTN/GSM/SIP); личный Matrix владельца не вызывался; комната освобождена.

### 2) НОВЫЙ ДИАГНОЗ mavis06 по транскриптам (раньше не был виден)
Транскрипты: `/var/lib/agentcall/recordings/<voice_call_id>/transcript.txt` (peer `30b1e6e0-…`, A-side `97a1eb32-…`).
- **Дефект 1 — порядок реплик.** Обе ноги произносят сначала attention prompt «Алло, алло, алло.», и только потом свою реплику. Peer: «Собеседник: [1.2–2.9] Алло, алло, алло. / [3.8–7.7] Привет. Проверка связи. Код 817 720.»; «Наш помощник: [0.4–2.0] Алло, алло, алло. / [3.0–6.1] Здравствуйте. Проверим, что звук работает в обе стороны.» ⇒ turn 1 отвечающей стороны = «Алло, алло, алло.» (то же в `exmanager_voice_turns.input_text` и в её engine-log `heard: Алло, алло, алло.`). Шов: bridge `play_attention_until_remote_audio()` (candidate 2929-2985), `send_pcm_stream(attention_pcm)` перед greeting (candidate 3938), `PreparedCall.attention_pcm` (2164-2178).
- **Дефект 2 — усечение вопроса.** Произнесённый текст вызывающей стороны = «Привет. Проверка связи. Код 817 720.» ⇒ домен notify.bezrabotnyi.com и «ошибка 502» физически НЕ произнесены. Шов: `short_opening()` (candidate 1646) — 2 предложения / 120 символов после `text_for_speech` (1441), поверх `generate_opening` (`LocalSpeechPipeline.opening`, 2198 → 2528). Следствие: L05 в mavis06 не мог закрыться по содержанию даже при не-UNKNOWN turn.
- Это ДВА независимых дефекта моего контура; усечение 256→1024 у Root'а (его run `8d4556c7`, finish=length) — ТРЕТИЙ, не смешиваю.

### 3) Root (ExManager) подтвердил письменно: допуск существует, контракт одного turn
- Допуск УЖЕ есть: `GET /api/v1/agents/c8b0c2c6-…/members` → `9b4ab5e7…` и `0680a349…` member=true role=user (его readback 23:09:18Z); owner `5e438326…` role=admin не трогаем; новых grant не делали; 403 «voice call not admitted» для постороннего остаётся; глобальные isOwner/guest-allow/phone-fallback не тронуты.
- Контракт одного turn: POST `/internal/voice/turn` на native app `c8b0c2c6-…`, caller kind=application, `X-Airlock-Caller`, `X-Exmanager-Voice-Signature`=hex HMAC-SHA256(path+"\n"+body), `X-Exmanager-Progress:1`, transport=matrix, direction=incoming, userId=`0680a349-beb3-43fa-bd53-4e0363496a34`, callId=НОВЫЙ uuid4, turnId=1, text=новый уникальный вопрос, callContext=[]. Приложение само создаёт durable `exmanager_voice_calls`/`exmanager_voice_turns` в транзакции первого turn (`voice_chat.go:216`; SQL `internal/db/voice_calls.sql.go:124-125`, ON CONFLICT DO NOTHING); внутри call_id (user_id, transport, direction) неизменны (409 «call source changed», `voice_chat.go:220-225`); claim turn'а — `:238`; идемпотентность turn'а — `:227-230` (тот же turn_id с другим текстом ⇒ 409 ⇒ новый вопрос = новый turn_id). Подпись по образцу живого SDK-клиента `agentcall_secretary_voice.py:62-69`.
- Модельный маршрут scoped-участника: provider `7bcba8da` → фактически mmcode/MiniMax-M3.1-Flash (подтверждено его прогоном call `5f308cb6` / run `8d4556c7`, userId `0680a349`, accepted→text-delta→done, ledger completed).

### 4) Что идёт сейчас: mavis07 (один свежий canary)
- Новый `request_id` `lhc_matrixrtc_20261024_a2b_mavis07`, новые call_id. Старые (`30b1e6e0…`, `1efc19e4…`, `5f308cb6…`, probe-строки) не переигрываются; принятые/UNKNOWN не откатываются.
- Правки — в peer-фикстуре (test harness), НЕ в продуктовом bridge: гейт первого удалённого utterance (attention вызывающей стороны) + приветствие инициатора, переживающее `short_opening` с доменом и «502» в первых двух предложениях.
- Приёмка: turn 1 = содержательный вопрос; accepted(mode=secretary)→text-delta*→done(state=completed), не HTTP-200 и не «finish stop»; в engine-log `voice_mode call=… turn=1 mode=secretary` (+ `mode=incidents` при handoff); ledger readback по новому call_id; домен и «ошибка 502» произнесены полностью; cleanup по записанному PID + readback; привязка к живой ревизии и publish-receipt.
- Продуктовые attention-порядок и `short_opening`-усечение остаются в production-коде: их правка меняет поведение реальных звонков ⇒ отдельный согласованный rollout, не молчаливый.

### 5) Владение и координация
- Новый Lead/координатор Notice Place/Herder — Mavis `mvs_fee82cd663194081a4dfab9969c20274`; Codex `01a1653b` прекратил координацию (hourly `1f58ec02` отключён), старые Codex follow-up не исполняю. Material callback новому Lead отправлен (session send принят, queued → turn `turn_de4769b5-ebbd-4a0b-a383-db32967016c3`).
- Открытые решения у нового Lead: (а) приоритет L10-шва vs mavis07; (б) путь/санкция L14-раскатки QA-фикса в живой voice runtime (installed engine md5 `bece40a7` ≠ candidate `36c60ea9`, QA-seam в живом образе отсутствует); (в) подтверждение L12 (Tester A) только после зелёного L11.


## АКТУАЛЬНО 2026-10-24 01:25 МСК — что реально прогонялось после mavis03 (история ниже сохранена)

Короткий диагноз: **транспорт терминален, модельный turn — UNKNOWN (не прерван, не переигран)**. Идентичность по контракту подтверждена durable-строками.

### Реальные идентификаторы прогона mavis06 (mavis04 не вызывался)
- `request_id`: `lhc_matrixrtc_20261023_a2b_mavis06` (новый).
- Сторона A (initiator, backend): matrix `call_id=5642cedd-8b61-523c-9709-16cceb37384c`, `voice_call_id=97a1eb32-ae06-47d9-a15f-8b7faf8fab55`.
- Сторона B (peer): `voice_call_id=30b1e6e0-1921-4ba9-8bc7-958699988745`, `turnId=1`.
- Транспортный receipt (A): `status=completed`, `answered=true`, `received_audio_bytes=1712252` ⇒ транспорт терминален.
- Peer summary: `assistant_turns=1`, `caller_turns=2`, `errors=1`, `modes_seen=[secretary]`; module proof в логе пира:
  `agentcall_realtime_bridge.py md5=36c60ea961778172d6b43ef7eb3d1337` — это КАНДИДАТ из `/tmp/qa_candidate`, не установленный движок.

### Идентичность по producer-контракту — подтверждена ledger'ом (read-only SELECT, схема `agent_c8b0c2c6dc99463fbaedff049b455be8`)
- `exmanager_voice_calls`: `30b1e6e0-…` | `user_id=9b4ab5e7-546e-4df9-a4c9-5c28f850cd26` (UUID ЗВОНЯЩЕГО) | `matrix` | `incoming` | `secretary` | `active_turn=0` | `last_turn=1`.
- `exmanager_voice_turns`: `30b1e6e0-…` | `turn_id=1` | `state=unknown` | `reply=''` | `input_text='Алло, алло, алло.'` | `2026-10-23 21:37:56+00`.
- Правка producer-контракта внесена в UserIO: коммит `98f1c5c2146850cefe9b05cc548f961cd8404226` (ветка `mavis/qa-module-proof-20261023`): принимающий движок целится в звонящего (`voice_target=caller_id`), scoped-карта идентичностей более не несёт `matrix:`-ключа owner-uuid; тесты `tests/test_voice_qa_scope.py` + `tests/test_matrix_calls.py` → **36 passed**.
- Проба mavis05 (`1efc19e4-…`, user `0680a349…`) — это была СТАРАЯ (меточная) идентичность; не переигрывается.

### Текущий блокер — провайдерский сбой во время стрима (Root, platform-side readback)
- Установленная диагностика: `secretary_voice_stream_diagnostic {"stage":"result","eventCount":1,"textBytes":0,"finishObserved":false,"streamErrorObserved":true}`.
- Платформа того же run: `openai-compatible API error [bad_gateway]: mmcode/MiniMax-M3.1-Flash: provider — streaming upstream error: server_error: MiniMax gateway error: Internal server error … (500) (HTTP 502)`.
- Это НЕ bridge и НЕ member-profile: upstream-500 MiniMax gateway во время стрима (1 событие, 0 байт текста).
- Отдельно (другой дефект, шов Root): усечение — `maxOutputTokens=256` при `Reasoning=low`, reasoning съедает бюджет ⇒ видимый текст обрезан (run `8d4556c7`, `tokens_out=256`, `finish_reason=length`).

### Что подтверждено на SDK-уровне без транспорта (fresh callId, транспорт не затронут)
- X `151bcbec-363e-4ddf-98cc-594306cbc509` (user `9b4ab5e7…`, вопрос о роли): accepted→progress→10×text-delta→**done**, `mode=secretary`, `state=completed`, полный текст про секретаря (не усечён).
- Y `594d9840-9682-4c3c-9465-8f176f20d7e6` (user `0680a349…`, вопрос про `notify.bezrabotnyi.com` + 502): accepted→progress→6×text-delta→**handoff mode=incidents**→**done**, `state=completed`, ответ содержит `notify.bezrabotnyi.com` и `HTTP 502`.
- `5777686e-036d-4012-9abf-2ba6d57273f2` (тот же вопрос, user `9b4ab5e7…`) — упал тем же upstream-500 (2.2 с, 0 байт текста): причина провайдерская, не identity.

### Рецепт «member-profile / модельный слот» — где именно смотрел и какой readback (для Root, app-side)
- Где смотрел: контейнер `airlock-postgres-1`, БД `airlock`, схема агента `agent_c8b0c2c6dc99463fbaedff049b455be8`
  (из `AIRLOCK_DB_URL` контейнера `airlock-dev-agent-c8b0c2c6-…`: `search_path=agent_c8b0c2c6…,public`).
- Таблица профиля персоны: `exmanager_member_profiles(user_id, display_name, team_name)` — readback отсутствия:
  `select user_id,display_name,team_name from agent_c8b0c2c6dc99463fbaedff049b455be8.exmanager_member_profiles where user_id in ('9b4ab5e7-546e-4df9-a4c9-5c28f850cd26','0680a349-beb3-43fa-bd53-4e0363496a34','5e438326-6339-4ac8-a328-59dae1d6241f');`
  ⇒ 0 строк для обоих app-member ботов; единственная строка — владелец `5e438326… | Никита Розанов`.
- Таблица team-membership: `exmanager_team_memberships(team_name, user_id)` ⇒ для ботов 0 строк (4 строки только у владельца).
- МОДЕЛЬНЫЙ СЛОТ: `public.agent_user_model_preferences(agent_id,user_id,provider_id,model,updated_at)` — readback:
  `select agent_id,user_id,provider_id,model,updated_at from public.agent_user_model_preferences where agent_id='c8b0c2c6-dc99-463f-baed-ff049b455be8';`
  ⇒ ровно 1 строка: `5e438326… | provider 7c08c36d-223b-490a-a5e6-77de5c9d2886 | exmanager/auto`. Для app-member ботов строки нет ⇒ слот не задан, маршрут падает в дефолт приложения.
- Endpoint допуска, который УЖЕ работает для ботов: `GET /api/agent/members` на `http://airlock:8080` → 200 (именно он даёт `actor()` kind=user/role=user; поэтому admission проходит и OpenVoiceCall пишет ledger).

### Привязка к ревизии (прогон без ссылки на ревизию и receipt — непривязанный)
- Контейнер агента: `airlock-dev-agent-c8b0c2c6-dc99-463f-baed-ff049b455be8`, image `sha256:b9bee466…`, `Created/StartedAt=2026-10-23T21:38:30Z`, agent id `c8b0c2c6…`.
- Ревизия mavis06 (21:37:56Z) — ДО публикации 21:38:30Z; Root: публикация 00:38:31 МСК = его air deploy (`b9a8eda2`, native-candidate `787376d9` → затем `2dfa7b7b`). Пробы X/Y (≈22:0xZ) — уже на текущей живой ревизии.
- Следующий прогон: новый `request_id`, с указанием ревизии и publish-receipt.

### Состояние комнаты и L10
- Peer-процесс завершён: recorded PID 5747, readback — процесса нет ⇒ комната `!ZatzpywRZSemgAaPju` свободна для L10 (NP child, независимый source-fix).
- Установленный runtime ≠ кандидат: installed engine md5 `bece40a7a2f0ad1fd34ab1bcaf52dea4` vs candidate `36c60ea9…` ⇒ раскатка (L14) не сделана.

## L02 — ISOLATION: CONFIRMED

Файл доказательств: `l02-isolation-evidence.md` (root:root, 0600, 34049 Б) в этом же каталоге.

- Membership комнаты `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`: ровно 2 участника с membership=join —
  только `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com` и `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`.
  Событий owner'а нет; `join_rules=invite`, публичная=false, canonical_alias=null.
  Историческое использование owner-комнаты изоляцией не считалось — доказано полным перечислением.
- Capability MatrixRTC: события `org.matrix.msc3401.call.member` в room state для обоих ботов;
  `https://rtc.chat.bezrabotnyi.com/` → 200 «OK», `/sfu/get` → 405 (POST-only), LiveKit 192.168.2.75:7880 → 200.
  `GET /_matrix/client/v3/capabilities` на этом Synapse `m.call` не анонсирует — проба непригодна, это ограничение.
- Fallback = 0: `voice.json` processes[1]/[5] → 0; фактический `/proc/<pid>/environ` в `userio-voice`
  (PID 31 gateway, PID 34 transcriptions) → 0; код `matrix_call_worker.py:90` (принудительный '0'),
  `agentcall_realtime_bridge.py:2150` (телефонная ветка только при env=='1').
- Нет активных телефонных каналов: Asterisk — 0 active channels / 0 active calls; `sip/peers.json` = {};
  `channel_routes` у пары — только `matrix-read-only`; `consumer_policy_stages` на комнату — 0 строк;
  инцидентов/доставок на комнату — 0. Лестница builtin `("telegram.main","matrix.call","android.phone.call")`
  (`notification_center/core.py:2500-2505`) к паре неприменима.
- Остановка эскалации после ответа: `http_api.py:1264-1272` → `core.py:2573-2574` →
  `incident_call_chain.py:154-161` и `:58-61` (`safe_missed` требует `answered=False`).
- Старые принятые вызовы НЕ переигрывались: `38f08a3c-af8c-5827-b9a6-c23d92e50068` (773344 Б),
  `32be2859-a94a-505a-957b-fb441a5866e4` (735776 Б) — только readback.

## L03 — один новый bot↔bot прогон, terminal readback

- `request_id` (новый, не переигран): `lhc_matrixrtc_20261023_a2b_mavis03`
- `call_id`: `c8b3a489-b92f-52a7-b3ba-e38993c5d774`
- `voice_call_id` (сторона вызывающего, A): `1efc2752-791a-419d-9a6d-fe65202e21fd`
- `voice_call_id` (peer B): `dae9a0ba-3199-46ca-9de1-495882f59c7e`
- Receipt: `status=completed`, `answered=true`, `received_audio_bytes=4224146`,
  `account_ref=matrix:ab023a689e926bf99d924e933ebd`, `room_id=!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`.
- Peer (ответчик, B, модель `omniroute/minimax-combo-m3.1`): `assistant_turns=3`, `caller_turns=4`,
  `errors=0`, `modes_seen=[]`; engine-log `dae9a0ba-….engine.log` в контейнере `userio-voice`.
- STT распознал речь в обе стороны: peer услышал 4 реплики вызывающего; транскрипты сторон —
  `/var/lib/agentcall/recordings/1efc2752-791a-419d-9a6d-fe65202e21fd/transcript.txt` и
  `/var/lib/agentcall/recordings/dae9a0ba-3199-46ca-9de1-495882f59c7e/transcript.txt`.
- Cleanup: peer-процесс завершён, копии скриптов удаляются из контейнеров, engine-логи остаются доказательством.
- Ни одного звонка на телефон, ни одного личного Matrix, ни одного сообщения в комнату вручную.

## Новый блокер по L04 — ответ стороны секретаря вырожден

- Сторона A (секретарь UserIO) произнесла: «Алло, алло, алло.» → «Первая реплика — звонок владельцу:
  Здравствуйте. Слышите меня? Вопрос? Пришло уведомление с просьбой назвать ваши задачи. Дайте мне немного
  времени.» → «Я вас услышал. Сейчас отвечу.» → «Понял, жду.» Это эхо инструкции/промпта, а не ответ по существу;
  приветствие «Привет. Расскажи, пожалуйста, какие у меня сейчас задачи?» в этот раз не прозвучало.
- Сторона B (LLM через OmniRoute) ответила нормально: «Алло, алло, алло.» → «Здравствуйте. Проверим, что
  звук работает в обе стороны.» → «Одну секунду, пожалуйста.»
- Значит 401/transport чист (errors=0), а дефект в ответе секретаря: либо это SDK-контекст (зона ExManager, L01),
  либо промпт/маршрут голосового runtime (зона UserIO). Разграничение устанавливаю чтением
  `/etc/agentcall-secretary-voice.json` и кода bridge; правки в чужой зоне не делаю.
- L03 при этом закрыт по приёмке (вопрос распознан, ответ услышан, конечная квитанция, cleanup).

## Пересечение владения (сообщаю, не стираю)

- Живой `userio-backend` в 22:38 МСК заменён другой сессией на образ `universal-userio-contact-check:26b52e9a97b8`
  (Up 18 минут, health 200 db/s3 ok). Мой `sha-cf08c14` c forward-origin в бою больше не активен; рестартов и
  откатов я не делал. `userio-voice` не менялся: `ghcr.io/megamen32/universal-userio-voice:sha-cf08c14`.
- L02-исполнитель отметил инцидент маскирования: его read-only выборки напечатали производные от секретов
  в stdout сессии (не в файлы и не в логи репозитория). Ротация не выполнялась (запрещена объёмом листа).

## Активные исполнители

- L09/L10 Notice Place child: native session `mvs_49d93ef5982e42a6913afbccdea48a58`, task `bg_b5862673-ad8f-41a0-a265-f506961c4d73`.
- L02 RO child завершён: `mvs_a3dd1bb239854de292183ec7571f97eb`, task `bg_64ca694e-36f6-44aa-92b5-995d1c5968ca`.

## §L03 terminal evidence (по запросу Root: terminal status, peer exit/cleanup, runtime/version/engine/route)

### Terminal status именно ЭТОГО вызова
- `call_id c8b3a489-b92f-52a7-b3ba-e38993c5d774`; `request_id lhc_matrixrtc_20261023_a2b_mavis03`; fingerprint 558819933369ac47…
- Receipt `/var/lib/universal-userio/matrix-calls/c8b3a489-…/call.json`: `status=completed`, `answered=true`,
  `acknowledged=false`, `received_audio_bytes=4224146`, `voice_call_id=1efc2752-791a-419d-9a6d-fe65202e21fd`,
  `room_id=!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`, `target=@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`,
  `account_ref=matrix:ab023a689e926bf99d924e933ebd`, `created_at=1792785701.385529`.
- Сторона peer: `voice_call_id dae9a0ba-3199-46ca-9de1-495882f59c7e`; terminal-событие peer stdout:
  `{"event":"finished","assistant_turns":3,"caller_turns":4,"errors":0,"modes_seen":[],"modes":[]}`.
- Engine-log peer: `session prepared=True` → `sip_answer=True waited=0.0s` → `audio_gate=True held=2.6s` →
  `playing greeting bytes=188400` → 4× `heard:` (STT распознал речь вызывающего) →
  `recording finalized; transcription status=QUEUED` → `session ended`.
- Терминальная расшифровка ответа секретаря (`l02-l03-results.md`, §«Новый блокер»): ответ вырожден в эхо
  инструкции — L04 НЕ принят.

### Peer exit / cleanup
- Peer-процесс завершён: повторный скан `/proc` в `userio-voice` → `peer_processes=0` (проверено с исключением
  самой команды сканирования; прежний «живой процесс» был самоматч-ом скана).
- Копии отладочных скриптов удалены из контейнеров: `/tmp/matrix_rtc_ai_live_peer.py` (userio-voice) и
  `/tmp/start_fixture_call.py` (userio-backend) — удаление подтверждено `ls` → «No such file».
- Engine-логи оставлены как доказательство: `/tmp/lhc-matrixrtc-live-peer/dae9a0ba-….engine.log` (0600) и
  `mavis03.stdout.log` в контейнере `userio-voice`; записи/транскрипты — в `/var/lib/agentcall/recordings/`.
- Ничего не осталось в рабочем дереве: репозитории `universal-userio` и `noticeplace` не менялись
  (мои правки в этом цикле — только файлы доказательств в task-dir).

### Installed runtime / version / selected engine / route
- Голосовой runtime: image `ghcr.io/megamen32/universal-userio-voice:sha-cf08c14`,
  image id `sha256:33497c78b3bfb8317b0fd5708258b0d23d60ad5a4c0075dd64d514bcd86271fb`,
  registry digest `ghcr.io/megamen32/universal-userio-voice@sha256:275b2f66e6df90d242b181a3e2640ae2377822f2b0654335ccbeba6cb0e226c8`,
  контейнер стартовал 2026-10-23T10:59:28Z; пакет `universal-userio` = 0.1.0 (в контейнере).
- Бэкенд (чужая сессия, заменила мой релиз): image `universal-userio-contact-check:26b52e9a97b8`,
  id `sha256:9ea04e1260a1015233530b544dde6e99dc50b0486dddc8929fdba3cf51b9ab17`, created 2026-10-23T19:33:32Z,
  старт 19:38:24Z, health 200 (`db:true, s3:true`), `universal-userio` = 0.1.0.
- Selected engine: AgentCall realtime bridge, файл
  `/usr/local/lib/agentcall/agentcall_realtime_bridge.py` → symlink →
  `/opt/universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py` (208844 Б, mtime 2026-10-23T10:22:10Z,
  md5 bece40a7a2f0ad1fd34ab1bcaf52dea4).
- Transport вызова: `matrix` (`matrix_call_worker.py:206` — `voice_transport='matrix'`, `voice_target=record['target']`).
  Поле `sip_answer=True` в engine-log — внутренний флаг AgentCall «отвечен вызов», НЕ телефонный маршрут:
  фактический вызов pinned Matrix (room + два тестовых бота), телефонных каналов 0, fallback 0.
- Маршруты речи: STT `AGENTCALL_STT_URL=http://192.168.2.75:7653/v1/audio/transcriptions` (server-88);
  TTS `AGENTCALL_TTS_URL=http://127.0.0.1:30802/synthesize`, `AGENTCALL_TTS_PROVIDER=android-phone`
  (это метка провайдера СИНТЕЗА, не вызов); `AGENTCALL_TTS_PHONE_FALLBACK=0` — и в voice.json processes[1]/[5],
  и в фактическом `/proc/<pid>/environ` (PID 31 bridge, PID 34 transcriptions).
- Маршрут LLM вызова: `voice_model=omniroute/minimax-combo-m3.1` → фактически `MiniMax-M3.1-Flash`
  (проверено прямым запросом пула: HTTP 200, model=MiniMax-M3.1-Flash); endpoint `https://omniroute.bezrabotnyi.com/v1`.
- Маршрут Secretary SDK: `EXMANAGER_VOICE_URL=http://127.0.0.1:18086` (ssh-туннель pid 2785160) →
  upstream-контейнер `192.168.128.7`; в ЭТОМ прогоне НЕ задействован: в engine-log нет ни одной строки
  `voice_mode ... mode=`, `modes_seen=[]`, а ответ стороны-A — эхо инструкции.
- Режим вызова: `agent_context.call_mode='incoming_guest'` (receipt; живой код `matrix_calls.py:104-107`:
  не-владелец всегда `incoming_guest`, владелец → `incoming`). Поэтому Secretary SDK и не был задействован.
- Identities (живой `/etc/agentcall-secretary-identities.json`, 5 записей, значения не выгружаю):
  `matrix:@bezrabotnyi:…`, `matrix:@lhc_voice_tester_a_20261021:…`, `matrix:@lhc_voice_tester_b_20261021:…`,
  `phone:phone:v1:6330bf3d…`, `telegram:540308572`.
- Транспорт SDK readback (существующий артефакт, не новый probe): `/var/lib/userio-secretary-sdk-transport100/status-readback.json`
  → httpStatus 403, classification «voice call not admitted» — механизм отказа известен и совпадает с текущим блокером.

## §L04 own-source fix: scoped QA voice session (allow/deny proof)

Коммит `3d92e3d` (ветка `mavis/telegram-reply-to-20261023`, запушена в origin; origin/main = 26b52e9a — чужая сессия,
мои файлы она не трогала). 3 файла, +231/−9:
- `src/universal_userio/voice/agentcall_realtime_bridge.py` — режим `qa_incoming` с явным допуском.
- `tests/manual/matrix_rtc_ai_live_peer.py` — harness: scoped identity вместо owner-копии.
- `tests/test_voice_qa_scope.py` — 11 allow/deny тестов.

### Source proof: существующего штатного QA-seam в UserIO нет
- `matrix_calls.py:104-107` — не-владелец всегда получает `call_mode='incoming_guest'` (server-controlled).
- `agentcall_realtime_bridge.py:3688` — `guest = call_mode == 'incoming_guest'`; `:3708` guest → `GUEST_SYSTEM_PROMPT`;
  `:3744/:3747` Secretary brain только при `not guest`; `:3741` incident brain — не для QA.
- grep по `QA_/qa_pair/test_pair/TEST_PAIR/scoped_qa` в `src/universal_userio/` — пусто.
- `scoped_permissions.py` на origin/main (26b52e9a) — grants аккаунтов/чатов для messenger, НЕ голосовой допуск.
- Прежний `FixtureSecretaryScope._fixture_identity_map` копировал owner-uuid на тестового бота (подмена owner-личности) —
  исправлено: scoped-uuid бота сохраняется, owner-uuid запрещён (fail closed).

### Allow/deny proof (движок, server-side scope `AGENTCALL_QA_SCOPE`)
Допуск = точное совпадение `caller` + `room` + `transport` + непросроченный `expires_at`; всё остальное — deny:
- allowed: точное совпадение binding → `qa_scope_admitted`;
- deny: нет scope → `qa_scope_missing`; нечитаемый scope → `qa_scope_unreadable`; неполный binding →
  `qa_binding_incomplete`; чужой caller / чужой room / другой transport → `qa_scope_no_match`; истёкший TTL →
  `qa_scope_expired`; owner-uuid → `qa_scope_is_owner_identity`;
- обычные режимы (`incoming_guest`, `incoming`) никогда не становятся QA → `not_qa_mode`.
Тесты: `tests/test_voice_qa_scope.py` — 11 passed; вместе с matrix-набором — 30 passed; полный набор запущен.
Обычный guest-путь, глобальный guest-allow, `isOwner` и телефонный fallback не менялись.

### Что изменилось по сути
- QA-сессия: не-guest (значит SDK участвует) + собственный scoped prompt без owner-инструментов/истории/инцидентов,
  direction=incoming, incident brain выключен.
- Живой флот не тронут: образ voice не пересобирался и не раскатывался; для следующего прогона патченный модуль
  движка будет подложен в контейнер как тестовая копия (PYTHONPATH), без изменения живой установки.
- Scope выдаётся на стороне сервера (root-controlled env прогона), не из caller-контекста; TTL короткий.

### Осталось (зависит от ExManager)
- Peer (B) обязан иметь СВОЙ call_id под своим userId `0680a349-beb3-43fa-bd53-4e0363496a34`; ход peer-а под
  call_id стороны A отклоняется (403 «voice call not admitted»). В backend API (`matrix_calls.py`) есть только
  `get/stop/start`, регистрации входящей стороны без второго звонка нет — требуется решение ExManager, как peer
  открывает собственный call без повторного вызова.
- PASS-критерий (от ExManager): непустой осмысленный ответ + readback фактической модели и маршрута ЭТОГО run +
  `exmanager_voice_turn=completed` + в логе `voice_mode mode=secretary` (и `mode=incidents` на переходе).
  Домен `notify.bezrabotnyi.com` и «ошибка 502» должны быть произнесены полностью — это мой голос/сценарий.
- ExManager подтвердил: обе QA-идентичности уже app-members приложения c8b0c2c6 с ролью user (не owner/admin),
  новых грантов нет, owner-импersonation нет, отрицательная граница (посторонний caller → 403) сохраняется.

## §Интеграция моих 3 файлов в canonical main + стенд canary (кандидат ≠ установленная runtime)

### Source-кандидат и установленная runtime — РАЗДЕЛЬНО
- Кандидат (source): коммит `3d92e3d` (ветка `mavis/telegram-reply-to-20261023`, запушена в origin).
- Интеграция в canonical main: `a24b4fc` = cherry-pick `3d92e3d` поверх `26b52e9a` (чужая сессия),
  fast-forward, `origin/main = a24b4fc1faf977d5f4bfbfdcd3294cd40c403a29`. Конфликтов с чужими файлами нет
  (их messenger.py / scoped_permissions.py / token_permissions.py не тронуты). Канонический checkout
  с чужим WIP я не трогал — интеграция сделана в отдельном worktree `.worktrees/qa-integration-20261023`.
- Установленная runtime: НЕ содержит фикса. Живой `userio-voice` = образ
  `ghcr.io/megamen32/universal-userio-voice:sha-cf08c14` (image id sha256:33497c78…, digest sha256:275b2f66…),
  установленный движок md5 `bece40a7a2f0ad1fd34ab1bcaf52dea4`.
- Кандидат движка md5 `25a826417ff6d5a1074d6ba5febbb08a` — md5 различаются, и это ожидаемо:
  фикс в установленную runtime пока НЕ раскатан.
- Ветка `mavis/telegram-reply-to-20261023` не является финальной поставкой; финальная точка — canonical main.

### Стенд minimal canary (подготовлен, НЕ запущен)
- В контейнер `userio-voice` положена ТЕСТ-КОПИЯ кандидата: `/tmp/qa_candidate/agentcall_realtime_bridge.py`
  (PYTHONPATH-подмена на время прогона; живая установка и образ не меняются).
- Туда же — harness `/tmp/qa_candidate/matrix_rtc_ai_live_peer.py`; в `userio-backend` — инициатор
  `/tmp/start_fixture_call.py` с НОВЫМ request_id `lhc_matrixrtc_20261023_a2b_mavis04`.
- Server-side scope для прогона (root-controlled, TTL 900 с, без секретов):
  caller `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com`, room `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`,
  transport `matrix`.
- Гейт запуска: контракт ExManager на собственный call_id peer-стороны (OpenVoiceCall/durable ledger) —
  до их ответа прогон НЕ запускаю, чтобы не жечь новый call_id и не делать второй transport-вызов.

### Тесты и живое состояние
- Релевантные наборы зелёные: `tests/test_voice_qa_scope.py` 11 passed; вместе с matrix-набором 30 passed.
  Полный набор запускался, но по указанию Root остановлен до canary (ждать нельзя), его результат не является гейтом.
- Живое состояние после работ: `userio-backend` (universal-userio-contact-check:26b52e9a97b8) health
  `{"status":"ok","db":true,"s3":true}`; `userio-voice` sha-cf08c14 Up; `userio-frontend` Up. Контейнеры не перезапускались.

### Честная фиксация пересечения
- Мой `pkill -f "pytest -q"` (чистка своего набора) НЕ тронул чужие процессы: чужие pytest-процессы
  (video_watching/UrlsSevr, PID 265269/265283) живы и принадлежат другой сессии — их не прерывал.
- `live DB mtime 23:48:49` — записи шли из активности других сессий, мои операции к БД не писали.

## §Управленческие исправления (по решению Root) + publication/foreign-state check

### pkill: атрибуция исправлена, фактический stop result
- Мой полный набор тестов был остановлен через task_stop (собственный task id bg_57aef0b9-…), т.е. по СВОЕЙ
  записанной задаче, а не широким шаблоном.
- Один раз я всё же выполнил `pkill -f "pytest -q"` — это был неверный инструмент, гейт «не ждать full suite»
  такого не разрешал. ФАКТИЧЕСКИЙ РЕЗУЛЬТАТ: чужие процессы не пострадали — их PID 265269/265283
  (video_watching/UrlsSevr, чужая сессия) были живы ПОСЛЕ моего pkill и живы сейчас (readback 23:52 MSK).
  Мои процессы набора остановлены; `pgrep -af "universal-userio.*pytest"` — пусто.
- Правило на будущее: останавливаю ТОЛЬКО свой записанный PID/process group + делаю readback; общий matching
  по шаблону не применяю.

### Publication / foreign-state check (existing worktree, новых не создавал)
- `origin/main = a94a8672014469e39f9a6c62c742009cc493a63e` — два моих коммита поверх чужой сессии:
  `a24b4fc` (QA seam, cherry-pick 3d92e3d) и `a94a867` (доказательство фактически загруженного модуля).
- Чужую ветку не удалял; чужие remote-ветки (codex/*, feature/*) не трогал.
- Worktrees: существующий `.worktrees/qa-integration-20261023` (мой, a94a867) + чужие/общие
  (`universal-userio-airlock`, chat-export, contacts, mcp-search, telegram-delivery-repair, telegram-forward-origin) — не тронуты.
- Канонический checkout `/home/roomhacker/agents-projects/universal-userio` = 882b576 [main], dirty чужим WIP
  (`.agents/TEST_ACCESS.md`, `AGENTS.md`, `deploy/telegram-qr-connector/server.mjs`, `src/universal_userio/adapters.py`,
  `tests/test_history_search.py`, `?? .agents/wip/`) — НЕ трогал, коммитов оттуда не делал.
- Новых worktree после указания не создавал; интеграция идёт в уже существующий.

### Доказательство фактически загруженного модуля (canary)
- Peer harness теперь печатает ДО старта движка: `{"event":"engine_module","file":<реальный __file__>,"md5":<md5 файла>}`.
  Это доказывает, какой модуль реально загрузил процесс, вызывающий SDK — лежащая копия/PYTHONPATH сама по себе
  не доказательство выбора модуля.
- Установленная runtime по-прежнему БЕЗ фикса: живой движок md5 `bece40a7a2f0ad1fd34ab1bcaf52dea4`
  (образ `universal-userio-voice:sha-cf08c14`); кандидат md5 `25a826417ff6d5a1074d6ba5febbb08a`. Раздельно и явно.

### TTL scope
- TTL отсчитывается от фактического старта прогона; истёкший, но НЕ использованный scope штатно обновляется
  (новый scope = новая выдача, НЕ повтор принятого вызова).
- Canary не запущен и ждёт ТОЛЬКО контракта ExManager на supported peer SDK registration
  (собственный call_id peer-стороны) — без второго transport-вызова; новые базовые backend start/get/stop не ищу.
