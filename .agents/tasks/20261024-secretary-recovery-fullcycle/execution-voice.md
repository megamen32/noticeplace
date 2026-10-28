## 9. FIRSTHEARD — достигнут (попытка 6, 16:41–16:42 МСК)

Звонок изолированный: бот A `@lhc_voice_tester_a_20261021` ↔ бот B `@lhc_voice_tester_b_20261021`,
комната `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`, owner absent, fallback 0, bot-only store/secret,
`qa_scope` admitted (ttl 900). Фикстурный stub — только на стороне caller'а; ответ ассистента — только
живой SDK.

- Идентификаторы: acceptor/peer (фикстура b) — `voice_call_id 0b862871-d43a-45d2-a0ab-4949391875ba`;
  caller (бот A) — `6b76ed9c-b472-4e00-ad18-e5493ac7c3ad` (процесс caller'а завершился `CancelledError`
  на ожидании; сессия acceptor'а отработала полностью, `state=complete`).
- Движок: `/usr/local/lib/agentcall/agentcall_realtime_bridge.py` md5 `36c60ea961778172d6b43ef7eb3d1337`,
  gatewayd pid 765620, `127.0.0.1:9092` живой. Модель/маршрут `omniroute/minimax-combo-m3.1`
  (peer-событие `ready`), `fast-agent provider_admission elapsed=0.000s` — живой SDK;
  phone/model fallback не использовался (`owner_prefetch unavailable=ModuleNotFoundError` → fallback 0).

Engine log `/tmp/lhc-matrixrtc-live-peer/0b862871-d43a-45d2-a0ab-4949391875ba.engine.log`
(25 строк, sha256 `2de034b6fca75f4df1c0b22f7736af706b6596d44d5d65996d9df0ceba678764`):

- `heard: Наблюдатель безработный.com отдаёт код 502. Что это значит и что следует проверить?`
- `voice_mode call=… turn=1 mode=secretary`
- `voice_progress_latency utterance_end_to_progress_pcm_ms=4257.0`
- `secretary_sdk call=… turn=1 first_text_ms=6599 elapsed_ms=6621`
- `voice_latency first_text_to_tts_ms=6.5 first_tts_to_pcm_ms=185.0 first_text_to_pcm_ms=191.5`
- `voice_latency utterance_end_to_pcm_ms=9252.8 stt_ms=3629.3 turn_to_pcm_ms=6809.0 oldest_input_to_pcm_ms=19033.7 answer_kind=native`
- `ERROR NativeTurnUnsettled: Secretary voice outcome is unknown; inspect receipt` → `recording finalized;
  transcription status=QUEUED` → `session ended`.

Запись `/var/lib/agentcall/recordings/0b862871-d43a-45d2-a0ab-4949391875ba/` (root:root 0700):

- `manifest.json` sha `2a316b09a3276e782d32b9d2c683a5149c9059de56e62b8b44d01fb5b034d0c3`: `state=complete`,
  `complete=true`, `duration=45.2079375`, 48 kHz mono, `frames` 2169981, `recordedFrames` caller 2129 /
  assistant 1437, `trackDirections` caller=received / assistant=sent, 13:41:21.914Z → 13:42:06.501Z,
  `recordingFailure=""`.
- `assistant.wav` / `caller.wav` по 4 340 006 Б (sha `f0cd0791…` / `8c1a14a5…`).
- `transcript.txt` sha `d39d3a8a…` — полезный ответ 27.48–44.62 с: «По коду ответа 502 Bad Gateway на
  bezrabotnyi.com — это ответ фронтового nginx, то есть он не смог получить корректный ответ от
  апстрима…».
- `transcription-job.json`: `status=COMPLETE`, unit `agentcall-transcribe@0b862871-….service`.

Доставка ответа инициатору — подтверждена в журнале caller'а `/tmp/l21_caller_a.jsonl`
(`transcript_final`, `speaker=remote`): 1792849307.7479618 «Сейчас дайте мне подумать. Открываю контекст
вашего разговора…», 1792849319.7287292 «Звук идёт в обе стороны. Слышу вас по коду код ответа 502…»,
1792849525.1849084 «Стоит, жив ли процесс бэкенда и слушает ли он ожидаемый порт?…» → бот A услышал и
распознал ответ ассистента.

SDK/ledger receipt (100, read-only), схема `agent_c8b0c2c6dc99463fbaedff049b455be8`:

- `exmanager_voice_calls`: `call_id 0b862871-…`, `user_id 9b4ab5e7-…` (бот A), `transport=matrix`,
  `direction=incoming`, `mode=incidents`, `last_turn 1`, `created_at 2026-10-24 13:41:39.824336+00`.
- `exmanager_voice_turns`: `turn_id=1`, `state=completed`,
  `request_hash a62f60cd8d27ee217a926427c03d6040e320b460e47de57a4ff45b378692c563`,
  `reply` = «Звук идёт в обе стороны, слышу вас. По коду: HTTP 502 Bad Gateway на bezrabotnyi.com — это
  ответ фронтового nginx…», `created_at 2026-10-24 13:41:39.824336+00`.
- Отдельного run-id/receipt-файла в `/var/lib/agentcall` нет: носители receipt'а — recording-dir +
  engine.log + peer/caller jsonl (это и есть «inspect receipt»).
- Нюанс к приёмке: в ledger у call-строки `mode=incidents`, в engine-логе ход `mode=secretary`; ответ по
  содержанию — ответ Secretary.

## 10. Terminal cleanup 88 (17:04–17:10 МСК)

- `/etc/agentcall-secretary/identities.json` восстановлен к owner-only: sha
  `10073b8da0eb29fae3c400548d54299269fd17049f9a71aa1788bd875e9fe4e7` == бэкап
  `/tmp/l21_88_identities.before.json`; 2 записи (`phone:phone:v1:6330bf3d…`, `telegram:540308572`);
  временных matrix-записей тестер-ботов нет. Прод-карта owner phone+telegram не менялась.
- Фикстурный stub остановлен: `18199` не слушает, процесса `canary_fixed_llm.py` нет.
- `/etc/agentcall-ai-bridge.env` НЕ менялся: sha `aa8b612bcd4f5d1df61f55c604b5c387257b5c30e44669da80b129045d511157`
  == бэкап.
- Доказательства продублированы в `/var/lib/agentcall/canary-evidence/0b862871-…/` (каталог 0700, файлы 0600):
  `engine.log`, `peer_b.jsonl`, `caller_a.jsonl`, `manifest.json`, `transcript.txt`, `transcript.json`.
  Записи `0b862871-…` и `1b4e70b9-…` оставлены в `/var/lib/agentcall/recordings/`.
- Живой runtime 88 не тронут: 9092 (pid 765620), 18100 (node pid 7622), 7654, 30802 — слушают;
  `mesh-voice-autoupdate.timer` inactive/enabled (не трогал).
- Для повторного canary нужны две временные matrix-записи в identities.json: A →
  `9b4ab5e7-546e-4df9-a4c9-5c28f850cd26`, B → `0680a349-beb3-43fa-bd53-4e0363496a34`.

## 11. Состояние 100 и уточнённый gap

- Контейнер `userio-voice`: образ `universal-userio-voice:qa-mavis09-d4b631484679226fad0457ee9c1236ca9fc8f82a`
  (мост `6966c2b1…`), Up 2 hours. Rollback-контейнер `userio-voice-l21-rollback-20261024` — `Exited (0)`.
- `userio-voice-autoupdate.timer`: `inactive` (enabled) — остановлен мной, чтобы откат не ушёл на старый
  main; решение по таймеру/образу за Root.
- `/tmp/l21_new_release.json` СУЩЕСТВУЕТ (162 Б, mode 644, mtime epoch 0 → артефакт образа):
  `{"schema_version":1,"commit":"eab26997cdd627291fc30e9723d37d55cbf5f904","manifest_sha256":"8dd87894351ea80ea757a7262051a684f423ec3ac384ddaf679f412e4e09c31e"}`.
  Точный факт: прежний gap «файл не создан» закрыт, но содержимое относится к published-релизу
  `eab2699`, а НЕ к canary-образу `d4b63148/6fa9`; собственного release-receipt'а моей установки на 100
  нет — это отдельный (малый) gap на стороне Root.

## 12. Консолидация веток в последний main (проверено 17:0x–17:15 МСК, read-only + один ff-отказ)

- `origin/main` = `e202833dae91ad411d7cee8f6d147a0232047d3a` («Build portable voice from its constrained
  runtime dependency closure»).
- `git branch --no-merged origin/main` = **0**; все origin-ветки `behind=0 / ahead=0`. В main уже слиты:
  `voice-canary` (16c9741), `VK-poc` (c73eb1c), portable voice (e202833), `messages.prepare` (5f58aa8/377fd04).
- Локальный `main` = `1eaa665`, отстаёт на 39. `git merge --ff-only origin/main` отклонён **дважды и
  безопасно**: (1) untracked `tests/test_messages_prepare_no_send.py` перезаписывался бы; (2) живой WIP
  других сессий в общем worktree `/home/roomhacker/agents-projects/universal-userio` —
  `scripts/container_release.py` и `scripts/runtime_release.py` изменены 17:12 МСК (идёт rolling-release
  работа), плюс `contact_start.py`, `tests/test_*.py`, `AGENTS.md`, `.agents/TEST_ACCESS.md`,
  `tests/test_history_search.py`.
- Сделано без потерь: untracked `tests/test_messages_prepare_no_send.py` (байт-в-байт равен upstream)
  перемещён в `/home/roomhacker/wip-preserve-20261024-l19/`; устаревшие варианты Telegram reply-to
  (`server.mjs`, `adapters.py` — upstream содержит ту же фичу в более новой форме, с
  `forward-origin.mjs`/`send-receipt.mjs`) сохранены патчем `telegram-replyto-local-wip.patch` и копиями
  там же; устаревшие локальные варианты этих двух файлов сняты (`git checkout --`), чтобы не регрессировать
  дерево.
- Гонку с активным писателем не начинал. Fast-forward остаётся за владельцем source/Git (communications):
  одна команда `git merge --ff-only origin/main` после приземления активного WIP коммитом. 28 worktrees не
  трогал — решение об удалении отдельно.

# Execution — UserIO voice: 88 Matrix acceptor, первый полезный SDK-ответ со слышимым TTS

Отчёт по фактически выполненным действиям. Секретные значения и дампы env не приводятся.
Все времена МСК. Хосты: 100 (площадка публикации/источник), 88 (единственный runtime-приёмник).
Ветка/сорк-источник в этой линии НЕ трогаю (за /root/communications). Новых агентов/веток/таймеров не создаю.

### 12.1 Итог консолидации (17:20–17:25 МСК): fast-forward ВЫПОЛНЕН

- Локальный `main` `1eaa665` → `957baa04be34d38b0ccae9e5a59ec26f2e7f12bc`; `main == origin/main`,
  `git rev-list --left-right --count main...origin/main` = `0 0`, `git branch --no-merged origin/main` = 0.
- Все фичи/ветки — в последнем main. Либота `L19` (свести UserIO и голосовые ветки в main) закрыта по факту.
- Сделано без потери байтов: (1) два untracked-файла, побайтово равные upstream
  (`tests/test_messages_prepare_no_send.py`, `tests/test_container_release_source_revision.py`), перемещены в
  `/home/roomhacker/wip-preserve-20261024-l19/` (копии сохранены); (2) семь файлов, которые upstream меняет,
  а рабочее дерево уже совпадало с tip'ом побайтово (`scripts/container_release.py`,
  `scripts/runtime_release.py`, `src/universal_userio/contact_start.py`,
  `tests/test_first_contact_delivery.py`, `tests/test_http_api.py`, `tests/test_mcp_surface.py`,
  `tests/test_service.py`), — обновлён только индекс, содержимое не тронуто, затем снято `git reset`;
  (3) устаревшие варианты Telegram reply-to (`server.mjs`, `adapters.py`) сохранены патчем
  `telegram-replyto-local-wip.patch` — upstream содержит ту же фичу в более новой форме
  (`110d492 Add source-bound Telegram replies`, `e0a5d46 Telegram: source-bound reply_to on the durable receipt`).
- Осталось грязным намеренно (WIP владельцев не тронут): `.agents/TEST_ACCESS.md`, `AGENTS.md`,
  `tests/test_history_search.py`, untracked `.agents/wip/`, `.planning/…`, static assets, `var/`.
- 28 worktrees не трогал — решение об удалении отдельное.
- L43-факт (подтверждено сессией exmanager): развёрнутый коннектор `/opt/userio-telegram-qr/server.mjs`
  не содержит `reply_to_id` (grep = 0) ⇒ git-фича reply-to в main есть, но в работающем коннекторе на 100
  не выкачена; привязка ответа в пилоте пришла со стороны платформы UserIO. Это отдельный gap L43.

## 13. Handback Root (24.10, ~17:30 МСК): что принято, что сделано, что ждём

Принято как есть: FIRSTHEARD засчитан **только** как полезный 502-reply — A receivedPCM+ASR (caller-side
`transcript_final speaker=remote`) и SDK turn `state=completed` в ledger. Не приняты: domain/mode roundtrip,
actual downstream model route, final caller status. Итог §9 не расширяю — домен и mode в нём остаются
непринятыми.

### 13.1 WAV-фикстуры с доменом (без звонка)

Синтез — published speech helper на 88: `POST http://127.0.0.1:30802/synthesize` (Silero v4 ru, unit
`agentcall-tts-local`). Проверка — ровно движковый STT-запрос: `model=whisper-1`, `language=ru`,
`response_format=json`, `vad_filter=true`, `cache=false` на `127.0.0.1:7654` → `7653`
(`AGENTCALL_STT_URL` + `AGENTCALL_STT_API_KEY`, значение не выводится).

- `/tmp/caller_domain_clear.wav` — «Нотифай. Точка. Безработный. Точка. Ком.» — 195 044 Б, 24 кГц mono 16-bit,
  4.06 с, sha256 `0f9ad3c4821ddfe9f574ca493e95b30e6594dff3b559da6839ca2b3367e70234`
  → ASR: `natifi. Безработный.com`
- `/tmp/caller_domain_502_clear.wav` — «Нотифай точка безработный точка ком отдаёт код пятьсот два. Что это
  значит и что следует проверить?» — 311 444 Б, 24 кГц mono 16-bit, 6.49 с, sha256
  `b1083fbf8aab37ae283db72c722d5237fc400bd0bbc9ad890232efdc35cec54f`
  → ASR: `На tFi.bezrotcom отдаёт код 502. Что это значит и что следует проверить?` (часть с 502 — чистая)
- Копии (0600) в `/var/lib/agentcall/canary-evidence/fixtures/`. Ни одного звонка не сделано.

### 13.2 Измеренный root cause домена (не фикстура)

