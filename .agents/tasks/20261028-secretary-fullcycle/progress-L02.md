# L02 — сверка живого пути, доступа и безопасного тестового контракта

Read-only ход (8–12 мин), без новых звонков, без сборок, без смены прав/грантов. Ниже: факты **source**,
**runtime** и **consumer** раздельно; точный RED (или честный UNKNOWN) и минимальные owned paths следующего
ремонта. Исторические квитанции сохранены и не перепроверялись.

## 1. Факты — source (код)

### 1.1 Голосовой слот моделей (уже опубликован и установлен)
- Репозиторий `megamen32/exmanager`, `main` = `20fc2ad` («Secretary voice: spoken turns use their own model
  slot»), опубликованный native-source = `4a8ca37d223e2064e427980820ba15af30fa0fa1`.
- Шов (ровно 2 пути): `airlock-agent/voice_chat.go` — `const voiceModelSlot = "voice"`, поле
  `llm func(context.Context, string) stream.Model`, помощник `turnInput(...)` с `Model: v.llm(ctx,
  voiceModelSlot)`, `a.RegisterModel(&agentsdk.ModelSlot{Slug: voiceModelSlot, Capability: agentsdk.CapText})`,
  оба вызова `v.stream(...)` переведены на `turnInput`; `airlock-agent/voice_model_slot_test.go` — узкий тест
  `TestVoiceTurnAsksTheVoiceModelSlot`.

### 1.2 Путь служебной credential (88)
- Updater: `universal-userio/scripts/secretary_sdk_tunnel.py` — `STATE = Path('/etc/agentcall-secretary')`
  (строка 25); `atomic_private(STATE / 'voice.token', ...)` (строка 86, атомарно: `mkstemp` + `fsync` +
  `os.replace`); `atomic_private(STATE / 'upstream.json', ...)` (строка 91). Разрешение приложения — SSH
  `roomhacker@192.168.2.100` + `docker inspect` (таймаут 20 с), публикация выполняется в цикле, когда
  снимок изменился.
- Bridge/клиент: `/etc/agentcall-secretary-voice.json` на 88 ⇒ `EXMANAGER_VOICE_TOKEN_FILE =
  /etc/agentcall-secretary/voice.token`, `EXMANAGER_VOICE_SIGNING_KEY_FILE = /etc/agentcall-secretary/
  signing.key`, `EXMANAGER_VOICE_URL = http://127.0.0.1:18086`; `agentcall_secretary_voice.py:_request()`
  перечитывает credential **на каждый запрос** (комментарий в коде: «credentials are reread for native
  rotation»).

### 1.3 Контракт режимов secretary/incidents
- Server (`airlock-agent/voice_chat.go`): маршрут `voiceModePath = "/internal/voice/mode"` (27) и его
  регистрация (76); `handleMode` (142) принимает только `secretary`/`incidents` (154) и требует
  `ownedCall` + `ActiveTurn == 0`; режим по умолчанию — `secretary` (208), событие `accepted` (275), ветка
  `call.Mode == "incidents"` → событие `handoff` (285); переключение внутри хода → `handoff` (352).
- Переключение изнутри ответа: инструмент `switch_voice_mode` (449–470) — `SetActiveVoiceMode` +
  `onSwitch(...)`; описание прямо фиксирует: «Switch this SAME live call … this does not acknowledge,
  resolve or repair any incident».
- Client: `SecretaryBrain.reply_stream` (в контейнере 88) обрабатывает `handoff` и передаёт вопрос в
  incidents-мозг (`_incident_reply` / `FastAgentIncidentBrain` в
  `universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py`).

### 1.4 Безопасный профиль инструментов для app-member QA
- `airlock-agent/voice_chat.go:403` — быстрый профиль вызывающего: `read_memory_context`, `read_past`,
  `search_memory`, `remember`, `list_tasks`, `create_task`, `list_reminders`; плюс `secretary_capability`
  (408, постраничный каталог) и `switch_voice_mode`.
- Область человека: `airlock-agent/person_identity.go:277` `personProductTools` — исключены `telegram_*`/`tg_*`.

