# L10 / L12 — handover оркестратора (Mavis), 2026-10-24 03:40 MSK

Автор: Mavis (Lead-оркестратор зоны Notice Place). Собрано по фактическим замерам,
без переигрывания принятых/UNKNOWN-прогонов.

## 1. L10 — source закрыт, live = safety-stop

### 1.1 Source-фикс (закрыт NP-ребёнком)

- commit `d1cf189355219bdc115ad55eb95f9bfbc29f99b8` — «L10 repair: an explicit Matrix only
  policy constrains the whole incident», author `Mavis <Mavis@local>`, Sat Oct 24 01:26:37 2026 +0300.
- Файлы: `.agents/tasks/20261023-voice-call-experience/l09-l10-noticeplace-receipt.md` (+247),
  `notification_center/core.py` (+66), `notification_center/incident_call_chain.py` (+10),
  `tests/test_consumer_policy.py` (+41), `tests/test_incident_call_chain.py` (+83).
- Тесты: 44 passed (chain-wide deny любого не-Matrix транспорта + deny successor'а цепочки +
  3 deny-регрессии).
- ВАЖНО: commit локальный. `main` на server-100 опережает `origin/main` на 4 коммита
  (`748c2d1`, `d1cf1893`, `33ce523`, +1) — в origin он ещё не попал. Пуш не делал:
  решение о раскатке — за координатором.

### 1.2 Live-приёмка: safety-stop (owner-bound адресация)

- `notification_center/http_api.py:352-355` — `principal=owner` для matrix-вызовов;
  `notification_center/matrix_calls.py:71-85` — допускаются только owner-аккаунты.
- Следствие: инициированный из Notice Place звонок в изолированную L02-комнату
  `!ZatzpywRZSemgAaPju` не доходит и не должен (звонить владельцу запрещено).
- Остаток L10 = scope-адресация под проект `noticeplace-qa-voice-20261024`,
  аккаунт `@lhc_voice_tester_b`, комната `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`.
  Зона — UserIO-исполнитель, не оркестратор.

### 1.3 Моя попытка обходного пути (mavis07) — звонок НЕ состоялся

- Peer (сторона B) поднят в контейнере `userio-voice` из `/tmp/qa_candidate`
  (скрипт `matrix_rtc_ai_live_peer.py`, md5 `aebb2f089eb0992473df2e9a754820ab`),
  `AGENTCALL_QA_SCOPE` не задан → fail-closed, гостевой путь без ExManager.
- Peer поднял кандидат-движок `/tmp/qa_candidate/agentcall_realtime_bridge.py`,
  md5 `36c60ea961778172d6b43ef7eb3d1337` (установленный в рантайме — `bece40a7a2f0ad1fd34ab1bcaf52dea4`).
- Лог `/tmp/mavis07_peer.log` (контейнер `userio-voice`):
  `engine_module md5=36c60ea9...` → `ready` (fixture b, caller
  `@lhc_voice_tester_a_20261021`, комната `!ZatzpywRZSemgAaPju`, `reply_turns=2`,
  `model=omniroute/minimax-combo-m3.1`) → `timeout_waiting_for_fixture_call`.
- Лог инициатора `/tmp/mavis07_initiator.log` (контейнер `userio-backend`):
  `python3: can't open file '/tmp/start_fixture_call_mavis07.py': [Errno 2] No such file or directory`
  — скрипта в контейнере на момент запуска не было.
- ИТОГ: звонок не создан. Нет call_id, нет receipt'а, нет транскрипта.
  В `/var/lib/universal-userio/matrix-calls/` новейшая запись — `5642cedd-8b61-523c-9709-16cceb37384c`
  (21:38, это mavis06). Ожидаемых 3 новых артефактов (receipt/manifest/transcript) нет.
  Провайдерская квота на звонок не израсходована.
- `request_id=lhc_matrixrtc_20261024_a2b_mavis07` НЕ израсходован (call_id детерминирован от
  request_id, звонка не было) — свободен для UserIO-исполнителя, если он возьмёт этот путь.

