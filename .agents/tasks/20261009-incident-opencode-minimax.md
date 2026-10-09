# R40 — инциденты через OpenCode / MiniMax по подписке

**Статус R40: завершено.** Живой Herder сохраняет отдельный подписочный incidentExecution; один synthetic diagnosis завершён на native OpenCode/MiniMax-M3.1-Flash-Preview, dedup и отсутствие пользовательских отправок подтверждены. Прежние блокеры ниже — история; итоговое доказательство в последнем разделе.

Владелец: рабочая сессия 01a11f86-283e-7831-8ee1-d97ab3389eba на server-100.

Цель: новые diagnosis/repair используют OpenCode, minimax-coding-plan/MiniMax-M3.1-Flash-Preview; явный выбор в UI и сохранение. Без fallback, копирования ключа и повторной обработки старых инцидентов.

Проверено: Admin main синхронизирован с origin; чужой WIP сохранён, ledger прочитан и не редактируется. Noticeplace main исходно чист. Herder содержит чужой delivery/runtime WIP. API моделей подтверждает точную подписочную модель, auth type=api credential_present=true без чтения значения в вывод.

Ошибка до исправления: live launch policy и diagnosis profile выбирают Codex; планировщик содержит строку omniroute/subagent; ремонт допускает ZCode/Codex. No-LLM lifecycle — отдельный модуль и не входит в изменения.

Координация: указанному publisher доставлен один steer о границах health-remediation.ts, launch-policy-settings.tsx и малом preflight в server.ts. API показывает этот ID в AutoFind; требуется сверка настоящего Herder publisher. Build/deploy Herder не подменяется.

Бюджет: targeted Python ≤512 MiB/1 CPU/2 min; Vitest ≤1 GiB/1 worker/2 min, UI/canary ≤2 GiB/2 CPU/5 min. На 07:21 UTC available около 21 GiB, PSI full avg10 0.43, OOM не доказан; давление само по себе работу не запрещает.

Следующий шаг: красные точные регрессии, исправление helper/profile/UI, scoped publish, deployment владельцем Herder и один безопасный deduplicated synthetic incident до завершённой диагностики.

## Публикация и проверки

Herder origin/main: e81eb8e — отдельный persisted incidentExecution/UI; 34a4264 — regression unavailable native model; d80e710 — только 9 строк health preflight в server.ts. Чужие staged/unstaged пути не опубликованы. 19 targeted tests и tsc --noEmit прошли. Build/deploy передан действующему publisher 01a11b54; 01a11b43 получил UI handoff ранее, единый выпуск согласуется у publisher.