Published Silero ru произносит «Нотифай» как /natifi/, Whisper-1 пишет это латиницей `natifi`. Параметр
`language` не влияет: `ru` / без `language` / `en` дают одинаковый результат ⇒ дефект в произношении TTS, а не
в настройке STT. Латинский вход TTS не синтезирует вовсе (HTTP 500 на `notify.bezrabotnyi.com`), системный
espeak этим STT не читается (пустой результат).

Следствие: движковый spoken-label map (`_SPOKEN_LABELS`: `notify`→`нотифай`, `bezrabotnyi`→`безработный`,
`com`→`ком`; `_URL_RE` — только латинские метки) по STT-пути **не срабатывает**: на входе получается смесь
`natifi. Безработный.com`. Чтобы «нотифай точка безработный точка ком» дошло до SDK, нужен фикс TTS
(русское произношение заимствования) либо доменная подсказка в STT (hotwords/initial_prompt) — это
source-правка (communications/ExManager), а не фикстура. Фикстура даёт лучшее доступное: база домена
«безработный.com» слышна корректно.

### 13.3 Готовность cutover (исполнение — только после FINAL pinned digest + 44 rollback-ready receipt)

- Pre-flight (idle-guard): активных agentcall-звонков нет; Asterisk 0 каналов; gatewayd `9092` без активной
  сессии; карта портов до и после идентична — 9092 engine, 18100 matrix-call-bridge, 7654→7653 STT,
  30802 TTS, 18086 tunnel → 192.168.128.7:8080.
- Снимок до: рабочие unit'ы (`agentcall-gatewayd`, `agentcall-tts-local`, `noticeplace-voice-route`,
  `userio-secretary-sdk-tunnel`, matrix-call-bridge), `/etc/agentcall-ai-bridge.env`
  (sha `aa8b612bcd4f5d1df61f55c604b5c387257b5c30e44669da80b129045d511157`),
  `/etc/agentcall-secretary/identities.json` (owner-only, sha `10073b8da0eb29fae3c400548d54299269fd17049f9a71aa1788bd875e9fe4e7`),
  release-venv `/opt/noticeplace-voice/releases/l21-d4b63148`, текущий hostmode-образ.
- Cutover: existing container → portable image (hostmode → portable) тем же путём, что на 44; **без**
  отдельного hotpatch адаптера — правка ExManager `5de41fd`/`486a6487` входит в thin image вместе с
  init/stop-timeout installer fix.
- Верификация после: образ/движок совпадает с pinned digest; STT/TTS/SDK доступны; owner-map не изменён;
  один изолированный bot-call с раздельным received-audio, явным режимом, фактическим run/trace/correlation.
- Rollback: возврат старого unit + hostmode-контейнера + env/identities/venv по снимку. Старт при
  `rollback time < stop timeout` запрещён (на 44 был FAILED: 15 < 20) — сначала receipt от Root.

Запреты, принятые: новых QA-звонков до joined-артефакта; attempt6 не переигрывать; Git/source — только
communications (ff/reset/index/WIP-операции прекращены, ff957 отмечен как сделанный, грязные ветки и
worktrees не удаляю); 100 — standby, autoupdate на паузе, `/tmp/l21` receipt не credit; 44/100 standby;
звонки только 88; без HA; OmniRoute без изменений.

Также зафиксировано: развёрнутый telegram-qr коннектор на 100 отстаёт от `origin/main` на ~1080 строк и
~13 модулей (`forward-origin`, `send-receipt`, `contact-resolve`, `message-reply`, `message-edit`,
`session-auth`, `account-disconnect`, `dialog-label`, `telegram-admin`, `media-download-limit`,
`send-attempts`, `attachment-response`, `history-evidence`); в каталоге уже есть метка
`forward-origin.mjs.not-deployed`. Ручной copy в прод не делаю — это coherent-rollout (Root/communications),
а не hotpatch.

## 14. Приёмка пилота Telegram (ExManager) — подтверждено тремя слоями

- Поручение: `telegram:540308572|8810909089:1993488` (outgoing, owner, 1792848524 = 16:48:44 МСК) — «Секретарь,
  запомни: критерии пилота фиксируем письменно до среды… поставь напоминание 25 октября в 10:00 (МСК)».
- Видимый ответ: `1993489` (incoming, 1792848527 = 16:48:47 МСК) с `reply_to_provider_message_id = 1993488`
  ⇒ ответ привязан к исходному сообщению; в тексте — карточка v9→v10 с пунктом «Зафиксировать критерии пилота
  письменно до среды» и напоминание на 25.10.2026 10:00 МСК.
- Контракт prepare→send: `1993487` «📝 Черновик ждёт подтверждения» → `1993488` (отправлено) ⇒ без
  подтверждения отправки не было.
- БД 100 (read-only, схема `agent_c8b0c2c6dc99463fbaedff049b455be8`):
  `exmanager_reminders` — id `0537fceb-2918-4d49-8d17-970ca38231f4`, `user_id 5e438326-6339-4ac8-a328-59dae1d6241f`
  (owner), `due_at 2026-10-25 07:00:00+00` (= 10:00 МСК), `state=PENDING`, текст «Критерии пилота: зафиксировать
  письменно до среды», created 2026-10-24 13:29:01Z;
  `exmanager_memory_sources` — новая запись seq 286, `memory_id faef2efb-e646-450e-ad53-d8efa769fb18`,
  `user_id 5e438326-…`, topic `work:forenergo:pilot-criteria`, content «24.10.2026 Никита Розанов поручил:
  критерии пилота фиксирова…».
- Не проверено: карточка `93b9bef4` в `exmanager_team_task_cards` по id не найдена (0 строк) — версия 10
  подтверждена текстом ответа, но отдельную строку карточки в этой таблице проверить не удалось.

## 15. Cutover на portable-образ на 88 — prepare RED устранён (фикс + readback; 24.10, точные UTC из receipt: конфиг+ассеты 17:07:35Z, restart StartedAt 17:09:16.833189256Z)

- Операция: устранение функционального блокера контейнера `userio-voice` — в образе отсутствует банк
  `wait-prompts`, из-за чего `load_wait_prompts()` (`agentcall_realtime_bridge.py:2149-2157`) бросал
  `FileNotFoundError` и любой звонок через контейнер падал в `prepare()` до начала разговора.
- Способ: **штатный** `AGENTCALL_ASSET_ROOT` + уже существующий rw-bind `/var/lib/agentcall` (без пересборки
  образа, без hotpatch, без нового bind, без новых демонов, без звонков).
- Изменение конфигурации (ровно один ключ, `/etc/userio-voice/environment.json`, root:root 0600 сохранён):
  `"AGENTCALL_ASSET_ROOT": "/var/lib/agentcall"`; 33 → 34 ключа; sha256 **до**
  `e4c10881d9cfb9d16aad8ca91b3a05aa6dbfbfef21eceea580af39198cc3ae4b` → **после**
  `e0f63013f7965ede6eec1e1a4efb5b93516a7744d95a96efce9b0dfe80bd4ba0`; файл побайтово равен бэкапу плюс одна
  непрерывная вставка (бэкап: `/var/lib/agentcall/cutover-assets-20261024T170735Z/backup/environment.json`).
- Перенесённые ассеты (host `/var/lib/agentcall`, root:root, видны в контейнере через тот же bind):
  `wait-prompts/` — manifest.json + 30 WAV (31 файл), manifest sha256
  `1f001ed1be37ee6c0b05bd709eda85e4aa6b8cd77653b613079f1f189a330add`; `greeting.wav` sha256
  `0b6f1cf6ade114fcef9988abccbc9c9966487642be5d047a1a9023abf5592336`.
- **configAssetRoot (путь):** `/var/lib/agentcall`.
- Restart: `docker restart userio-voice` (restart, не recreate, не повторная установка). Контейнер healthy через
  7 с; образ не трогали.

### 15.1 Installed readback (после фикса)

- installed CID: `78990e584ab2df0b5ce2eca2779d78c68e254397cb8b7cfb28533bc32bd1865c` (restart ID не меняет).
- Образ: `ghcr.io/megamen32/universal-userio-voice@sha256:cdea3f878124267aed2fce266a48f6a09fbbf366794fbcdb4d8219dbd16e9317`,
  image id `sha256:52a5c881c21f61cece2731c498418bebaa72ac40c86bca5082adfa8c3933edae`; labels
  `com.userio.portable-voice=1`, `com.userio.role=calls-primary`, revision `b8b8a20c9cccdad67933423d2f442d4a6bfa5d4d`.
- Health=healthy, StartedAt `2026-10-24T17:09:16.833189256Z`, RestartCount 0, NetworkMode=host,
  RestartPolicy=unless-stopped, tmpfs только `/tmp:1777`; binds: `/etc/userio-voice:ro`, `/var/lib/agentcall:rw`,
  `/run/agentcall:rw`.
- `payload` rc=0 и `health` rc=0 (эти проверки возвращают код выхода, текст не печатают — по построению).
- Ровно один владелец 9092: `python` pid 2386508 на `127.0.0.1:9092`.
- Сокеты host-view: `control.sock`/`gatewayd.sock` — `root:notification-center` 0660, `incoming.sock` —
  `root:asterisk` 0660; container-view: `incoming.sock` — `root:147` 0660 (GID 147 = host `asterisk`).
- Native unit: `agentcall-gatewayd.service` — is-active=inactive, is-enabled=disabled (второго владельца 9092
  после ребута не будет).
- Отчёты: `/var/lib/agentcall/cutover-assets-20261024T170735Z/` (0700 root:root) — `readback.txt`, `sha256.txt`,
  `result.json`, `backup/environment.json`.

### 15.2 Исход prepare (безопасный локальный, без звонка)

- Проверка с env **живого** процесса бриджа (`/proc/<bridge_pid>/environ`, bridge pid 7; PID 1 — это
  `/sbin/docker-init`, а не бридж): `load_wait_prompts()` → **`wait_prompts_loaded 30`, rc=0**.
- Negative control: тот же вызов без переноса env → ожидаемо
  `FileNotFoundError: '/usr/local/lib/agentcall/wait-prompts/manifest.json'` (default asset root) — подтверждает,
  что фикс держится именно на env, а не на подмене пути.
- Полный `CallController.prepare()` с этим env, с Matrix-подобным `originate` (как в `matrix_call_worker.py:202`,
  где originate подменён на RTC-originate), без звонка и без внешнего LLM: **ok**, `msg_pcm=277800`,
  `attn_pcm=85800`, 0.3 с, `prepare` вернул `{ok, call_id, receipt_id}`.
- В контейнере: bridge sha256 `6fbcee13b727e0f3b129f152846ecf44e25f86b236984aadb7d76a00493811ba` (не изменён —
  hotpatch не применялся).

### 15.3 Отклонения / открыто

- Отклонение (не дефект поставки): `docker exec … env | grep AGENTCALL_ASSET_ROOT` даёт 0 — `docker exec`
  поднимает процесс только с image-level `Config.Env`, тогда как `serve()` применяет `environment.json` внутрь
  процесса бриджа (`voice_container.py:205`, `os.environ.update(runtime_environment(settings))`). Проверять
  надо env живого бриджа, а не `docker exec env`. Чтобы ключ появился в `docker exec env`, нужен recreate —
  запрещён (только restart).
- Наблюдение вне подготовки: primary TTS-тир (`android-phone` → `/usr/local/bin/s21-lease`) в контейнере
  отсутствует (бинарь только на хосте) → сработал штатный Silero-tier `:30802`; Asterisk-ветка
  (`/usr/sbin/asterisk`) в контейнере отсутствует, но Matrix-путь её не задевает (originate подменён).
  Ничего не чинил — вне назначения.
- Material callback Root: доставлен в его codex-сессию (`01a1653b-d774-73b3-9c0a-09cced76ea9e`), принят как `delivery=accepted`, `activated=true`, turn `01a16ef2-b23c-72f3-8307-074c840bd02a` (первая попытка была `skipped_inactive` — сессия была idle, доставлено с активацией).
- Открыто: новый функциональный Matrix-звонок не выполнялся (это решение не поручает звонок/SDK/HA);
  `AGENTCALL_ASSET_ROOT` не отдаёт наружу (только внутрь контейнера) — внешних потребителей не требуется.
- Первая попытка исполнителя прервалась (`tee` писал в root-only каталог от непривилегированного пользователя);
  каталог `/var/lib/agentcall/cutover-assets-20261024T170716Z/` оставлен с пометкой `ABORTED.txt` (не удалял).

## 16. Финальный образ 36ae на 88 — установка, readback и один изолированный Matrix-разговор (24.10, точные UTC из receipt)

### 16.1 Source (источник)

- Решение: `ghcr.io/megamen32/universal-userio-voice@sha256:36ae227964358da8316b1848bef91a107994637e168ce75d333af98ce31a3002`;
  source revision `86f53b7e19f8b1d86e032bb2370c260d9d772cc2` (подтверждён label `org.opencontainers.image.revision` установленного образа);
  manifest ref `deploy/containers/releases/voice-86f53b7.json`, manifest main `c59819fff2a3e5a7ae68c705ad7bae7f9cfdd781`;
  независимый review `advisor-phone-incoming-review.md` APPROVE86f53b, 51 focused GREEN.
- Superseded: `sha256:74b81bd9e9aebfb0cb26bb7470ba8a04a68942383e79aa36166e579d758e9fe0` — NOT_PHONE_READY, не финал.
- Источник правды: `portable-incoming-native-handoff.json` (0600, 5532 Б) прочитан полностью; rollback-цель — cdea3f87.
- Ассеты CAS (2 файла, доставка проверена по sha на обоих концах):
  `incoming-greeting.wav` 112 732 Б sha `3d876032b494f1ed0485b0a88f1c50299cf9c2a20ec0d1d50ee08883de6cb8c8`;
  `incoming-guest-greeting.wav` 551 642 Б sha `ea3aad3f48744d1bf05f5dcb59fba2e9b5f01dbf938ffb1856e6aca74f00c30b`
  → `/var/lib/agentcall`, 0600 root:root. Generic `greeting.wav` (`0b6f1cf6…`) и 30 wait-prompts не тронуты: aggregate
  `c02bdf55f64c4bd8bde3866914e773f027f77b8471e9acd751d57b2723aea9ab` до = после.
- Отклонение по источнику: literal source-пути handoff на 88 (`/usr/local/lib/agentcall/*.wav`) не существуют — symlink
  ведёт в corrections-каталог без WAV; байт-в-байт оригиналы найдены на host-100 (`/usr/local/lib/agentcall`), скопированы
  по CAS и удалены со staging.

### 16.2 Runtime (установлено и подтверждено на 88)

- Pull: `docker login ghcr.io` (секрет `arc-github-auth`, значение не выводилось) + `docker pull <digest>`;
  image id `sha256:ec7fcd803e6139ac068792fd50dd3abc7d144c4a0a0f7b96dc83e681c82cae36`.