### 1.4 Прогоны, которые НЕ переигрывать

| прогон | движок | исход | статус |
|---|---|---|---|
| mavis03 | установленный `bece40a7` | транспорт чистый, `errors=0`, но сторона A произнесла эхо инструкции | UNKNOWN (нет содержательного ответа) |
| mavis05 | кандидат `36c60ea9` | `qa_scope_admitted`, `voice_mode=secretary` → upstream HTTP 502 MiniMax | UNKNOWN (`NativeTurnUnsettled`) |
| mavis06 | кандидат `36c60ea9` | то же | UNKNOWN |
| mavis07 | кандидат `36c60ea9` | инициатор отсутствует → звонок не состоялся | НЕ СОСТОЯЛСЯ |

Новых принятых live-прогонов нет.

## 2. L12 — вопрос владельца по A2A-каналу (карточка создана, ответ pending)

- `human_request_create` + `noticeplace_ask_human` (карточка владельцу).
- `request_id`: `505a45c3-34dd-41a0-8562-013aeddc5260`, `state=pending`, `mode=choice`,
  `project=hermes`, `telegram_bound=true`.
- Варианты (три): `ready` «ГОТОВ — войду сейчас» / `later` «Позже — оставь ожидание» /
  `no` «Нет — не нужно».
- Приёмник ответа: opencode-сессия `ses_e94aaa5aeffeIp1fXKMi2ZZ4VS`
  (`noticeplace_qa_a2a_owner_q_20261024`, cwd `/home/roomhacker/agents-projects/noticeplace`).
  Отдельная сессия создана потому, что мост карточек Notice Place не поддерживает harness
  `minimax-code` (ошибка `native_target harness is not supported`).
- Незавершённая попытка `dde7d16e-0446-4211-a191-a744ab44b5c3` (привязка к minimax-code):
  карточка не отправлена, в `noticeplace_pending_questions` не значится → владелец видит
  ровно одну карточку. Запрос остаётся pending в Herder без карточки.
- Device-login код НЕ выпускался: ждём «ГОТОВ». По правилу — ровно один код, одна проверка.

## 3. Что НЕ сделано (честно)

- Живой звонок с содержательным вопросом не принят: ни одного нового принятого прогона.
- Фактическое прозвучание домена `notify.bezrabotnyi.com` и «ошибки 502» НЕ измерено
  (payload есть в greeting инициатора, голоса не было). Реально произнесённое не измерено.
- Перебивание без старой очереди (L04) — на уровне source-политики закрыто (`d1cf1893`),
  в голосе не подтверждено.
- Secretary → incidents → Secretary (в плане L11) не начиналось: зависит от L10 live.
- Нейтральный полезный ответ секретаря — блокер вне моего контура: у тестовых ботов нет строки
  в `exmanager_member_profiles` → модель не резолвится (`textBytes=0`, `streamErrorObserved=true`),
  плюс upstream-500 MiniMax (`mmcode/MiniMax-M3.1-Flash`, HTTP 502) → `NativeTurnUnsettled`.
  Исправление ведёт ExManager-сессия `mvs_f70ce650bbf94d789e4e4a2669f6743a`.

## 4. Закрыто и отдано ранее (остаётся в origin/main)

Консоль Notice Place — коммиты `b07856d3`, `a1bf797d`, `4e0f27c7`, `6e8dcbef`, `109b3bb2`,
`1cc6e920`, `74570509`, `54c6b1f1`. Деплой на server-100: `noticeplace-console-api.service`
на `127.0.0.1:8094`, откат в `/var/backups/noticeplace-console/<ts>/`, самотест
`noticeplace-console-selftest.{service,timer}`. Рендер-гейт `test/panel.test.mjs`:
7 маршрутов PASS локально, на 100 и по боевому пути с SSO; негативный тест воспроизводит
`rows.join is not a function`; сломанная панель отбита pre-flight'ом.