Noticeplace: 98 targeted tests прошли (health workflow/helper/GPTAdmin/R40). До исправления live policy preferredHarness=codex, protected diagnosis profile codex/gpt-5.6-sol; in-memory исходная нормализация подтверждает допустимый Codex. После изменения точный подписочный route, отсутствие скрытого fallback и сохранение global routing проверены. Evidence: .tmp/r40/*.txt, before-route.json. Исторические incidents не переобрабатывались; исходные продуктовые сессии не менялись.

Следующий шаг: scoped publish Noticeplace, штатный upgrade; дождаться выпуска Herder, сохранить incidentExecution через действующий API и проверить UI + один synthetic incident до terminal diagnosis. До этого R40 не завершён.

## Исправление push → upgrade

Первый SSH push отказал; вызов upgrade ошибочно продолжился. Предыдущий release 7a3c2c12e6c7 восстановлен. После явного дополнительного восстановления проверен живой /health: storage_ready=true, dispatcher_ready=true; HTTP 503 вызван существующей исторической очередью reconciliation_required=1452, uncertain=1409. Эти доставки не переигрывались по прямому запрету пользователя. Не выдавать этот health за зелёный.

Чужие два коммита объединены через обычный merge d9796fd, опубликованный HTTPS (SSH 443 временно отказал); reset/force/WIP скрытие не применялись. 117 integrated tests прошли. Новый дефект взят в работу: deploy/noticeplace upgrade сверяет HEAD с свежим remote main и заданным NOTICEPLACE_PUBLISHED_SHA до любых действий; отказ связи/расхождение прекращает upgrade. 3 subprocess regression red до исправления, 7 deploy checks green после.

## Текущий consumer результат

Noticeplace release: /opt/noticeplace-releases/20261009T074113Z-b5a2bf06a067-518962. Штатный upgrade перед переключением проверил свежий remote SHA b5a2bf06a067db457a095d3bbba1993a4af46379. Оба сервиса active. Защищённые allowlist profiles обновлены на opencode/minimax-coding-plan/MiniMax-M3.1-Flash-Preview; uid/gid и 0600 сохранены, auth.json не изменялся.

Herder окончательные owned bytes: fb9e78e5a170069ae92c4e921599874f5ea6376c. Свежая UI-проверка пока показывает старую панель без incidentExecution; API поле пока отсутствует. Единственный combined publisher получил SHA/пути/тесты. Следующий необходимый шаг у publisher: штатный build/deploy; после этого R40 сам сохраняет настройку и проверяет один terminal diagnosis. Задача не закрыта по source или session start.

Дополнительный фактический отказ: _load_profile под uid=1000 не мог прочитать свой 0600 allowlist через /etc/gptadmin (root:root, 0700). Root-owned каталог не раскрывался: добавлен только ACL u:roomhacker:--x, без чтения/listing/write. Прежний ACL сохранён .tmp/r40/gptadmin-parent-acl-before.txt. После исправления реальный helper читает профиль и подтверждает opencode + точный minimax-coding-plan route; файл остался uid=1000 и 0600. auth.json не изменён.

Реальная отрицательная проверка upgrade: заведомо неверный agreed SHA остановил entrypoint с exit=1 до смены release и PID. Evidence .tmp/r40/live-upgrade-refusal.json. API/UI Herder всё ещё старые; canary не создавался до сохранённого live выбора.

Уточнение guard исполнено: selected SHA проверяется как ancestor свежего remote main; archive использует именно выбранный SHA, а не подвижный HEAD. Документный advance remote и чужой WIP допускаются; изменение runtime входов bin/notification_center/deploy/scripts/package.json после согласованного SHA блокирует выпуск. Positive regression red на прежнем guard и green после; unpublished SHA, failed verification и runtime collision сохраняют прежнюю ссылку. 8 deploy checks прошли.

## Итог до Herder admission

Noticeplace live release: /opt/noticeplace-releases/20261009T075324Z-10227d5ecbe8-642263. Четыре фактических runtime input совпадают с опубликованным SHA 10227d5ecbe8eb30458eee419b738138ebba6e81 (.tmp/r40/live-inputs.json). Guard проверяет published ancestry и архивирует pinned SHA; 8 regression/deploy checks прошли. Исторический health backlog остаётся исключён из обработки.

Consumer blocker: Herder service по-прежнему старый (старт 7 октября), GET /api/automation/launch-policy не содержит incidentExecution. Реальный /opt/noticeplace/bin/notify-agent-job возвращает точный no runtime/provider fallback ДО создания сессии. Synthetic canary ещё не создавался: нельзя заявлять работоспособный исходный маршрут по source или старту задачи. Владельцу runner R38 01a11f58 передан один конкретный admission blocker и свежая capacity; единственный publisher 01a11b54 владеет combined build/deploy. Следующий шаг после его live поставки: UI save/readback и .tmp/r40/live-canary.py с одним диагностическим delivery + dedup, затем terminal native provider/model readback и нулевые human/business sends.

Дополнительная live проверка guard: после опубликованного документного advance 79bc7b9 реальный verifier разрешил ancestor 10227d5, не запуская повторный upgrade (.tmp/r40/live-ancestor-verification.json). 180 секунд bounded no-LLM ожидания Herder не показали смену runtime. Canonical Herder tracker уточнил фактического runner owner: Infra 01a11b2c-3f9c-74e2-ab26-19e6f6878d99; ему доставлен один consumer blocker с целевым next action на finite admission/wait для единственного publisher. R38 ранее получил тот же downstream impact как владелец общей capacity, новые payload не создавались. Noticeplace main чист и synchronized; Herder main synchronized, чужой delivery WIP сохранён. R40 остаётся открыта до live UI/save + terminal native canary, а не завершена по опубликованному коду.

## Блокировка бизнес-API архиватором — 09.10.2026

UserIO передал два py-spy снимка: HealthSessionArchiver.run_once:50 удерживает общий center._lock, чтение существующих карточек и dispatcher ждут lock. Свежий EXPLAIN на live DB подтвердил correlated SCAN audit_events; отдельный read-only запрос остановлен SQLite progress handler после 2 секунд. Индексов incident/type в audit_events не было. Пробное чтение тех же userio-43010/userio-42696 в текущем окне дало 200/1514ms и 200/6ms — не доказательство устойчивого устранения.

Узкий fix: только schema index audit_events_incident_type_created(incident_id,type,created_at) в core.py и tests/test_r40_archiver_query.py. Regression на 8000 посторонних audit + 80 session events упиралась в конечные 80000 SQLite steps до исправления; после индексного исправления прошла. 66 focused проверок archive/human requests/HTTP/core прошли. Бюджет тестов: 1GiB virtual memory, 120 CPU seconds, 120s timeout, один процесс. Измеренная available RAM 25GiB; PSI не использовался как запрет. UserIO, Herder и native chats не изменялись; старые cards/jobs/notifications не переигрывались.

Следующий шаг: scoped push, guarded Noticeplace upgrade, EXPLAIN и bounded query на существующей базе, authenticated loopback/public readback тех же двух request IDs; затем один consumer результат владельцу UserIO.

Поставка archive-lock fix: source 9a1a1f299212d2733e65b79eabe6466b9caf917c опубликован; guarded upgrade подтвердил SHA, live release /opt/noticeplace-releases/20261009T083527Z-9a1a1f299212-1005627, core bytes совпадают с опубликованными. На существующей DB создан audit_events_incident_type_created; EXPLAIN заменил SCAN a на SEARCH a USING INDEX. Тот же bounded read-only archive query: 202ms, два кандидата; никаких archive/LLM POST для проверки не делалось.

Authenticated readback: userio-43010 и userio-42696 вернули HTTP200/ID match, loopback 5.18/2.86ms, public HTTPS 30.62/22.81ms. Карточки не создавались повторно. Source/UserIO runtime/Herder/native chats не менялись; перезапущены только штатные Noticeplace services через управляемый upgrade. Пользовательский API результат доставлен владельцу UserIO прямым steer по актуальной сессии; он продолжает прежний same-card consumer flow. Evidence .tmp/r40-lock-repair/{query-after,api-after,live-source}.json. R40 routing/UI canary остаётся отдельной открытой задачей: Herder API пока без incidentExecution.

Проверка через следующий минутный цикл: 8 authenticated GET за 70 секунд, все HTTP200/ID match; latency 2.41–5.55ms, timeout не повторился (.tmp/r40-lock-repair/card-across-archive-cycle.json). Archive-lock incident repair принят по фактическому API; finalUserIOsame-card и исходная R40 UI/native canary не объявляются завершёнными за владельцев.

## Итоговая приёмка R40 — 09.10.2026

Herder live source/main/origin: 00c7b9a23314b52202870dbc3f6ae4fb337b35ee, PID1116921, invocation8add6fd85c044b39b05acccaf525d9a6. Выпуск выполнил publisher; его85 tests/tsc/Vite проверены им, R40 runtime самостоятельно проверен через UI/API/native consumer.

UI: в существующей панели выбран OpenCode → MiniMax-M3.1-Flash-Preview, явные «MiniMax по подписке» и provider minimax-coding-plan. Сохранение через UI подтверждено API и файлом 0600, повторным открытием UI и свежим отдельным процессом AutomationLaunchPolicyStore. Global allowedHarnesses/preferredHarness/models остались прежними Codex/ZCode. Скриншот .tmp/r40/ui-reloaded-subscription.png.

Canary: inc_77eedac22f0b4ea8b03a5221170858cc, два producer events с одним dedup, единственный delivery dlv_80176af69b374ada84a75870d8438bb8 (attempt1, sent). Диагностическая сессия ses_ee0223058ffeMZPuR6QaZb7ZVN единственная; orchestrator sessions=0, исходные продуктовые сессии не возобновлялись и не клонировались. Noticeplace audit agent_job_completed: status=completed, harness=opencode, точный подписочный model, elapsed75456ms. Нативное независимое GET OpenCode /session/.../message: assistant providerID=minimax-coding-plan, modelID=MiniMax-M3.1-Flash-Preview, finish=stop, cost=0. Connected native catalog подтверждает provider/model и endpoint api.minimax.io. Это completed inference, не только session start или queue ACK.

Модель вернула diagnosis_complete, распознала искусственную телеметрию, notify_user=false. Telegram delivery cancelled/attempt0; human/business sends=0. Synthetic incident оставлен muted как доказательство, реальный service recovery по нему не заявляется. Старые incidents/cards/jobs/notifications не replay. OpenCode auth.json не менялся, ключ не выводился и не копировался.

Ремонт использует тот же сохранённый incidentExecution и точный model через helper/API; production code path и focused tests проверены ранее. Дополнительных ремонтных LLM-сессий для canary не создавали по ограничению одной диагностической сессии. Live remediation preflight отверг omniroute и auto с HTTP400 до создания задач; no provider fallback. No-LLM автопродолжение не менялось и не смешивалось с LLM diagnosis.

Evidence: .tmp/r40/{policy-before-save,policy-saved,native-canary-readback,native-provider-proof,subscription-catalog-proof,canary-terminal-proof,live-preflight-rejections,live-canary}.json и ui-reloaded-subscription.png. Archive/card API fix и upgrade publication guard также опубликованы/развёрнуты с ранее записанными consumer proofs. В R40 не осталось обязательного следующего шага; отдельные UserIO/R37/общий health backlog не объявляются закрытыми.