### 1.5 Приём входящего Matrix-звонка (вопрос шифрования)
- `universal-userio/src/universal_userio/matrix_inbound_calls.py:287 admit_call(...)`: отказ при
  `own_user_id not in allowed_accounts` (`account_not_allowed`), при недоверенном отправителе
  (`caller_not_trusted`), при комнате не 1:1 (`room_not_one_to_one`, сравнение множества
  `call.joined` с `{own_user_id, call.caller}`) и по истечении (`call_expired`).
- **Шифрование комнаты в условия допуска не входит**: сигналы звонка читаются из state комнаты, а
  `m.room.encryption` не проверяется.

## 2. Факты — runtime (живое состояние, read-only)

### 2.1 Прямой GLM-слот и установленная ревизия
- Привязка слота `voice` (штатный control plane `PUT /api/v1/agents/{id}/models`): `assigned_model =
  glm-5.3-flash`, `assigned_provider_id = 0e7f69eb-b78f-4981-b0d4-9cccc2a14a38` (`zai-coding-direct`,
  `https://api.z.ai/api/coding/paas/v4`, каталог: tools=true, reasoning=false, ctx=160000, out=32000).
  Слоты `deep_task`/`team_manager`/`telegram_vision` и переопределения exec/vision — без изменений ⇒ рабочий
  OmniRoute других задач не затронут.
- Установленная ревизия: контейнер `airlock-dev-agent-c8b0c2c6-dc99-463f-baed-ff049b455be8`, образ
  `c8b0c2c6-…:4a8ca37d223e`; публикация build `5c696be9-1c09-44d5-abe0-892dbf3a64e5` (candidate
  `48d689ff-449a-4a7e-8f5e-dc3045757d04`, task `f7b405ba-044a-47e8-8d92-9951ca4ba895`, статус complete,
  deployment COMPLETE, 10:41:02→10:42:24Z).

### 2.2 Credential: один замер по всем namespace (только `token_version`, без секретов)
| namespace | token_version | agent_id |
| --- | --- | --- |
| 88, host-копия `/etc/agentcall-secretary/voice.token` | **184** | c8b0c2c6-dc99-463f-baed-ff049b455be8 |
| 88, каталог контейнера `userio-voice` (тот же файл) | **184** | c8b0c2c6-dc99-463f-baed-ff049b455be8 |
| выданная приложением credential (контейнер приложения) | **184** | c8b0c2c6-dc99-463f-baed-ff049b455be8 |

- Пути: updater пишет `STATE=/etc/agentcall-secretary` (владелец `roomhacker:roomhacker`, режим 700;
  `voice.token`/`upstream.json` — 600); bridge читает `EXMANAGER_VOICE_TOKEN_FILE=/etc/agentcall-secretary/
  voice.token`; mount контейнера: `bind /etc/agentcall-secretary -> /etc/agentcall-secretary rw=false` ⇒
  **запись и чтение — один и тот же файл, разрыва в путях нет**.
- Живой замер разрешения по рабочему пути (та же команда, что у updater: SSH 88→100 + `docker inspect`):
  `rc=0`, **479 мс** при таймауте 20 с; разрешено `token_version=184`, адрес `192.168.128.7`, `containerId
  72d136240ac5…`, `imageId sha256:c279f3…` (текущий образ сборки).
- SSH-форвард в приложение жив: `127.0.0.1:18086` LISTEN (pid ssh), TCP-connect — 4 мс; это и есть
  `EXMANAGER_VOICE_URL` клиента.

### 2.3 Consumer (исторические квитанции, не перепроверялись)
- `intake 707bb3ed-70cf-5303-b926-6bc9646426b9`; B `671d59db-e971-5aee-98c1-6a00d6df1925` — ledger
  turn 1/2 = completed; платформенные строки `llm_usage` `387f929c` и `99a0b8ba` = `zai-coding-direct` /
  `glm-5.3-flash`, слот `voice` (6.328/5.141 с) ⇒ OmniRoute в этом прогоне не участвовал.
- Замер: первый полезный звук **8.47 с** после конца вопроса, полный ответ **19.43 с** (было 54.07/61.09 с).
- Изоляция QA: комната `!vmxuOOunnmTUeuEdvi:chat.bezrabotnyi.com`, ровно 2 бота; A `account_ref
  matrix:ab023a689e926bf99d924e933ebd`, B `account_ref matrix:37698708b623db45e1bfe61f4d01` /
  `@lhc_voice_tester_b_20261021`; личные Matrix/PSTN/SIM/GSM/external SIP/fallback = 0; owner/@aiadmin как
  QA-адресат не использовались; текущий allowlist = только `@aiadmin:chat.bezrabotnyi.com`.