- Установка: literal argv из handoff (`container_release.py install-voice --role calls-primary --name userio-voice
  --port 9092 --config-dir /etc/userio-voice --data-dir /var/lib/agentcall`) → receipt `status=installed`,
  `container_id=a6d69e1a0da0ecb600ac609ccc29e8aa045750744363e082bb059ea976e6d808`, `revision=86f53b7e…`,
  `previous=userio-voice-previous`.
- Перед установкой: бэкап `/var/lib/agentcall/cutover-36ae-20261024T181346Z` (0700) — `container-inspect.json`,
  `backup/userio-voice-etc` (5 файлов 0600), `backup/agentcall-secretary-etc`, `config-sha256.txt`, `config-stat.txt`,
  `asset-sha256.txt`, `asset-root-listing.txt`.
- Старый контейнер остановлен по точному CID `78990e584ab2df0b5ce2eca2779d78c68e254397cb8b7cfb28533bc32bd1865c`
  (exit 143) и переименован установщиком в `userio-voice-previous` — rollback-база сохранена.
- Readback: StartedAt `2026-10-24T18:16:08.323495785Z`, Health=healthy, Restart=unless-stopped, NetworkMode=host,
  binds `/etc/userio-voice:ro` + `/var/lib/agentcall:rw` + `/run/agentcall:rw`; `payload` rc=0, `health` rc=0.
- Ровно один владелец 9092: `python` pid 2908958 на `127.0.0.1:9092`.
- Сокеты host-view: `control.sock`/`gatewayd.sock` — root:notification-center (994) 0660; `incoming.sock` — root:asterisk (147) 0660.
- Native `agentcall-gatewayd.service` — inactive/disabled (второго владельца 9092 нет).
- Installed incoming prepare (owner + guest) на 36ae: ok, call_id `6e667323-bee0-417a-8adc-06dfaa56c0d7` / `b46996ef-d23f-443e-8502-6d878b295f30`,
  `mode=owner`/`mode=guest`, 0 звонков.
- Конфиг не изменён (побайтно те же sha): `environment.json` `e0f63013…`, `identities.json` `10073b8d…`,
  `secretary-voice.json` `6efe2cd8…`, `signing.key` `2b752e80…`, `voice.token` `9ddac65a…`.
- Фикс 36ae подтверждён в исходнике: `incoming-greeting.wav` и `incoming-guest-greeting.wav` резолвятся от
  `AGENTCALL_ASSET_ROOT`; в env живого бриджа: `AGENTCALL_ASSET_ROOT=/var/lib/agentcall`,
  `AGENTCALL_INCOMING_SOCKET_GID=147`, `EXMANAGER_VOICE_CONFIG_FILE=/etc/userio-voice/secretary-voice.json`
  (отсутствие `EXMANAGER_VOICE_URL` — не ошибка).

### 16.3 Consumer (один изолированный Matrix A/B-разговор)

- Движок разговора = установленный 36ae: bridge md5 `48e7de28aa4612fd3e1e00fd5c0e8350` (в контейнере); host-bridge не использовался.
- Fixture: peer = b (Secretary, движок внутри контейнера), caller = a; комната `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com`,
  ровно 2 joined, encryption=false; fresh request: peer voice_call_id `bdb29199-e7a7-4523-8ed0-3f2ad9fba713`,
  caller `2fbeb1f8-0558-4155-b4c7-3acb9c9299eb`.
- Изоляция: owner prod map не тронут (`identities.json` sha `10073b8d…` до = после); non-owner `matrix:`-identity подан
  только через приватный временный identities+config (`FixtureSecretaryScope`), QA scope TTL 900, deny-by-default.
- Вопрос (озвучен silero v4, 24 кГц; STT-контроль до старта): «Назови полный адрес службы уведомлений и объясни,
  что означает ошибка 502.»
- Доказано: `qa_admission=qa_scope_admitted`; peer услышал вопрос дословно; `voice_mode turn=1 mode=secretary`
  (реальный Secretary SDK, не stub); progress PCM отдан (`utterance_end_to_progress_pcm_ms=3359.1`); A принял PCM и
  распознал реплики Secretary («Сейчас сформулирую ответ. Открываю контекст вашего разговора.», «Сверяю информацию из
  ваших разговоров.», «Проверяю доступность.»).
- Terminal: peer завершился сам (`session ended`), caller RC=0 за 88 с (`ended` reason `no_recognized_speech_timeout`),
  все fixture-пути в контейнере удалены по точным путям, процессов не осталось; доказательства выгружены в
  `/var/lib/agentcall/cutover-36ae-20261024T181346Z/matrix-evidence/` (`peer_b.jsonl`, `caller_a.jsonl`, `peer.engine.log`).
- **Итог хода — UNKNOWN (реплей запрещён).** Peer: `ERROR NativeTurnUnsettled: Secretary voice outcome is unknown;
  inspect receipt; cause=ValueError: voice turn is not durably completed`. Durable receipt прочитан, не переигран:
  `execution_invocations` run_id `6f8c68a2-84f6-46a8-a2f3-34ffe11e7e60`, agent `c8b0c2c6-dc99-463f-baed-ff049b455be8`
  (eXmanager), created `2026-10-24T18:41:18.094764Z`, closed `2026-10-24T18:44:22.529097Z`, revoked_at — null;
  `llm_usage`: provider `openai-compatible`/`omniroute`, model `exmanager-codex-gpt-6-luna`, терминальная запись
  `18:44:22.498Z` — errored=t, 0 токенов, latency 103 394 ms; лог airlock `18:44:22.496` «MiniMax Code agent request
  timed out: session/prompt»; durable transcript
  `/home/roomhacker/.cache/airlock-deploy/model-transcripts/app-6f8c68a2-84f6-46a8-a2f3-34ffe11e7e60.jsonl`
  (102 строки: 9 model-steps с tool-call'ами; финал — text «Проверю точный адрес службы уведомлений по своим настройкам,
  чтобы назвать верно.» → error timeout).
- **Конкретное ожидание, закрывающее UNKNOWN:** terminal receipt хода с `callId=bdb29199-e7a7-4523-8ed0-3f2ad9fba713`
  в ExManager voice SDK (terminal `done` со `state=completed`). Он не наступил: brain-run закрылся по таймауту LLM
  (103.4 с) до финального текста, поэтому `invocation.closed_at` есть, а terminal receipt — нет. До его появления ход
  не переигрывать (accepted, не settled); повтор возможен только как новый запрос с новым callId.
- **Чего нет (не заявляется):** финального текста с `notify.bezrabotnyi.com` и разбором 502 — ответ не доведён до конца
  из-за таймаута brain-LLM на стороне SDK. Также в этом разговоре не проверены secretary↔incidents/context/interrupt:
  для них нужен живой brain-ответ.

### 16.4 Отклонения / открыто

- Harness читает БД по абсолютному пути `/var/lib/universal-userio/userio.sqlite3`; в контейнере путь создавался временно
  (копия 16 384 Б, 0600) и удалён после прогона. `docker cp` в tmpfs контейнера не работает — harness/БД/secrets
  доставлены через stdin (base64).
- Caller (a) закрылся с `no_recognized_speech_timeout`: его собственный guest-LLM путь упёрся в «Сервис ограничил ответы
  помощника» (provider admission на стороне a) — на ответ Secretary не влияет, но terminal-состояние a не стало ответным.
- Таймаут brain-LLM — на стороне SDK (host-100/airlock), вне зоны 88; owned fix не применялся (нет RED на 88, запрет
  новых ремонтов/волн).
- NP: карточка ExManager `93b9bef4` не опрашивалась (NP-работы остановлены по c57c ACK); ACK не заявляется без доказательств.

### 16.5 Закрытие NP-вопроса и переданное измерение (начало работ — по авторизации владельца, см. §16.6)

- Карточка `93b9bef4-035c-4e52-9c2c-a3f7c94d6240` (закрытый NP-вопрос «где хранится») — ответ получен от сессии ExManager
  (read-only измерение на host-100): хранилище — сама доска Todo/kanban, version=10, project «Олег и Никита», column=todo,
  description содержит «Зафиксировать критерии пилота письменно до среды» (v9→v10). Проекция ExManager:
  `agent_c8b0c2c6….exmanager_todo_team_task_links` — РОВНО 1 строка: board_card_id=`93b9bef4-…`,
  team_task_id=`0f7ba403-8218-5ec5-a090-fd81585a33ee`, team_name=forenergo, board_version=10,
  content_hash=`69ca0484503f66bcd0cf049af5eb056b4438086f6bd4e67796a6d8bd2c9dff27`, sync_state=READY, conflict_reason=null,
  last_seen_at=2026-10-24T13:29:20.897435+00:00. Отсутствие строки в `exmanager_team_task_cards` — другая схема ключей
  (там свой uuid + task_id + surface=ASSIGNEE_DM), а не признак отсутствия записи. Работы по NP не велись; ACK с моей
  стороны не заявляю — ждёт действующего окна.
- Передано измерение дефекта в моей зоне (ExManager измерил, правку не делал): «UserIO returned invalid JSON» — не битый
  JSON, а склейка ВСЕХ текстовых элементов ответа UserIO (`src/universal_userio/userio.go:292-305`). Сырые ответы:
  autogram-group и oleg-tg — по 9 элементов (5 текстов + 4 картинки) ⇒ конкатенация невалидна, при этом ПЕРВЫЙ элемент
  валиден; dmitry-sales-tg — 1 элемент, разбирается. Payload корректен (ok=true, 200 сообщений, peer_id совпадает).
  Минимальная правка: брать первый текстовый элемент / `structuredContent` и возвращать структурированную ошибку вместо
  плоской строки. Статус: измерение принято; по прямой авторизации владельца («Да, давай пофиксим», 24.10) работа
  начата и закрыта — фикс, RED/GREEN и границы публикации в §16.6.
- Пилот: приёмка 1993488 → 1993489, reply_to привязан, карточка v10, напоминание 0537fceb PENDING на 25.10 10:00 МСК,
  память seq 286 topic work:forenergo:pilot-criteria — подтверждено ExManager со своей стороны. Моя зона: cutover 88
  hostmode → portable выполнен (§16.2), один fresh изолированный bot-call выполнен (§16.3).

### 16.6 Фикс дефекта UserIO «invalid JSON» (по авторизации владельца «Да, давай пофиксим»)

- Источник дефекта уточнён: Go-клиент UserIO — это `airlock-agent/userio.go` в репозитории ExManager (модуль
  `github.com/megamen32/exmanager/airlock-agent`), функция `userioDecodeCall` (`userio.go` в origin/main, sha256
  `93096389d2cfd596ac1d9dfddb199f257c564dce725d406dd00c6a4a591af8cc`). Путь `src/universal_userio/userio.go` из
  измерения ExManager в этом репозитории НЕ существует: репозиторий `universal-userio` (host-100) содержит только
  Python (`src/universal_userio/*.py`) — Go-слой клиента UserIO живёт в ExManager.
- Механизм подтверждён по исходнику UserIO: `src/universal_userio/mcp_transport.py:117-128` — payload кладётся в ОДИН
  text-элемент и одновременно в `structuredContent`; `src/universal_userio/mcp_surface.py:471-479` — на КАЖДОЕ
  вложение добавляются caption-text + image-блок. Отсюда наблюдаемая форма ответа (1 JSON + 4 подписи + 4 картинки =
  9 элементов). Старый клиент склеивал ВСЕ text-элементы в одну строку и парсил её как один JSON ⇒
  «UserIO returned invalid JSON» на любом ответе с вложениями; тем же дефектом терялся машинальный код отказа
  (`userIOFailureFromResponse`).
- Изоляция правки: worktree `/home/roomhacker/agents-projects/.worktrees/exmanager-userio-json`, ветка
  `fix/20261024-userio-multi-element-json` от `origin/main` (320af11). Грязный основной checkout ExManager (129
  чужих незакоммиченных правок, ветка `fix/20261024-airlock-app-readonly-board-client`, b3e3531) НЕ тронут. Пакет `db`
  в origin/main не собирается без генерации: `sqlc generate` (1.30) → `internal/db`; недостающие query-файлы взяты
  из чужого WIP ТОЛЬКО для компиляции, в коммит НЕ вошли.
- RED (старое поведение, шаг «вынес декодирование в чистую функцию» без изменения поведения):
  `TestUserIOPayloadReadsAnswerWithAttachmentCaptions` → FAIL «UserIO returned invalid JSON»;
  `TestUserIOPayloadPrefersStructuredContent` → FAIL; `TestUserIOFailureFromResponseKeepsCodeWithAttachmentCaptions` →
  FAIL «machine reason lost: code=""».
- Фикс (минимальный): `userIOPayload` — приоритет `structuredContent`, иначе КАЖДЫЙ text-элемент парсится отдельно и
  побеждает первый валидный документ (вместо конкатенации); при отсутствии payload — структурированная ошибка
  `userIOPayloadError{Elements, Stage}` с сохранением прежней формулировки «UserIO returned invalid JSON» (классификатор
  `secretary_trace.go` по подстроке «invalid JSON» продолжает срабатывать) и без утечки содержимого ответа;
  `userIOFailureFromResponse` переведён на тот же разбор ⇒ машинальный код отказа (`chat not found` и т.п.) больше не
  теряется.
- GREEN: `go test . -run 'TestUserIOPayload|TestUserIOFailureFromResponse'` → 4/4 PASS (0.294s); `go vet .` чист;
  `gofmt` чист; соседние UserIO/Messenger-тесты — регрессия зелёная (0.236s). Коммит `310a6049fefcd55ff5e863ef62984eea60fe0a29`
  (Mavis <Mavis@local>), 2 файла: `airlock-agent/userio.go`, `airlock-agent/userio_payload_test.go`.
- Что НЕ сделано: публикация/деплой фикса (путь публикации ExManager требует действующего SDK owner-login —
  подтверждения нет, поэтому по умолчанию только фикс + focused-тест); коммит в чужой checkout; `legacy_capabilities.go`
  (legacy-путь «adapter returned invalid JSON») не трогал — вне измеренного дефекта; сервер UserIO не менялся — он
  отдаёт корректные данные, дефект в клиенте.

### 16.7 Terminal receipt хода `bdb29199-e7a7-4523-8ed0-3f2ad9fba713` (UNKNOWN — терминальный, реплей запрещён)

- Durable receipt найден в БД ExManager (контейнер `airlock-postgres-1`, БД `airlock`, роль `airlock`), схема
  `agent_c8b0c2c6dc99463fbaedff049b455be8`:
  - `exmanager_voice_turns`: call_id=`bdb29199-…`, turn_id=1, state=**unknown**, reply=``, request_hash=
    `66103f4c6f70c24d813b0cce9e45797186b936ce1b2ac7b407309aec43f10c51`, created_at=2026-10-24T18:41:18.037098+00:00.
    Допустимые состояния по CHECK: processing | completed | delegated | unknown.
  - `exmanager_voice_calls`: transport=matrix, direction=incoming, mode=secretary, user_id=бот A
    `9b4ab5e7-546e-4df9-a4c9-5c28f850cd26`, active_turn=0, last_turn=1 ⇒ активного хода не осталось.
- Итог: `unknown` — ТЕРМИНАЛЬНОЕ состояние, а не «в ожидании»: ход закрыт как unknown (агентский run в airlock закрыт
  ошибкой таймаута brain-LLM, `llm_usage` errored, 0 токенов, latency 103394 мс). Receipt `completed` по этому callId не
  появится: проверены `execution_invocations`/`llm_usage` и весь дамп схемы БД (2 вхождения callId — только эти строки).
- Следствие: критерий «полезный ответ с notify.bezrabotnyi.com и 502» по ЭТОМУ ходу не выполнен и выполнен быть не
  может — реплей запрещён; новый ход возможен только новым запросом с новым callId.
- В `input_text` receipt'а сохранён фактический текст хода: вопрос + «Хорошо, дайте мне подумать. Сервис ограничил ответы
  помощника. Сейчас не могу получить ответ. Можно продолжить позже?» ⇒ ограничение пришло от провайдера модели
  («Сервис ограничил ответы помощника»), а не от нашей логики.

## 0. Статус стрима и плановое окно звонка

- Состояние сейчас: **PREPARING → готов к запуску, ничего не звонит**. На 88: Asterisk 0 активных
  каналов/звонков, gatewayd pid 765620 слушает 127.0.0.1:9092, активного agentcall-звонка нет,
  принятых потоков не прерываю.
- Плановое окно одного свежего изолированного bot-call: **88, бот A ↔ бот B, комната
  !ZatzpywRZSemgAaPju:chat.bezrabotnyi.com, длительность ≤ 240 с** (лимит harness), без owner
  Matrix, без PSTN/GSM/SIP/телефона. Ориентировочно старт сразу после закрытия трёх пунктов §5;
  фактические start/end фиксирую в этом отчёте по завершении.
- JOIN с native SDK DEPLOY: текущий a28f6988 (image 4d36d499) СТАБИЛЕН; DEPLOY ждёт согласованного
  JOIN со мной, чтобы не прервать принятый turn. Я не начинаю никакой новый звонок для «проверки» и
  не повторяю UNKNOWN (9d929860 и 100-канарей 6b6d9ffb не реплею). Точные start/end моего звонка
  дам Root'у в отчётности — это и есть окно для DEPLOY после него.
- Elapsed по линии 88: старт сборки 15:35, сейчас ~16:25 → ~50 мин.

## 1. 88 — фактический readback (выполнено)

| Что | Значение |
|---|---|
| engine bridge | pid 765620, `127.0.0.1:9092`, файл `/usr/local/lib/agentcall/agentcall_realtime_bridge.py`, md5 `36c60ea961778172d6b43ef7eb3d1337` (источник: `/opt/noticeplace-voice/corrections/4ec71d7e32dd62212927f52008ec14e9b26cbb36/agentcall/`) |
| QA seam в мосте | 4 вхождения `qa_test_admission/qa_guest_promotion`, 2× `AGENTCALL_QA_SCOPE` → образ 6966 для canary НЕ требуется |
| SDK transport | `127.0.0.1:18086` (ssh-туннель pid 756257) → `192.168.128.7:8080`, SDK `a28f6988`, image `4d36d499` |
| STT | `127.0.0.1:7654` (nginx) → `0.0.0.0:7653` (pid 740234) — crosshost `192.168.2.75` |
| TTS | `127.0.0.1:30802` (silero, `agentcall-tts-local.service`) |
| matrix-call-bridge | `192.168.2.75:18100` (node pid 7622), dialer `dial-matrixrtc.mjs` — только слушает/держит RTC, вопрос не говорит (публикует tone) ⇒ caller для canary = готовый branch-caller, не dialer |
| mesh-voice-autoupdate88 | inactive/enabled — не трогал (уже paused, повторно не останавливал) |
| prod identities.json | sha256 `10073b8da0eb29fae3c400548d54299269fd17049f9a71aa1788bd875e9fe4e7`, только owner phone+telegram → `5e438326` — НЕ изменён |
| Asterisk guard | `0 active channels / 0 active calls` (4 calls processed всего) |
| before-снимки | `/tmp/l21_88_identities.before.json` (sha `10073b8d…`), `/tmp/l21_88_bridge_env.before` (sha `aa8b612b…`) — 0600, own paths |

## 2. 88 — установленные зависимости (выполнено, published pins, без system python)

- Release-venv: **`/opt/noticeplace-voice/releases/l21-d4b63148/venv`** (python3.10 venv, отдельный
  release-env в существующем deployment-dir; общий system python не трогал).
- Установлено (пины из `pip freeze` опубликованного образа `6fa9`): `livekit==1.1.20`,
  `aiohttp==3.14.4`, `websockets==15.0.1`, `pydantic==2.13.5`, `pydantic-settings==2.15.0`,
  `openapi-pydantic==0.6.0` + авто-зависимости (`aiofiles 25.1.0`, `protobuf 7.36.2`, `numpy 2.2.6`).
- Проверка: `from livekit import rtc` + `aiohttp/websockets/pydantic` → OK.
- **Причина «incomplete deps» (точная)**: первый `pip install livekit==1.1.20` отработал успешно, но
  `import aiohttp` падал. Причина НЕ в сети и НЕ в правах: `livekit 1.1.20` не объявляет
  `aiohttp/websockets/pydantic` своими зависимостями (только `aiofiles`, `protobuf`, `numpy`,
  `types-protobuf`), а в образе эти пакеты пришли от других зависимостей; мой `-q` скрыл это.
  Самый дешёвый следующий шаг — явная установка published-пинов (сделано, §2).
- **Причина ошибки sqlite (точная)**: в инлайн-скрипте сборки store был выставлен
  `row_factory=sqlite3.Row`, а строки индексировались по позиции (`r[0]`) → `TypeError: tuple indices
  must be integers or slices, not str`. Исправление: без `row_factory` (обычные tuple). Store собран.

## 3. 88 — подготовленные пути и хэши (готовы к запуску)