## 3. Точный RED и честные UNKNOWN

- **RED-1 (окно 401, 28.10 13:36–15:06 MSK) — НЕ объявляется.** Замер выше показывает зелёный путь
  сейчас: одна и та же запись/чтение, версия 184 во всех трёх namespace, разрешение 479 мс при таймауте 20 с,
  форвард жив. Журнал updater'а содержит только переходные строки (`resolved: false` / `resolved: true`),
  это не полная история опросов ⇒ точная причина окна **не установлена** и не доказывается по одному
  окну; RED по догадке не объявляю. Узкая проверка устойчивости (без вызовов): периодическое чтение
  `token_version` в трёх namespace + замер времени разрешения + живость форварда; расхождение версий или
  таймаут разрешения — сигнал к ремонту.
- **UNKNOWN-1 — incidents-ремонт и ACK.** Символы контракта есть (handoff → incidents-мозг моста,
  `switch_voice_mode`), но сквозной ремонт/ACK не подтверждён (ремонт fast-agent в предыдущем цикле
  отложен). Лист L05/L06.
- **UNKNOWN-2 — зашифрованная Matrix-комната.** Допуск шифрование не проверяет (§1.5), call-события в
  зашифрованной комнате читаются (комната владельца, §22.1 канона) ⇒ поведение постоянного обработчика в
  зашифрованной тестовой комнате **не доказано**; нужен отдельный QA-канари (лист L07), правки intake не
  требуются.
- **UNKNOWN-3 — app-member QA без новых прав.** Для голосового слота новые гранты не нужны: привязка слота
  принадлежит агенту, а entitlement проверяется на пути персональных предпочтений, не на привязанном
  слоте (доказанный прогон шёл с QA-персоной). Профиль инструментов — безопасный fastTools (§1.4).
  Непроверено: полный набор персональных инструментов (вне fastTools) для QA-человека.
- **UNKNOWN-4 — второй A engine-сеанс в доказанном прогоне** (повторный старт/переподключение движка
  абонента). Факт зафиксирован в каноне; локализация не выполнялась (в этом листе не исследуется).

## 4. Минимальные owned paths следующего ремонта

| Что чиним | Owned path (минимальный) | Статус |
| --- | --- | --- |
| Окно credential/401 (если повторится) | `universal-userio/scripts/secretary_sdk_tunnel.py` — публиковать токен при изменении версии независимо от сравнения снимка целиком (1 условие в цикле `supervise()`) | не применять, пока причина не доказана |
| incidents → ремонт → ACK | `airlock-agent/voice_chat.go` (handoff) + `universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py` (incidents-мозг) + сторона Notice Place (ACK) | листы L05/L06 |
| Зашифрованная тестовая комната | только фикстура/комната (интейк не меняется: допуск не зависит от шифрования) | лист L07 |
| Профиль инструментов QA | правок не требуется: действующий fastTools (§1.4) | — |
| Голосовой direct-GLM | правок не требуется: слот привязан, ревизия установлена (§2.1) | — |

## 5. Что реально сделано в этом листе

Только read-only: чтение исходников (`universal-userio`, `megamen32/exmanager`), чтение живого состояния
(слот/привязка в БД, ревизия контейнера, mount-ы, версии токена, замер разрешения и форварда, чтение
журнала updater'а и journal-окна), запись этого файла. Новых звонков, генераций, сборок, изменений прав и
грантов, коммитов в чужие пути — нет. Чужой WIP (494 записи в worktree `e5fb`, чужая ветка родительского
checkout) не тронут.

## 6. Статус

L02 закрыт по фактам; неподтверждённые capabilities названы явно (UNKNOWN-1..4). Дальнейший ремонт идёт по
этому же плану `20261028-secretary-fullcycle` (листья L03+), без нового проекта, без общей консолидации
веток. Проекции плана (`plan.md`/`plan.html`/`plan.graph.json`) обновляются владельцем плана; здесь изменён
только собственный leaf-файл `leaves/L02.json` и этот отчёт.