| Путь | Что | Hash/размер |
|---|---|---|
| `/opt/noticeplace-voice/releases/l21-d4b63148/venv` | release-venv с livekit | — |
| `.../pkg/universal-userio/src` | код из опубликованного образа `6fa9` (docker create+cp; git-копий/веток/worktree не создавал) | tar переноса sha256 `c37f887c976eaed28a26da9acdee13662ddd4dc367233fa07e05cdbe3c795015` |
| `.../harness/matrix_rtc_ai_live_peer.py` | acceptor (in-process, engine рядом) | md5 `0917ed8dad3c6c46448a5f0b9c7aecd8` = published commit `d4b63148` |
| `.../harness/matrix_rtc_ai_caller.py` | caller (бот A), фиксированная фраза | md5 `41b968db4d620e4e756afaaa156f06d4` = published commit `d4b63148` |
| `.../harness/canary_fixed_llm.py` | локальный fixed-phrase LLM-stub (второй ход caller'а) | готов |
| `/var/lib/universal-userio/userio.sqlite3` | bot-only store: 2 users / 2 provider_accounts / 2 user_ai_settings — ТОЛЬКО фикстурные боты, строк owner/telegram нет | 16384 B, 0600 |
| `/var/lib/universal-userio/connected-secrets/{96dfb84a…,3d06b949…}.json` | секреты двух фикстур (ключи: access_token/account_id/homeserver/matrix_user_id/provider/user_id) | 305 B каждый, 0600 |
| `/usr/local/lib/agentcall/agentcall_realtime_bridge.py` | мост под тестом (тот же, что крутит gatewayd) | md5 `36c60ea9…` |

Продуктовая карта (owner phone + telegram) и bot-only store НЕ смешаны: прод-`identities.json`
не изменён, peer-harness сам собирает временный identities-файл только для пары ботов.

## 4. Контракт harness (прочитан, определяет запуск)

- `matrix_rtc_ai_live_peer.py` = acceptor: ждёт звонок от второго бота в комнате, **сам строит
  bot-only QA scope** (`build_qa_scope`: entries caller/room/transport/participants/expiry 900 с +
  `promote_guest` + `identity_map` на не-owner uuid), кладёт его в `AGENTCALL_QA_SCOPE`, пишет
  временный identities-файл и временную копию protected-конфига, убирает `EXMANAGER_VOICE_URL`
  (движок берёт endpoint из config-файла) и печатает `{"event":"engine_module","file":…,"md5":…}` —
  это прямое доказательство, какой код моста отвечал.
- Аргументы: `fixture a|b`, `--room-id`, `--caller-id`, `--reply-turns 1|2`. Разрешённые caller'ы:
  две фикстуры (+owner, но owner НЕ используется). Проверка членства в комнате — иначе отказ.
- Фиксированная первая фраза caller'а: «Наблюдатель безработный точка ком отдаёт код пятьсот два.
  Что это значит и что следует проверить?» (говорится из WAV, LLM её не переписывает).
- Второй ход caller'а — через fixed-phrase stub (endpoint из `user_ai_settings`).

## 5. Что СЕЙЧАС мешает ОДНОМУ свежему изолированному bot-call (три пункта, все закрываемы)

1. **Нет WAV первой фразы на 88**: `/tmp/caller_first.wav` отсутствует → синтезировать на 88 через
   локальный silero: `POST http://127.0.0.1:30802/synthesize`, form `text=…&language=…&engine=…` →
   `audio/wav` (16-bit mono). Контракт подтверждён чтением `tts_local.py`.
2. **Второй ход caller'а**: endpoint в bot-only store указывает на реальный omniroute
   (`omniroute/minimax-combo-m3.1`) ⇒ чтобы не было LLM-перефразировки, переключить endpoint в
   bot-only store на локальный stub (`canary_fixed_llm.py`, 127.0.0.1:18199) либо вести звонок с
   `--reply-turns 1` (только фиксированная первая фраза).
3. **Env для in-process движка**: нужен экспорт runtime-env 88 (model/STT/TTS/endpoints) из
   `/etc/agentcall-ai-bridge.env` + `AGENTCALL_FAST_AGENT_FALLBACK_ENABLED=0` (fallback 0) в процесс
   canary; prod-конфиг не меняю, gatewayd не перезапускаю под canary.
   Отдельно: риск несовместимости harness `d4b63148` ↔ мост `36c60` (в ветке мост получил «свой
   дедлайн + причину»). При воспроизводимом RED (TypeError/AttributeError) — owned release-fix:
   versioned install моста `6966` из опубликованного кандидата `6fa9` (новая corrections/release
   директория + пере-линковка + рестарт ТОЛЬКО `agentcall-gatewayd` под guard «0 каналов»,
   откат = возврат ссылки на текущий corrections-каталог) → GREEN.

Никаких permission-гейтов нет: установка published-зависимостей и reuse release-venv уже разрешены.

## 6. 100 — состоявшаяся установка (факт, superseded, не откатываю)

- Мой install 15:25:57 (свой скрипт `/tmp/l21_voice_install.py`, 0700, свои имена/ID/rollback):
  контейнер `userio-voice` пересоздан на образ `6fa9` (image_id `6fa9b2ea1703…`), мост в контейнере
  md5 `6966c2b1…` (host RO-связка симлинка образ не перекрыла), `container_runtime --health` exit 0,
  9092 жив; rollback-контейнер `userio-voice-l21-rollback-20261024` (sha-c942e88, Exited(0));
  compose переведён на кандидата (before sha `4c143e3a…`); таймер `userio-voice-autoupdate.timer`
  остановлен мной (enabled сохранён).
- Exact gap: `/tmp/l21_new_release.json` НЕ создан — мой скрипт упал на раннем health-пробе уже
  ПОСЛЕ старта контейнера (rollback не сработал, потому что readback был вне try); фактический
  readback собран вручную и описан выше. Позже 100-rollout отменён владельцем; откат не делаю
  (blind rollback запрещён), решение по таймеру/образу на 100 — за Root.
- Кандидат `d4b63148` — ветка `mavis/voice-canary-mavis09-20261024`, не main; в voice-пути diff только
  мост (+81/−12), 6 backend-модулей в ветке старше main (voice-процессами не используются).

## 7. События/вызовы, которые НЕ повторяются

- Старый неadmitted call `9d929860` — не реплею.
- 100-канарей 13:55/13:56 МСК (`55b76b61…`, `6b6d9ffb…`) — не реплею; это QA-пара MatrixRTC.
- Auto-outgoing owner-drop: последние автоисходящие к личному Matrix владельца — `78aad791…`
  (23.10 14:56) и `c44b9524…` (23.10 13:11) — исторические тест-звонки, закрыты; pending-ретрая нет
  (agent_task_calls: последняя строка 23.10 14:28, budget_exceeded, attempts 1/1). Канал UNKNOWN
  документирован, повтор не делаю.

## 8. Секреты и персональные данные

Значения токенов/ключей, полные env и дампы не выводятся. В отчёт попали только имена файлов,
ключи JSON-структуры, хэши и версии. Bot-only store содержит только двух фикстурных ботов;
прод-карта владельца (owner phone + telegram) не менялась и не копировалась.

## 17. Checkpoint ZCode-наследника 27.10 (2026-10-27T00:15:43Z): приёмка не доказана — живой блокер brain-маршрута; звонков ноль

- Live readback 88 (read-only, ssh): `userio-voice` running/healthy, image_id `sha256:21dbf1cd7586…`, StartedAt `2026-10-26T04:08:31Z` (~20ч без рестартов), ровно один 9092, `l01-primary88-forward.service` active; это ревизия `1d1e2bb` (l01-scoped, отчёт L01 §6.4, установка 26.10 ~07:00 МСК с idle-proof, previous сохранён). `36ae/86f53b7` superseded — не откатывать (указание владельца). Висячий контейнер `gallant_tesla` (тот же образ, Up 2d) — не трогал.
- Приёмка «полезный финальный ответ СЛЫШЕН стороной A» НЕ доказана более свежей квитанцией: квитанции 26.10 (§6.9: call `736bb1fd` → изолированная комната, target `@lhc_voice_tester_b`, бот A caller, peer B ответил, приветствие 343200 байт, двусторонний PCM, completed, ack текстом бота B; §6.4: runtime readback SEARCH/READ внутри `1d1e2bb`) доказывают доставку/маршрут/чтение чатов, но не полезный финальный SDK-ответ голосом.
- Конкретный блокер brain-LLM (ран `6f8c68a2`, 24.10: «MiniMax Code agent request timed out: session/prompt», UNKNOWN — не повторяю) НЕ исправлен и воспроизводится сейчас в другой форме: в `~/.cache/airlock-deploy/model-transcripts` 223 error-only рана с 26.10 ~12:42 по 27.10 00:26 МСК, все `503 ALL_TARGETS_SKIPPED by pre-dispatch filters` (в т.ч. всплески 27.10 00:05/00:25/00:26). Успешные после 24.10 — только `exmanager-codex-gpt-6-luna` (6 ранов, последний 26.10 12:43 — чат ExManager, не голосовая сессия).
- Зона блокера: пре-диспетч фильтры/цели маршрута в OmniRoute (fleet routing). Активного владельца комбинации на борде нет; политику маршрутов менял владелец 21.10 (ban minimax-coding-plan в NP-контуре) и 26–27.10 (OpenCode + minimax-coding-plan, no-OmniRoute для NP-инцидентов — нота c96fda3e, скоуп NP-файлов). Смена/разбан цели brain-маршрута — решение владельца/Root; скрытая смена модели/маршрута мне запрещена.
- Сделано за checkpoint: только read-only (ssh inspect 88, транскрипты airlock, стат-файлы, herder/board ноты). Звонков/реплеев `bdb29199`/`6f8c68a2`/новых волн/новых сущностей — ноль. Ownership перед runtime-write проверен: активных владельцев файлов потока нет; установочный владелец 88 — продолжение L01 (np_secretary_glm), лист переоткрыт её Telegram-follow-up.
- Следующая операция: Root/владелец восстанавливает или явно подтверждает цель brain-маршрута (цели/фильтры OmniRoute) → затем ОДИН новый изолированный A/B звонок по чек-листу приёмки (TEST_ACCESS пара `lhc_voice_tester_a/b_20261021`, комната `!ZatzpywRZSemgAaPju`, живое доказательство изоляции: ровно 2 fixture-бота, owner absent, private; новые call/request/run ID, no replay), фиксация полезного финального ответа стороной A + terminal SDK/caller/peer/cleanup → запись сюда → STOP.

## 18. Продолжение 27.10 (сессия np_secretary_glm, OpenCode, zai-coding-plan/glm-5.3): найдена и устранена ротация SDK-учётки 88; последний метр — admitted-call

### 18.1 Дискриминирующий шаг: app (person QA) → execution_invocations/run → SDK-результат

- Персона QA в ledger: `principals.id=9b4ab5e7-546e-4df9-a4c9-5c28f850cd26` (`users.display_name=«Airlock Luna 4 QA»`,
  `tenant_role=user`), grant на app `exmanager` (`agent_grants.role=user`).
- Связка «голос → ledger» оказалась не в `execution_invocations.token_hash` (хеш 88-токена там не найден), а в
  `agents.id`: токен 88 — это **agent-API JWT** самого приложения: `agent_id/sub=c8b0c2c6-dc99-463f-baed-ff049b455be8`
  (= app `exmanager`, контейнер `airlock-dev-agent-c8b0c2c6…` = цель туннеля 18086 на 88), `aud=[airlock-agent-api]`,
  `iss=airlock/agent`, `profile=agent-api/v1`.
- Готового свежего completed-ответа нет: `runs`/`agent_conversations`/`execution_invocations` по QA-персоне — 0 строк.
  Значит разрешённое ОДНО text-поручение действительно нужно.

### 18.2 Точные квитанции (штатный транспорт, HMAC + bearer + application/voice caller)

| попытка | результат | вывод |
| --- | --- | --- |
| `POST /internal/voice/turn`, `callId`=fresh uuid4 | `HTTP 401 unauthorized` | учётка 88 отвергнута сервером |
| `POST /internal/voice/mode` (control, без модели), fresh uuid4 | `HTTP 401 unauthorized` | отказ на транспорте, не на payload |
| после починки (§18.3): `POST /internal/voice/mode` | `HTTP 403 voice call not admitted` | **аутентификация проходит**, сервер проверяет admitted-call |
| после починки: `POST /internal/voice/turn` | не выполнялась (control не принят) | ни одной модельной сессии не создано |

Ledger после попыток: `runs` по QA-персоне = 0, `agent_conversations` = 0, `execution_invocations` за 3 ч = 0,
`llm_calls` = 0. Никакой модели не запускалось, ничего не записано, повторов не было.

### 18.3 Корневая причина 401 и починка (в границах карты 88)

- `agents.agent_token_version` у app `exmanager` = **181**; в учётке 88 было `token_version=180`.
  Ротация — 26.10 04:50 (пересборка app: `agents.updated_at=2026-10-26 04:50:22`).
  Агентские токены версионируются и протухают за 7 суток; API требует `agent_token_version` == версия токена.
- Следствие: с 26.10 04:50 UTC **голосовой Secretary SDK с 88 не проходил аутентификацию** (401), т.е. путь
  «секретарь → SDK» на 88 был мёртв. Покрывающий 26.10 прогон это скрывал: A-нога отвечала через
  fast-agent incident-brain (`answer_kind=native`), а B-нога (peer вне приложения) в SDK не доходила вовсе
  (в её engine-логе нет ни одной строки `secretary_sdk`).
- Штатный источник учётки — надзираемый транспорт `userio-secretary-sdk-tunnel.service` (активен на 88,
  `/etc/agentcall-secretary/voice.token`, `upstream.json` = containerId `3c407508…`, imageId `sha256:ee6bc2fd…`,
  address `192.168.128.7`, `tokenVersion=181`, `pendingIdleReconnect=False`). Он обновляет файл только при смене
  снапшота (это не поломка: токен 181 актуален до 02.11).
- Развёрнутый gateway читал **статическую копию** `/etc/userio-voice/voice.token` (v180) — байт-в-байт та же,
  что разворачивалась 24.10 (`config-sha256.txt` релизного receipt'а: `9ddac65a…`), и уже отозванная ротацией.
- Починка (минимальная, только карта 88): refresh развёрнутой копии из надзираемого источника
  (`cp` содержимого `/etc/agentcall-secretary/voice.token` → `/etc/userio-voice/voice.token`, root:root 0600,
  атомарная замена), до этого — бэкап конфига и idle-guard (0 активных каналов asterisk, 0 call-событий движка
  за 10 мин, 0 подключений incoming.sock, 0 нетерминальных matrix-доставок в меше), затем `docker restart
  userio-voice` (тот же образ `sha256:526a2581…`, ревизия `b3520fe4`, без recreate). Readback: healthy,
  `token_version=181` в обеих копиях. Signing key и identities map **не менялись** (sha `2b752e80…` / `10073b8d…`
  совпадают с надзираемыми), прод-карта owner phone+telegram не тронута.
- Проверено и отброшено: вариант «читать напрямую `/etc/agentcall-secretary/voice.token`» — каталога нет в
  bind-наборе portable-контейнера (`/etc/userio-voice:ro`, `/var/lib/agentcall:rw`, `/run/agentcall:rw`),
  readback дал `FileNotFoundError`; конфиг восстановлен из бэкапа побайтово.

### 18.4 Последний метр: admitted-call (структурное требование, не обходится)

- Сервер принимает SDK-ход только для звонка, который начало **собственное голосовое приложение**: 24.10
  canary-сессия в gateway дала `accepted` + `voice_mode mode=secretary` (engine.log
  `/var/lib/agentcall/canary-evidence/0b862871…/engine.log`), а текущий `TEST_ACCESS` фиксирует то же правило:
  «synthetic fixture calls still cannot validate a Secretary turn because ExManager requires an admitted call record».
- Поэтому `fresh id` без живой сессии приложения → `403 voice call not admitted` (получено).
- Живой A↔B-контур L01 (A=бот A, B=бот B, комната `!ZatzpywRZSemgAaPju`) в 26.10 шёл через peer **вне**
  приложения (`qa-mavis09-voice` на 100) ⇒ его SDK-ход не адмитится ⇒ fallback на fast-agent ⇒ полезного
  финального ответа не было. Ровно это и воспроизведено 27.10.
- Модельный маршрут приложения **жив**: 236 успешных `runs` с `llm_calls>0` по `exmanager-codex-gpt-6-luna`
  за 3 суток (последний 27.10 06:00 UTC). Ошибки `503 ALL_TARGETS_SKIPPED` (223 рана 26–27.10) лежат в
  model-transcripts **airlock-deploy** (поверхность деплоя/чата), а не в ledger приложения; провайдер не является
  стоп-фактором для SDK.
- Уцелевшие детали канонического canary: фикстуры `/var/lib/agentcall/canary-evidence/fixtures/caller_domain_502_clear.wav`
  (вопрос про 502) и `caller_domain_clear.wav`; RTC-мост `192.168.2.75:18100` (node pid 7622) жив. Сам harness
  (branch-caller/dialer) лежит в `/home/roomhacker/matrix-synapse/call-e2e/scripts/` — каталог недоступен для чтения
  и не воспроизводится из доступных репозиториев; в `execution-voice.md` §10 зафиксировано, что повторный canary
  требует двух временных matrix-записей в identities.json (A → `9b4ab5e7…`, B → `0680a349…`) с последующим
  восстановлением owner-only карты.

### 18.5 Что НЕ делалось (по прямому запрету владельца)

Ни одного звонка человеку; PSTN/GSM/SIM/external SIP не использовались; личный Matrix и рабочая комната владельца
не участвовали; новых caller-архитектур и assistant-заглушек нет; тестовые инциденты не создавались; прод-ключи и
identity-байты owner не менялись; образ 88 не откатывался; accepted/UNKNOWN не повторялись.

### 18.6 Готовые следующие шаги (по одному, с решением владельца)

1. **Разрешить повторный canary в приложении** (собственный gateway = admitted-call): harness недоступен для
   чтения, нужен доступ к `matrix-synapse/call-e2e` (или его владелец запускает canary сам) + две временные
   matrix-записи в identities.json по §10 с восстановлением owner-only карты; вопрос — фикстура 502.
2. **Сделать durable учётку на 88**: добавить bind `/etc/agentcall-secretary:/etc/agentcall-secretary:ro` в
   portable-spec голоса (`scripts/voice_container.py create_spec`) и переустановить контейнер на 88 — тогда
   gateway читает надзираемый файл и ротация учётки больше не ломает путь; текущая копия протухнет на следующей
   ротации (после 02.11).
3. Отдельно: чтение `!Zatz…` живым `joined_members` (сейчас в комнате `@aiadmin` + бот A + бот B — временная
   запись `@aiadmin` от 26.10 восстановлена после теста; для strictly-2-бота её надо снять на время звонка).

### 18.7 Компактная запись переносимого опыта (self-improve)

- Наблюдение: у клиента AirLock agent-API токен версионирован (`token_version` в JWT) и отзывается ротацией
  `agents.agent_token_version` (пересборка app). Копия у клиента молча умирает с `HTTP 401`, при этом
  `exp` ещё в будущем — проверять «срок» бесполезно.
- Исправление (метод): перед любым SDK-действием — (1) декодировать claims локального токена
  (`agent_id`, `aud`, `token_version`), (2) сверить `agents.agent_token_version` в ledger, (3) взять учётку из
  надзираемого транспорта (`upstream.json` = containerId/imageId/tokenVersion), а не из локальной копии.
- Наблюдение: «одна разрешённая попытка» сгорает, если не прочитан контракт клиента: правило admitted-call
  берётся из `exmanager/docs/voice-secretary.md` и из `TEST_ACCESS`, а не из догадки.
- Проверка: §18.2 (401 → 403), §18.3 (починка + readback v181), §18.4 (admitted-call 403 + живой brain-маршрут),
  §18.5 (нулевой побочный эффект в ledger) — переиспользование применимо к любому SDK-клиенту AirLock.

## 19. 27.10 (продолжение L01, сессия np_secretary_glm): долговечная учётка + доказанный admission + секретарский SDK-ответ

### 19.1 Дефект ротации учётки устранён долговечно (не статической копией)

- Причина `HTTP 401` на каждом Secretary SDK-запросе с 88: локальная копия agent-api токенаv180 против `agents.agent_token_version=181` (ротация 26.10 04:50 при пересборке приложения). Токен 7-суточный, версионируемый; срок его не истёк — отозван номер версии.
- Починка по контракту portable-spec: `universal-userio/scripts/voice_container.py` — bind надзираемого источника `/etc/agentcall-secretary` (ro) для роли `calls-primary` (только там, где каталог есть); конфиг 88 переведён на надзираемые `token/signing.key/identities.json` (key и identities побайтово те же, sha `2b752e80…`/`10073b8d…`), так что ротация учётки больше не ломает путь.
- Установка на 88: native idle handoff (0 asterisk-каналов, 0 call-событий 10 мин, 0 incoming.sock, 0 нетерминальных matrix-доставок) → `docker stop` → `install-voice` тем же образом `sha256:526a2581` (rev `b3520fe4`, 36ae не ставился) → readback: health=healthy, bind присутствует, rollback-контейнер `userio-voice-previous` сохранён, `token_version=181`. Focused-тесты `tests/test_portable_voice_release.py` — 13 passed (+2 новых на bind).

### 19.2 Приёмка: изолированный Matrix-разговор, admission и секретарский SDK-ответ

- Контур: строго двух-ботовая комната `!vmxuOOunnmTUeuEdvi` (создана ботом A, участники только бот A и бот B; owner, @aiadmin и люди — нет; @aiadmin из прежней комнаты НЕ удалялся), QA-скоуп (политика mesh + routes UserIO) переведён на неё. Нога-приёмки — внутри УСТАНОВЛЕННОГО контейнера `userio-voice`, движок `md5 48e7de28aa4612fd3e1e00fd5c0e8350` (установленный образ `b3520fe4`; исторический host-bridge `36c60ea9` не использовался), `AGENTCALL_FAST_AGENT_FALLBACK_ENABLED=0`; import-preflight подтвердил engine hash, источник `/opt/universal-userio/src`, фикстурные креды, `sdk_config` = надзираемая учётка v181.
- Три RED, каждый закрыт GREEN-ом без звонка: (1) power levels новой комнаты — бот B не мог публиковать RTC-membership (`403 user_level(0) < send_level(50)`) → выровнено с эталонной QA-комнатой (`events: m.call.member=0`); (2) релизный harness peer жёстко ограничивал комнату (`room != QA_SCOPE_ROOM` → ValueError) → в моём экземпляре deny-by-default привязка новой комнаты; (3) идентитет-карта без matrix-привязки QA-пары (`fixture_identity_not_scoped`) → временные scoped A/B-привязки (владельцем разрешены) с побайтовым бэкапом `identities.json` (sha `10073b8d…`) и побайтовым восстановлением после прогона.
- Секретарский ход (без incident-контекста, штатный входящий контракт): app ledger `exmanager_voice_calls` = `call_id 74e8df54-0b08-4d6c-ad45-04b9b4fffda4`, transport=matrix, direction=incoming, **mode=secretary**, `user_id=9b4ab5e7…` (QA-персона); `exmanager_voice_turns` = **state=completed**, input = «Представься и скажи, чем ты можешь мне помочь как голосовой секретарь?», reply = «Здравствуйте! Я голосовой секретарь: веду личные дела — напоминания, задачи, сохранённые заметки — и при необходимости подключаюсь к проверке состояния сервисов на bezrabotnyi.com и связанных доменах». Engine-лог ноги: `voice_mode … mode=secretary` → `secretary_sdk call=74e8df54 turn=1 first_text_ms=31487 elapsed_ms=31514` → `voice_turn … mode=secretary` (перехода в incidents НЕ было).
- Отдельно, ранее: ход `32498f6b` (mode=incidents, reply incident-brain) — это доказанный **admission**, но НЕ секретарская приёмка; по границе владельца не засчитан. События accepted→done сняты, реплея нет.
- Изоляция и побочные эффекты: реальных телефонных наборов — 0 (`phone.call`/`android.phone.call` за весь период — 0), `telegram.call` — 0, owner/личный Matrix/@aiadmin в контуре — нет, QA-инциденты закрыты, identity-карта восстановлена побайтово (sha совпал), harness-процессы остановлены (0), комната осталась строго двух-ботовой.
- Честная граница: финальный ответ SDK на стороне A записан не полностью — harness-caller закончил звонок по `no_recognized_speech_timeout` (его собственный STT не распознал аудио), поэтому в записи A видны приветствие и progress-фраза, а не весь текст ответа; сам ответ durably completed в ledger приложения и произнесён ногой B (её `assistant.wav` в `/var/lib/agentcall/recordings/74e8df54…/`).

### 19.3 Что НЕ делалось

Ни одного звонка человеку; PSTN/GSM/SIM/external SIP — нет; личный Matrix и рабочая комната владельца не участвовали; новых caller-архитектур и assistant-заглушек нет; образ 88 не откатывался; accepted/UNKNOWN не переигрывались; прод-ключи и identity-байты owner не менялись; чужие процессы/права не тронуты.

### 19.4 Секретарская приёмка: ответ произнесён и услышан A (финальный живой прогон)

- Что не хватало в 32498f6b/74e8df54: ответ вычислялся (app ledger `completed`), но **не был озвучен** — harness ноги-B закрывал сессию сразу после текста ответа (до TTS), а нога-A обрывала звонок по «речь не распознана». Оффлайн-разбор принятых записей (без модели и без реплея) это показал: дорожка A содержала только приветствие и progress-фразу, а дорожка B обрывалась до TTS (`first_text_to_tts_ms` отсутствовал).
- Два фикстурных дефекта, оба в моей зоне, закрыты точечно:
  1. **нога-B (speaker)**: stop-условие peer-harness срабатывало на тексте ответа → добавлено окно воспроизведения (22 с) до закрытия сессии;
  2. **нога-A (listener)**: терпение к тишине (`AGENTCALL_END_SILENCE_SECONDS=150`) и никаких дополнительных LLM-вызовов caller'а (endpoint фикстуры A — локальный fixed-phrase stub, LLM не вызывается).
- Финальный живой прогон (одна пара, current installed engine, та же изоляция): A произнёс нейтральный вопрос «Представься и скажи, чем ты можешь мне помочь как голосовой секретарь?»; app ledger: `call_id a4ec5d98-caf9-42ac-b65b-98c7f7ab73f5`, transport=matrix, direction=incoming, **mode=secretary**, `user_id=9b4ab5e7…` (QA-персона), turn **state=completed**, reply = «Здравствуйте! Я голосовой секретарь: веду ваши личные дела — напоминания, задачи, заметки…»; engine-лог ноги-B: `secretary_sdk … elapsed_ms=3267` → `voice_turn … mode=secretary` → `voice_latency first_text_to_tts_ms=9.5 first_tts_to_pcm_ms=212.8 … answer_kind=native` ⇒ ответ **синтезирован и отправлен в сеть**.
- **Доказательство, что ответ услышал A** (офлайн-транскрипция дорожки, полученной A, `/var/lib/agentcall/recordings/afdf475e…/timeline.md`): сегмент 10.33–26.28 с в колонке «Собеседник» = «Одну секунду, пожалуйста. Открываю контекст вашего разговора. **Здравствуйте. Я голосовой секретарь. Веду ваши личные дела, напоминания, задачи, заметки и при необходимости подключаюсь к рабочим системам**…» ⇒ полезный секретарский ответ **услышан caller'ом A**.
- Изоляция и cleanup: комната — ровно два фикстурных бота (owner absent, @aiadmin absent, людей нет); телефонных наборов 0, `telegram.call` 0; identity-карта восстановлена побайтово (sha `10073b8d…` == бэкап); harness-процессы и стаб остановлены (0); 88 voice healthy, ревизия `b3520fe4`, надзираемая учётка v181, три юнита active.
- Правки в репозитории (мои пути, main запушен): `universal-userio` `6c88e5b` (portable-spec bind надзираемой учётки + 2 focused-теста) и `ea21395` (peer-harness: окно воспроизведения ответа).

### 19.5 Границы (честно) — уточнено по фактическому аудио

- **Вопрос, который произнёс A, — нейтральный.** Три независимых источника сходятся: оффлайн-ASR собственной дорожки A в `recordings/afdf475e…/timeline.md` (сегмент 5.21–8.58 с = «Представься и скажи, чем ты можешь мне помочь как голосовой секретарь?»), `input_text` хода в app ledger и строка `heard:` ноги-B — все дают нейтральный вопрос. Строка живого лога harness-caller'а с текстом про 502 относится к предыдущему прогону (call `634ac559`), а не к финальному `afdf475e`; при разрешении спора опираюсь на аудио, а не на эту строку.
- Ответ секретаря (app ledger, ход завершён): «Здравствуйте! Я голосовой секретарь: веду ваши личные дела — напоминания, задачи, заметки, — и при необходимости подключаюсь к рабочим системам и агентам, чтобы проверить состояние сервисов или найти нужную информацию. С чего начнём?»; в дорожке, полученной A, — «Здравствуйте. Я голосовой секретарь. Веду ваши личные дела, напоминания, задачи, заметки и при необходимости подключаюсь к рабочим системам…».
- Терминальные состояния: A завершил звонок по собственному `no_recognized_speech_timeout` уже после получения ответа; B закрыл сессию после отыгранного ответа; harness-процессы ног A/B и стаб остановлены, временные scoped identity-привязки сняты побайтово, комната осталась строго двух ботов.
- Пакет фактических доказательств (без секретов) в этом же каталоге: `l01-secretary-acceptance-evidence.json` (call/turn/llm_usage/run, room, account_refs, терминалы, установленный digest, изоляция) и `l01-caller-a-timeline.md` (офлайн-транскрипция дорожек A, 0600).

## 20. 27.10 (продолжение, сессия np_secretary_glm): постоянный handler на @aiadmin — установлен, приёмку чинит один дефект каленой ноги

### 20.1 Фактический checkpoint последнего изолированного прогона (readback, до следующего ring)

- **Кто звонил / кто принимал:** caller `botA` = `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com`
  (аккаунт `matrix:ab023a689e926bf99d924e933ebd`, user `user_b0fa237eee8f4793aad037971be582a3`);
  callee `botB` = `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`
  (аккаунт `matrix:37698708b623db45e1bfe61f4d01`, user `user_ee857eff8b2e45f3887a33db98441519`);
  комната `!vmxuOOunnmTUeuEdvi:chat.bezrabotnyi.com` — ровно два фикстурных бота, owner/@aiadmin/людей нет.
- **Отвечал ли именно новый штатный handler: да.** Intake нового установленного backend (`8a6d62d`) увидел RING,
  записал долговечный claim-receipt `4d0afc5f-23df-5231-8f57-a9d3c05ca7fc` (22:21 MSK) и отправил запрос
  по приватному control-маршруту на 88; отвечала новая каленая нога (`voice fd448ae`, action
  `matrix_answer_transport`). Голосовой id прозвонка A: `e924249c-d854-40b8-b4d8-567f8eaa7027`
  (A: `timeout_waiting_for_answer`).
- **Первый полезный SDK-ответ: НЕТ.** Ход SDK не начался: до голоса дело не дошло.
- **Достигнутые режимы: НЕТ** (mode не зафиксирован, ни `secretary`, ни `incidents`).
- **Точное исключение и первая причинная строка:** `KeyError` (receipt `status=unknown`,
  `result={"type":"error","error":"KeyError"}`). Первая причинная строка — контракт надзираемого секрета
  в SDK-адаптере `agentcall_secretary_voice.private_file`:
  `path = Path((settings or os.environ)[name])` — каленая нога передавала в него только поля голосового
  конфига, поэтому ключ `EXMANAGER_VOICE_IDENTITIES_FILE` отсутствовал. Это мой дефект в новой ноге, не
  дефект окружения.
- **Почему RING был виден в этом прогоне (и не виден в предыдущем):** дефект читателя. Call-membership —
  это state-событие, публикуемое ДО ring, поэтому оно не приходит в той же дельте `/sync`; читатель искал
  его только в timeline этой дельты и молча ронял звонок, уводя курсор вперёд. Ring теперь разрешает
  свою membership по событию, на которое сам ссылается (`m.relates_to.event_id`), с room-state как
  fallback; regression: MEMBER в poll N, RING в poll N+1 → ровно один admitted call.

### 20.2 Что чинится дальше (только этот дефект)

- `71f5ab7` (voice, опубликован): каленая нога разрешает карту личностей по надзираемому секрету хоста —
  ровно как это делает мост; плюс hardening `account.get(...)`. Образ `sha256:c0fbefae…` собран, **ещё не установлен**.
- Следующий дефект того же handshake (по коду, до ring): **UUID-first**. В каленой ноге `capture` стартует
  на `room.connect`/`track_subscribed` и сразу пишет PCM в `audio_reader`, а UUID-кадр кладётся только
  ПОСЛЕ connect, публикации membership и синтеза приветствия ⇒ ранний звук может обогнать UUID, и движок
  (`handle_call` требует UUID первым кадром) отклонит кадр. Работающий `matrix_call_worker` решает это
  гейтом `transport_ready` (receive ждёт готовости, UUID кладётся ДО `transport_ready.set()`). Нужен
  оффлайн race-test: ранний входящий PCM во время подготовки не обгоняет UUID.

## 21. 27.10 (продолжение): постоянный приём @aiadmin — ДОКАЗАНО; incidents-roundtrip deferred_by_owner_priority

### 21.1 Доказанная квитанция (без нового звонка)

- **Главная квитанция приёма (intake, backend 8a6d62d):** `39688964-49ba-5891-a793-b624e166df4b` —
  `status=sent`, `answered=true`, `result.state=answered`, `result.turns=24`,
  `result.callId=07c4c83a-9a6b-5b3e-8748-d08fa409d609`,
  `person=9b4ab5e7…` (QA-персона), комната `!vmxuOOunnmTUeuEdvi:chat.bezrabotnyi.com`
  (только два фикстурных бота), аккаунт-получатель `matrix:37698708…` (@lhc_voice_tester_b).
  **Отдельная квитанция другого разговора:** `65affd09-b496-5a9a-ab18-7350e3c958d7` →
  `result.callId=6d8b7cd3-9a25-58b7-a2d8-b0ece1765629` (`answered=true`, `state=answered`, `turns=16`,
  20:13Z) — она доказывает приём отдельным разговором, но **не** связана с аудио A (см. ниже).
- **SDK ledger по B = `07c4c83a…` (тот же разговор, что и A=`bb873f0f…`):** ход **1 = `completed`**,
  `created_at` хода 20:08:41Z, `input_text` = «Представься и скажи, чем ты можешь мне помочь как голосовой
  секретарь?», `reply` = «Здравствуйте, я голосовой секретарь. Помогаю с личными делами: напоминаниями и
  задачами, поиском по сохранённой памяти и рабочей переписке, а также переключением на разбор инцидентов.
  Скажите, что нужно сделать.»
  Корреляция по времени: запись A стартовала 20:08:31Z, ход B создан 20:08:41Z ⇒ это один разговор.
  У B=`6d8b7cd3…` (20:13Z) **другой** ответ на тот же вопрос — в аудио A его нет.
- **Аудио, полученное A** (происхождение дорожки проверено по коду recorder'а:
  `agentcall_recording.py` — `incoming()` → трек `caller` (**получено**), `outgoing()` → `assistant`
  (**отправлено**); в манифесте записи `trackDirections: {caller: received, assistant: sent}`):
  запись **ноги A** того же разговора — `recordings/bb873f0f-a8de-40c1-96d2-e8bf09750075/`
  (A-движок; B-нога этого разговора — `07c4c83a…`, `startedAt` 20:08:31Z ↔ ход B 20:08:41Z),
  `state=complete`, `direction=incoming`,
  `peerRef=!vmxuOOunnmTUeuEdvi:…`, `peerLabel=@lhc_voice_tester_b_20261021…`.
  В её оффлайн-транскрипте (`timeline.md`) колонка «**Собеседник**» — это полученная речь B:
  0.02–2.25 с «Я голосовой секретарь. Слушаю вас.»; 10.58–14.38 с «Дайте мне ещё немного подумать.
  Открываю контекст вашего разговора.»; **16.44–26.66 с «Здравствуйте, я голосовой, секретарь. Помогаю
  с личными делами, напоминаниями и задачами, поиском по сохранённой памяти и рабочей переписке, а также
  переключением на разбор инцидентов. Скажите…»**; 28.31–29.42 с «Что нужно сделать?».
  В колонке «Наш помощник» — собственная речь A: 5.21–8.58 с = «Представься и скажи, чем ты можешь мне
  помочь как голосовой секретарь?» ⇒ **A услышала полезный ответ Secretary** (а её вопрос виден рядом).
- Уточнение к прежней записи: `recordings/6d8b7cd3…/assistant.wav` — это **отправленная** речь ноги B
  (её `trackDirections` = assistant: sent), т.е. подтверждение того, что B произнесла ответ, а НЕ доказательство
  приёма звука A. Доказательство приёма — дорожка `caller` записи ноги A (`bb873f0f…`) выше.
- Терминалы: сессия 88 закрылась штатно, caller завершил по своему таймауту тишины, UNKNOWN не переигрывался.

### 21.2 Прод-readback (без звонка человеку)

- **Allowlist:** в core-процессе backend `USERIO_MATRIX_INBOUND_ACCOUNTS=@aiadmin:chat.bezrabotnyi.com`
  (только аккаунт владельца; QA-калле убран), секрет `@aiadmin` + homeserver `chat.bezrabotnyi.com` доступны.
- **Постоянная Matrix-identity владельца:** в надзираемом источнике 88
  `/etc/agentcall-secretary/identities.json` → `matrix:@bezrabotnyi:chat.bezrabotnyi.com` → `5e438326…`
  (плюс phone/telegram как были); временных QA-привязок нет; голосовой хост читает именно этот файл
  (`EXMANAGER_VOICE_IDENTITIES_FILE=/etc/agentcall-secretary/identities.json`).
- **Здоровье:** backend `running`, рестартов 0, образ = пин `8a6d62d`; voice 88 `healthy`, ревизия
  `ed6c4c7`, рестартов 0; `userio-secretary-sdk-tunnel` и `agentcall-tts-local` — active.
- **Cleanup:** стаб-последовательность остановлена, RTC-membership обеих фикстур сняты (2), карта личностей
  возвращена к постоянной (3 привязки), QA-калле убран из allowlist, конфигурация backend восстановлена
  из baseline-снимка (24 mount'а, UID, network, entrypoint, лимиты — как до установки) + новый образ + inbound ENV.

### 21.3 Deferred_by_owner_priority

- Ремонт режима `incidents` на постоянном handler (fast-agent home/tool/MCP-зависимости голосового хоста,
  `PYTHONPATH` MCP-серверов, обход barge-in) **остановлен по решению владельца**: ближайшая цель — чтобы
  владелец позвонил и получил ответ. Изменения сохранены коммитами (`a9c358f`, `c37ce50`, `b380091`,
  `a9c358f`… — точнее: `dad230f`, `e64a547`, `b380091`, `e5ab570`, `ed6c4c7`, `3d3c3fe`, `07d4bcf`), образ
  `07d4bcf` собран, но **не установлен**; установленная рабочая Secretary-ветвь — `ed6c4c7`.
- Не выполнено по этому приоритету: `secretary → incidents → secretary` одним звонком на постоянном handler.

### 21.4 Компактная запись переносимого опыта (self-improve)

- **Приёмка доказывается квитанцией, а не «звонком запустился»:** связка
  receipt(`status/answered/state/result.callId`) → ledger-ход(`state=completed`,`reply`) → оффлайн-ASR аудио,
  полученного A. Каждый шаг сверяется по идентификатору (`result.callId`), а не по времени.
- **Дефект чтетеля событий стоило искать по форме данных, а не по логам:** m.call-membership — это
  state-событие, публикуемое ДО ring; читатель искал его в timeline той же дельты и молча ронял звонок,
  уводя курсор `/sync` вперёд. Точный путь — `m.relates_to.event_id` RING'а плюс персистентный room-state.
- **Импорт в рантайме надо проверять по месту запуска, а не по факту импорта в соседнем модуле:** мост на 88
  стартует как скрипт ⇒ относительный импорт каленой ноги не разрешается ⇒ action отвечал `ImportError`.
  Мост публикует себя под плоским именем — этим и пользуется сосед.
- **Среда исполнителя внутри контейнера проверяется ДО генерации:** worker, его интерпретатор, его
  MCP-серверы, их env и cwd. Существование каталога недостаточно: MCP-клиент передаёт ребёнку только
  `server.env` ⇒ `PYTHONPATH` обязан быть в окружении именно зависимого сервера.
- **Правило окружения владельца приоритетнее разумной полноты:** когда владелец сужает цель, ремонт
  второстепенной ветки останавливается, WIP сохраняется коммитами и помечается `deferred_by_owner_priority`;
  закрытым остаётся ближайший измеримый результат (квитанция приёма + readback прод-конфигурации).

## 22. 28.10 (reopened): владелец нажал вызов на @aiadmin — «никто не берёт трубку» ⇒ ПРИЧИНА НАЙДЕНА И УСТРАНЕНА

### 22.1 Проверенные данные owner-call (read-only, без выгрузки текста переписки)

- **Время:** 28.10 00:58:42 MSK (`ts=1793138322433`); клиент очистил своё membership через 91.4 с
  (01:00:13 MSK) ⇒ звонок истёк без ответа.
- **room_id:** `!qOMevyFhRPiIwUIVDS:chat.bezrabotnyi.com` (личная комната владельца с @aiadmin).
- **Фактические типы событий:** `org.matrix.msc3401.call.member` от `@bezrabotnyi`
  (`state_key=_@bezrabotnyi:chat.bezrabotnyi.com_9KrATrt4uw_m.call`, `application=m.call`, `call_id=''`,
  `device_id=9KrATrt4uw`, `membershipID` ЕСТЬ, `expires=14400000`,
  `foci_preferred=[livekit → https://rtc.chat.bezrabotnyi.com]`, `focus_active`, `m.call.intent`, `scope`)
  и его очистка (`content={}`). **`m.call.invite` ОТСУТСТВУЕТ. `org.matrix.msc4075.rtc.notification`
  (RING) ОТСУТСТВУЕТ.**
- **joined:** ровно 2 (`@aiadmin`, `@bezrabotnyi`) ⇒ гейт 1:1 проходит. **encryption:** `m.room.encryption`
  `m.megolm.v1.aes-sha2` (call-события читаются в открытом виде).
- **Первый пропуск:** poller жив, allowlist=`@aiadmin`, но `MatrixCallReader.poll` узнавал звонок
  **исключительно по RING с m.mentions** ⇒ сигнала от owner-вызова не было ⇒ receipt не создан,
  dispatch не выполнялся ⇒ тишина. (Старые bot-receipts `65affd09`/`39688964` — это fixture-разговоры
  botA↔botB, они owner-call не доказывают.)

### 22.2 Исправление (минимальное, подтверждённое)

- **Интенция:** `org.matrix.msc3401.call.member` отправителя с livekit-фокусом — самостоятельный сигнал
  вызова; путь RING сохранён. Дедуп внутри опроса — по идентичности вызова (session `membershipID`),
  **не по state_key**: state_key постоянен между вызовами одного device, поэтому новый вызов с новым
  `membershipID` распознаётся, а повтор того же member — нет. Durable-квитанция по `membershipID`
  не даёт второго ответа.
- Код: `universal-userio` `e059ac6` (intake) + `ff03c2b` (фикстура может воспроизводить форму реального
  клиента) ⇒ main запушен; backend-образ `sha256:b7cb3d81…` (пин `ff03c2b`) установлен; установленный
  `matrix_inbound_calls.py` = `3c19c4fd0ab6f3bd` = пиннированный исходник.

### 22.3 Проверка изолированно, в форме реального клиента (botA → botB, через постоянный handler)

- Один разговор **только с membership, без RING**, нейтральный вопрос, без команд режимов.
- **Принято:** intake распозвал вызов ⇒ claim-receipt `fccd9626-4c09-5801-8e7a-1b7b241b27c3`; dispatch на 88
  состоялся; **88: session call=78cc251e-375b-55a2-a108-0065abb4be2c, `playing greeting bytes=169200`,
  `heard: Представься и скажи, чем ты можешь мне помочь как голосовой секретарь?`, `voice_mode turn=1
  mode=secretary`, `secretary_sdk turn=1 … elapsed_ms=89356`, `voice_turn turn=1 mode=secretary`** ⇒
  секретарский ход состоялся через постоянный handler.
- **Услышано A** (запись `recordings/ebcab4da-a9ec-4556-8764-2ea55fd82b11`, `trackDirections
  {caller: received, assistant: sent}`): 0.06–2.86 с «Здравствуйте, я голосовой секретарь. Слушаю вас.»;
  10.97–14.70 с «Сейчас нужно немного времени. Открываю контекст вашего разговора.» ⇒ **трубку взяли**.
- Честная граница: первый **полезный** ответ секретаря в этом прогоне не попал в дорожку A — ход SDK
  завершился на 89-й секунде (thinking маршрута), а запись A закрылась на 77-й ⇒ приветствие и progress
  услышаны, содержательный ответ — нет. Предыдущие прогоны показывают полезный ответ в среднем на
  21–45 с; 89 с — outlier маршрута, не логики приёма.

### 22.4 Финальная конфигурация и cleanup

- **Allowlist:** только `@aiadmin:chat.bezrabotnyi.com` (в живом core-процессе backend и в
  `/etc/userio-containers/processes.json`); QA-калле убран.
- **Identity 88:** постоянная карта (matrix owner + phone + telegram), временная QA-привязка удалена.
- **RTC-membership фикстур:** сняты (2). Стаб и harness-процессы: не запущены.
- Backend `running`, рестартов 0, образ = пин `ff03c2b`; voice 88 `healthy` (`ed6c4c7`).
- Старые bot-receipts сохранены историей и owner-call не доказывают.

### 22.7 Checkpoint задержки первого звука в живом звонке (28.10, read-only трассировка завершённого call 5a833c9a)

Звонок **не повторяется**; ниже — точные timestamps уже завершённого вызова (нога B
`5a833c9a-562c-5c8f-87d2-aae52f6fe3ce`, turn 1, `created 2026-10-27T23:07:23.425461Z`):

| момент (UTC) | событие | источник |
| --- | --- | --- |
| 23:07:23.383 | B распознал вопрос: `heard: Здравствуй. Как тебя зовут?` | лог 88 voice |
| 23:07:23.444 | сервер принял ход: `voice_mode turn=1 mode=secretary` (61 мс после вопроса) | лог 88 voice |
| 23:07:23.559 | OmniRoute получил `POST /v1/chat/completions \| exmanager-codex-gpt-6-luna \| 7 msgs \| 10 tools \| effort=low` (115 мс) | app.log OmniRoute |
| 23:07:23.592–.594 | маршрут разрешён: `minimax/MiniMax-M3.1-Flash`, провайдер `minimax`, connection `a507dbb3` («MiniMax M3 token») | app.log OmniRoute |
| 23:08:57.910 | upstream-запрос завершён: `succeeded (94320ms, 0 fallbacks)` | app.log OmniRoute |
| 23:08:57.926 | **строка роутера: `duration=94327`, `ttft_ms=94295`, `added_wait_ms=null`, `tokens_in=3562`, `tokens_out=97`, `tokens_reasoning=0`, `status=200`** | storage.sqlite `call_logs` |
| 23:08:57.980 | первый текстовый дельта дошёл до голоса: `first_text_ms=94536` | лог 88 voice |
| 23:08:58.185 | синтез первой фразы: `first_text_to_tts_ms=4.5`, `first_tts_to_pcm_ms=224.7` | лог 88 voice |
| 23:08:58.0 | ход завершён: `voice_turn turn=1 mode=secretary elapsed_ms=94614` | лог 88 voice |
| +101.98–116.46 с | A услышала полезный ответ (в шкале A) | `recordings/a21e52ac…/timeline.md` |

- **Участок:** 94.295 с TTFT из 94.327 с суммарных — целиком upstream/adapter (официальный API MiniMax,
  connection «MiniMax M3 token»). Остальные участки: принятие хода 61 мс, ход до роутера 115 мс,
  дельта→PCM 229 мс, доставка в сеть ~3.2 с. Скрытого thinking в отчётности нет (`tokens_reasoning=0`),
  но это не доказывает его отсутствие.
- **Короткий source-check (без генерации):** клиент шлёт `stream: true` (goai/openai chat); тело запроса
  в OmniRoute уходит к провайдеру без изменений (решение по `stream` — по телу клиента,
  `api/v1/chat/completions/route.ts`); relay пересылает каждый апстрим-чанк немедленно
  (`relay/chat/completions/streamFinalizer.ts`: `pull → reader.read() → controller.enqueue`) ⇒
  **внутренней буферизации в voice/adapter-контуре нет**.
- **Вывод:** задержка внешняя (upstream first-token). Косметический фикс, сборка и смена модели/провайдера/
  комбо/квот НЕ выполняются (рабочий маршрут и owner-avoid не трогаются). Следующий шаг — один
  изолированный A→B замер на нынешнем установленном production-маршруте с честными числами.

### 22.8 Замер на текущем production-маршруте (28.10): один новый A→B, одинаковые метрики, внешний блокер

- **Изоляция:** комната `!vmxuOOunnmTUeuEdvi:chat.bezrabotnyi.com`, ровно два фикстурных бота; A
  `account_ref matrix:ab023a689e926bf99d924e933ebd` (target B `@lhc_voice_tester_b_20261021`), B
  `account_ref matrix:37698708b623db45e1bfe61f4d01`; личные Matrix/PSTN/SIM/GSM/SIP/fallback = 0; owner и
  `@aiadmin` как QA-адресат не использовались; мок 18199 (`PHRASES=["Хорошо."]`) жил до терминала B.
- **Вопрос (новый, нейтральный):** «Добрый день! Как тебя зовут?» — WAV 24000 Гц 2.05 с (native
  `pcm.sample_rate`), оффлайн-ASR «Добрый день. Как тебя зовут?». Фикстура `fc61412` без изменений
  (`attention_pcm=b''`, гейт микрофона, `speech_speed=1.0`), `CALLER_SCRIPT_WAVS` пусто, `CALLER_CLIENT_FORM=1`,
  терпение 150 с.
- **Квитанции:** intake `9b268023-a510-55b0-b35d-66d5ef03e804` (`sent`/`answered`); нога B
  `00f7619e-d512-5dc9-a95d-f1ffa0219f56` — ledger `exmanager_voice_turns` turn 1 = **completed**, input
  «Добрый день. Как тебя зовут?», reply «Добрый день! Меня зовут eXmanager, я ваш голосовой секретарь —
  помогаю с делами, задачами и напоминаниями. Чем могу быть полезен?»; нога A
  `23407810-9576-4964-90b0-fb807124f6f7` — `session ended`, `audio_socket_closed` (07:14:58.21Z); B —
  `session ended` 07:12:47.206Z.
- **Замер (тот же маршрут `exmanager-codex-gpt-6-luna` → `minimax/MiniMax-M3.1-Flash`, connection
  `a507dbb3`, промпт 3562 токена, `added_wait_ms=null`):** B услышал вопрос 07:11:02.044Z; первый текст SDK
  07:11:52.402Z (`first_text_ms=50289`); строка роутера: `duration=50085`, **`ttft_ms=49269`**,
  `tokens_out=51`, `tokens_reasoning=0`, `status=200`; `utterance_end_to_pcm_ms=52611.1`,
  `stt_ms=1378.3`, текст→PCM 210.7 мс; **A услышала полезный ответ на 57.37–64.39 с** своей шкалы
  («Добрый день. Меня зовут Экс-менеджер. Я ваш голосовой секретарь. Помогаю с делами, задачами и
  напоминаниями. Чем могу быть полезен?»), закрытие звонка B — 107.35–112.20 с.
- **Сравнение одинаковых метрик с предыдущим вызовом (5a833c9a):** upstream TTFT 94 295 → **49 269 мс**;
  суммарно upstream 94 327 → 50 085 мс; первый текст SDK 94 536 → 50 289 мс; до PCM 96 570,7 → 52 611,1 мс;
  первый полезный звук у A 101,98 → **57,37 с**; конец ответа 116,46 → 64,39 с.
- **Честная граница:** ориентир < 30 с **не достигнут** — 49–95 с лежат во внешней задержке
  upstream first-token (официальный API MiniMax, та же connection, тот же промпт, без queue-wait); разброс
  95 с → 50 с — вариативность провайдера, а не исправление в коде. Внутренних буферизаций не найдено
  (§22.7), поэтому косметический фикс, сборка и смена модели/провайдера/комбо/квот НЕ выполнялись;
  решение о маршруте остаётся за владельцем.
- **Cleanup после обоих терминалов:** allowlist = только `@aiadmin:chat.bezrabotnyi.com` (контейнер
  `9a133b4e0033 → 377c3334a2c9`, ревизия `ff03c2b`, образ `sha256:b7cb3d81…`, health OK); постоянная
  identity-карта 88 (3 привязки), временная QA-привязка снята, QA `model_grants` = 0 (owner = 14); мок 18199
  остановлен, harness-процессов нет, живых RTC-membership нет (2 истёкших события).

### 22.9 Direct-GLM для голосового Secretary: задержка первого звука 8.5 с (было 54.1 с)

**Решение владельца (2026-10-28):** «использовать либо MiniMax M3.1 Flash напрямую, либо GLM 5.3 Flash БЕЗ
OmniRoute, чтобы быстрее» — только для голосового Secretary.

- **Диагноз (§22.7/§22.8):** 94.295 с TTFT из 94.327 с — внешняя задержка upstream first-token официального
  API MiniMax; внутренней буферизации нет ⇒ косметический фикс в голосовом контуре был бессмысленен.
- **Источник и сборка (штатный `go tool air deploy`, изолированный кандидат):** база = живой native-source
  (`air clone` приложения `c8b0c2c6-dc99-463f-baed-ff049b455be8`, build `b990160e`), кандидат = база + **ровно
  2 моих пути** (`airlock-agent/voice_chat.go`, `airlock-agent/voice_model_slot_test.go`), чужой WIP ноль.
  Правка: голосовой ход спрашивает собственный слот моделей `voice` (`agentsdk.ModelSlot` + `a.LLM(ctx,
  "voice")`), а не дефолтный текстовый маршрут агента; SDK, инструменты, история, ledger и auth не тронуты.
  RED (`undefined: voiceModelSlot`) → fix → GREEN (`go test -run TestVoiceTurnAsksTheVoiceModelSlot` ok),
  `go build ./...` ok. Публикация: build `5c696be9-1c09-44d5-abe0-892dbf3a64e5`, candidate
  `48d689ff-449a-4a7e-8f5e-dc3045757d04`, task `f7b405ba-044a-47e8-8d92-9951ca4ba895`, source ref
  `4a8ca37d223e2064e427980820ba15af30fa0fa1`, образ `c8b0c2c6-…:4a8ca37d223e`, статус complete / deployment
  COMPLETE (10:41:02→10:42:24Z), приложение Up.
- **Config (штатный control plane, без новых прав):** слот `voice` зарегистрирован развёрнутым исходником и
  связан через `PUT /api/v1/agents/{id}/models` на провайдера `zai-coding-direct`
  (`0e7f69eb-b78f-4981-b0d4-9cccc2a14a38`, `https://api.z.ai/api/coding/paas/v4`) и модель `glm-5.3-flash`
  (каталог: tools=true, reasoning=false, ctx=160000, out=32000; гранты владельца есть). Слоты
  `deep_task`/`team_manager`/`telegram_vision` и переопределения exec/vision — **без изменений** ⇒ правка
  только голосовая. OmniRoute в этом маршруте не участвует.
- **Обновление существующей service-credential (регрессия 401 после публикации):** деплой перевыпустил
  сервисный токен приложения (в живом приложении `token_version=184`, копия на 88 была `183`) ⇒ голосовой
  клиент получил `HTTP 401`. Исправлено штатным обновлением существующей копии (`/etc/agentcall-secretary/
  voice.token` → 184, `upstream.json.tokenVersion=184`) из действующей credential платформы; клиент
  перечитывает её на каждый запрос (native rotation). Signing key и owner-привязки не тронуты; новых грантов
  нет. Провал прогонов `ecc5ee8b`/`26e29569` (401, 0 строк SDK) НЕ повторяется, UNKNOWN сохранён.
- **Изолированный канари (один, новые ID):** intake `707bb3ed-70cf-5303-b926-6bc9646426b9`; нога B
  `671d59db-e971-5aee-98c1-6a00d6df1925` — ledger `exmanager_voice_turns`: turn 1 **completed** («Здравствуйте.
  Что вы умеете?» → «Здравствуйте! Я ваш голосовой секретарь: могу вести задачи и напоминания, искать по
  истории и памяти, проверять сервисы через штатные инструменты, а по инцидентам переключить на
  расследование. Чем помочь?»), turn 2 **completed** (тот же вопрос повторно распознан). Фактические строки
  платформы: `llm_usage` `387f929c` и `99a0b8ba` — **оба `zai-coding-direct` / `glm-5.3-flash`, слот
  `voice`**, 6.328 с и 5.141 с ⇒ **OmniRoute в этом прогоне не участвует**, код 500 не было, режим остался
  `secretary`.
- **Замер (A received):** нога A дала **два engine ID** в одном звонке — `00b5b0c8-1d45-436f-8f94-
  53a0a5deec2e` (старт 10:54:31.55Z) и `9068dd1e-14fe-4ad1-a5bb-db837b258500` (старт 10:54:47.88Z): это
  повторный старт/переподключение движка абонента внутри того же звонка, обе записи завершены полностью —
  это ДВА разных аудио-сеанса, а не один. Конец вопроса — 3.43 с; **первый полезный звук: 11.90 − 3.43 =
  8.47 с** (второй движок: 11.00 − 3.43 = 7.57 с), **полный полезный ответ: 22.86 − 3.43 = 19.43 с** ⇒
  против baseline 54.07 с / 61.09 с ⇒ **ориентир < 30 с достигнут**. Терминалы: B — `session ended`
  10:55:58Z; обе сессии A — `ended` (audio_socket_closed) с завершёнными записями.
- **Cleanup (owner-only):** allowlist = только `@aiadmin:chat.bezrabotnyi.com` (контейнер
  `be23f27941f9 → 84a195b3d746`, ревизия `ff03c2b`, образ `sha256:b7cb3d81…`); постоянная identity-карта 88
  (3 привязки), временная QA-привязка снята, QA `model_grants` = 0 (owner = 14); мок 18199 остановлен, своих
  caller-процессов 0, живых RTC-membership нет; credential приложения актуальна (184), signing key и
  owner-привязки не тронуты.
- **Scoped source publish:** `megamen32/exmanager`, ветка `codex/voice-direct-model-slot-20261028`, коммит
  `30d46c9` (ровно 2 пути, +74/−12) ⇒ diff = только мой голосовой шов, чужой WIP ноль. Слияние в `main` —
  решение Lead (`main` занят worktree'ом другой сессии, `origin/main` ушёл вперёд).

### 22.6 ПРИЁМКА ДОСТИГНУТА (28.10, изолированный client-form A→B): один вопрос → один полезный completed reply → услышан A

- **Причина потери вопроса (установлена по коду, не по гипотезе):** A импортирует СТАРЫЙ host-engine
  `/opt/noticeplace-voice/corrections/4ec71d7e…/agentcall/agentcall_realtime_bridge.py`; в нём (строки
  3938–3949) ДО `greeting` отправляется `send_pcm_stream(attention_pcm)`. Гейт микрофона A (первый WAV)
  расходовался на attention ⇒ **вопрос резался**. Production engine/host не трогались.
- **Минимальный фикс (только фикстура, `universal-userio` main):** `fc61412` — у prepared-звонка A
  `attention_pcm=b''`, `attention_text=''` (attention не синтезируется) ⇒ первым PCM в комнату уходит
  вопрос. Сохранены: гейт микрофона `5ae9756`, `speech_speed=1.0` (`583b162`), живой локальный стаб
  `PHRASES=["Хорошо."]`, `CALLER_SCRIPT_WAVS` пусто, `CALLER_CLIENT_FORM=1`, терпение 150 с.
- **Прогон (один, client-form, только membership, без RING; комната двух фикстурных ботов
  `!vmxuOOunnmTUeuEdvi`; новый нейтральный вопрос «Здравствуй! Как тебя зовут?», WAV 24000 Гц 2.06 с,
  оффлайн-ASR «Здравствуй. Как тебя зовут?»; стаб 18199 PID 798249 жив весь разговор):**
  - **intake:** `a9b80ec3-c67b-5608-8b76-9cdbdcaf8d00` (`status=sent`, `answered=true`, `state=answered`);
  - **нога B:** `5a833c9a-562c-5c8f-87d2-aae52f6fe3ce` — `heard: Здравствуй. Как тебя зовут?` (23:07:23Z),
    `voice_mode turn=1 mode=secretary`, `secretary_sdk turn=1 first_text_ms=94536`, `voice_turn turn=1
    mode=secretary elapsed_ms=94614`; ledger `exmanager_voice_turns`: **turn 1 = completed**, input
    «Здравствуй. Как тебя зовут?», reply «Здравствуйте! Меня зовут eXmanager — я ваш голосовой секретарь.
    Помогаю с задачами, делами и напоминаниями: могу создать задачу с дедлайном, показать список ваших
    дел и напоминаний. По техническим сбоям и состоянию сервисов переключусь в режим инцидентов. Что нужно
    сделать?» — **один вопрос, один полезный completed reply**; кода 500 нет; режим остался `secretary`;
  - **нога A:** `a21e52ac-d436-4a1c-802d-8aef96220ccd` — `session ended`, `audio_socket_closed` (23:11:19Z);
  - **услышано A** (`recordings/a21e52ac…/timeline.md`, колонка «Собеседник», сегмент **101.98–116.46 с**):
    «Здравствуйте. Меня зовут Экс-менеджер, я ваш голосовой секретарь. Помогаю с задачами, делами и
    напоминаниями. Могу создать задачу с дедлайном, показать список ваших дел и напоминаний. По техническим
    сбоям и состоянию сервисов переключусь в режим инцидентов. Что нужно сделать?» ⇒ **полезный ответ SDK
    услышан A**;
  - **гейт A сработал:** после вопроса B не распознал больше ни одной реплики A (собственные фразы A со
    стаба в комнату не ушли) ⇒ ответ B не перебивался.
- **Честная граница:** приёмка — в изолированной QA-комнате (без шифрования) между двумя фикстурами;
  личный зашифрованный звонок владельца этим прогоном не доказан (сигнал intake на membership доказан
  §22.1–22.2, ответ секретаря — здесь).
- **Cleanup после терминала:** allowlist = только `@aiadmin:chat.bezrabotnyi.com` (`/etc/userio-containers/
  processes.json` и живой контейнер; пересоздан из baseline-снимка: `9900b2f87b12 → 789703703ab6`, образ
  `sha256:b7cb3d81…`, ревизия `ff03c2b`, health OK); постоянная identity-карта 88 (matrix owner + phone +
  telegram, 3 привязки), временная QA-привязка удалена; стаб 18199 остановлен, harness-процессов нет;
  живых RTC-membership нет (2 истёкших события). Временные QA-артефакты прогона сохранены в
  `/var/lib/agentcall/l01-canary/`.

### 22.5 Клиентская приёмка 28.10 (одна попытка после правки фикстуры): звонок состоялся, полезного ответа НЕТ

- **Что менялось в фикстуре (только `universal-userio` `tests/manual/matrix_rtc_ai_caller.py`, в main):**
  `5ae9756` — микрофон A пропускает в комнату только PCM первого вопроса (дальше микрофон нем), чтобы
  собственные реплики A не перебивали ответ B; `583b162` — `speech_speed=1.0`, чтобы вопрос доходил
  своей длительностью и гейт A удерживал ровно вопрос. Production voice/маршрут/модель не менялись.
- **Одна попытка (client-form, только membership, без RING, комната двух фикстурных ботов
  `!vmxuOOunnmTUeuEdvi`, вопрос «Привет! Что ты умеешь?», WAV 24000 Гц 1.81 с, оффлайн-ASR:
  «Привет. Что ты умеешь?», `CALLER_SCRIPT_WAVS` пусто, терпение 150 с, временный локальный стаб
  127.0.0.1:18199 `PHRASES=["Хорошо."]` (PID 697192) жив весь разговор):**
  - **intake:** `afc686dc-294b-56c1-ad60-27842c8df82e` (`status=sent`, `answered=true`);
  - **нога A:** `23ba9665-1250-4636-af16-01465d5afa18` (session ended, `audio_socket_closed`);
  - **нога B:** `0a5cdd80-a562-5505-a1e8-c303fb538224` (session ended);
  - **SDK B:** `voice_mode`/`secretary_sdk`/`voice_turn` — НЕТ ни одной строки; ledger
    `exmanager_voice_turns` по `0a5cdd80…` — **0 строк**; 88-лог: `no recognized speech` (reprompt=True),
    затем `reprompt=False`, `session ended`;
  - **дорожка A (`recordings/23ba9665…/timeline.md`, колонка «Собеседник»):** 0.54–3.66 с «Я голосовой
    секретарь. Слушаю вас.»; 23.42–26.79 с «Я пока не могу разобрать вашу речь. Повторите, пожалуйста.»;
    46.58–51.40 с «Я всё ещё не могу разобрать вашу речь. Завершаю звонок…» ⇒ **полезного SDK-ответа A
    не услышала; трубку взяли, но содержательного ответа не было.**
- **Факт о потере звука (без гипотез):** опубликованная A дорожка (`recordings/23ba9665…/assistant.wav`,
  фрагмент 5–10 с, оффлайн-ASR) = «Привет. Что ты умеешь?» ⇒ вопрос у A синтезирован и отправлен в
  комнату; дорожка B (`recordings/0a5cdd80…/caller.wav`, 53 с, оффлайн-ASR) = пусто ⇒ B не получил
  распознаваемой речи. Куда именно теряется сигнал (публикация A → SFU → подписка B → capture B) —
  не локализовано.
- **Итог приёмки:** НЕ достигнута. Требование «B SDK: один вопрос/полезный completed reply, A received
  слышит его» не выполнено; owner-call по-прежнему не доказан. Новых звонков не делалось, старые
  принятые не переигрывались.
- **Cleanup после терминала звонка:** allowlist = только `@aiadmin:chat.bezrabotnyi.com` (в
  `/etc/userio-containers/processes.json` и в живом контейнере; контейнер пересоздан из baseline-снимка
  тем же образом `sha256:b7cb3d81…` и ревизией `ff03c2b`, `94bea856f624 → 163f1dba65d0`, health OK);
  постоянная identity-карта 88 (matrix owner + phone + telegram), временная QA-привязка удалена;
  стаб 18199 остановлен (порт не слушается), harness-процессов нет; RTC-membership фикстур истёк
  (live-вызовов нет). Временные QA-артефакты прогона сохранены в `/var/lib/agentcall/l01-canary/`.
