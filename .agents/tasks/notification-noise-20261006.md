# Меньше уведомлений и служебных чатов

Пользователь просит выбирать обычные уведомления через AI, исключить дубли
этапов в Telegram, называть контроллеры по сути инцидента и архивировать
завершённые ненужные сессии. Настоящие критические события и существующие
звонки сохраняют прямой путь. Телефон выключен; пользователь явно отложил
восстановление и проверочные звонки.

## Подтверждённые причины и исправления

- Живое чтение Telegram Helper Careviolan: 09:49:21–09:54:56 UTC, 335 секунд,
  три новых INFO-карточки завершения Herder (5745–5747), включая стабильное
  состояние без действий. За предшествующий час 72 Telegram-доставки:
  fleet-health 41, hermes 15, nginx-dev 13, tg-commentator 2, agent-herder 1.
- Herder игнорировал decision.notify=false для done. Scoped9f2afbd опубликован
  на main, 23/23 проверки: quiet receipt+audit без Notice, notify=true сохраняет
  отправку/retry. Root Herder включает это в единую сборку; ещё не live.
- Диагностика, планировщик и итог создавали разные сообщения. Теперь только
  подтверждённая исходная Telegram-квитанция определяет редактируемую карточку.
  Этапы/ошибка/восстановление обновляют её; URL сохраняется после планов.
  Поздняя доставка не возвращает восстановленный инцидент в диагностику.
  MESSAGE_NOT_MODIFIED подтверждает применённое редактирование; остальные
  ошибки не трактуются как успех. Нет дополнительных звонков для этих этапов.
- Новые имена: «Диагностика: <суть> · <стабильный suffix>», «План решения: …»,
  «Исправление: …». Сохранена 12-значная часть хэша для разделения эпизодов;
  она вторична. Native ID/ancestry не меняются и не выводятся из заголовков.
- Consumer архива использует только durable diagnosis/planner receipts,
  закрытый health-инцидент, ноль активных/неясных agent jobs, строгий stop gate
  всех исходных native ID (consume=0) и подтверждённый readonly JSON-результат.
  POST /api/sessions/codex/<nativeID>/archive с {}: история остаётся доступной.
  ACK/open/test, исполнители/бизнес, unsupported CLI/ZCode, permissions/active,
  humanStopHeld и неизвестная защита исключены. Нет удаления, SQL-архива,
  очистки hold, fallback или запуска новой сессии. Результат/отложенная причина
  записываются в существующий audit; ошибки не будят пользователя и не
  влияют на завершение задачи. Один batch<=2 в свободном существующем agent
  pool раз в минуту; повтор ошибок не чаще раза в час. Новых потоков нет.
- 24 ранее завершённых контроллера реально переименованы и архивированы
  штатными Codex App tools. Все 24 проверены в archived list; exact-ID чтение
  одной архивной сессии через Herder сохранилось. Рабочие/открытые чаты сохранены.

## Проверки и ограничения

175/175 связанных source/card/helper/worker/stop/interactions проверок прошли.
Бюджет: serial 512MiB soft/1GiB hard, swap128MiB, CPU100%, Tasks64,
IOWeight20, timeout60s; артефакты в .tmp/notification-noise-20261006.
Последняя дополнительная проверка идентичного edit и HTTP adapter:36/36,
final-scoped-tests.log. Native архивный API реализует root Herder отдельно.

Live Notice остаётся89aa85d. Producer stop gate9d5e3d6 опубликован и полон;
exact Herder API-ready SHA/PID ещё не объявлен. До него запрещены Notice
upgrade/launch/canary. После готовности: один согласованный managed upgrade,
один настоящий Helper AI click (галочка→URL same message), проверка карточки
и архива, повторное тихое наблюдение. AutoSeller consumer_e9c5791a0d834f828f743444d3d66286
и его приватный interest token сохраняются. Не переигрывать uncertain звонки.

Отдельный UserIO HumanRequest topic-routing дефект подтверждён соседней
сессией01a110c6; она готовит узкий patch после публикации данного slice.
send_human_request_telegram и HumanRequest route/storage не менялись здесь.

## Обычные health-сигналы сначала оценивает диагностика

Scope строго health.degraded/kind=incident/notice или important, четыре полных
source/host/signal/correlation поля, allowlisted health-diagnosis. Initial
карточка ждёт решения; callback сохраняет принятый native ID тихо. Strict
notify_user=false сохраняет audit/receipt/инцидент и завершённую диагностику,
не создавая карточку/планировщик. notify=true даёт одну исходную карточку с
причиной и ссылкой; ошибки диагностики/missing decision не скрывают проблему.
Critical/emergency, ручное AI и untyped explicit diagnostic остаются прямыми.
Review нашёл и исправил ошибку predicate, которая могла прятать untyped events;
её воспроизведение и исключающие регрессии включены. source gates/native IDs/
sourceSessions/model policy/manual authority не менялись.
Другой review исправил ложное recovery при смене severity: только новый typed
health.phase_changed с точной source identity и отличающимся next_severity
закрывает прежний этап. Он обновляет старую карточку текстом «сигнал ещё активен»,
никогда не выдаёт «восстановилось». Genuine disappearance сохраняет recovered,
независимый verified repair сохраняет resolved. Поздние этапы не меняют вывод.
Краткие заголовки severity Telegram теперь по-русски.

Три bounded subset проверки:84 helper/GPT/source,118 core/card/worker,
32 HTTP/HumanRequest. Один полный запуск был остановлен60s budget; последние
review/phase delta93 и финальные sender/decision/HTTP40 прошли отдельно.
UserIO287933f уже в общей ancestry.
Производитель fleet-health получает отдельный scoped классификатор severity;
предупреждения важны для AI-review, действительное исчерпание ресурсов остаётся
critical. Статус/блокер установки записан в инфраструктурном owning tracker.
Source producer8a97c96 +corrective39fb33f опубликованы,15 checks green; watcher
ещё не установлен до Notice contract и освобождения infra clean checkout.
Runtime inspection обнаружил несовпадение прав архива: Notice daemon работает
как notification-center, а Hub profile принадлежит roomhacker/0600. Consumer
теперь использует существующий локальный NOTIFY_HEALTH_REMEDIATION_URL/CWD
configuration seam без чтения приватного Hub profile. Loopback endpoint и
absolute CWD проверяются; external URL запрещён. Native IDs/stop gates прежние.
Extensionless bin/notify-center production wiring подтверждён integration
regression: настоящий worker_loop→реальный archive consumer→mocked API→durable
audit, без новых потоков/agent delivery. Последний lifecycle subset13/13;
явный persisted test=true тоже исключает auto cleanup без догадок по имени.

Herder API READY480750a437fc337233cb026fb3299b6d41547f22/PID3125891 объявлен root:
real stop/held/blocked automation/explicit same-ID resume прошли Codex/ZCode,
hold пережил restart. ONE Notice managed upgrade и serial manual/quiet canaries
теперь разрешены. Phone/calls отложены.

## Реальная приёмка и corrective terminal seam

ONE managed upgrade d0a54673acb396abc9e510f89ac3a1e17dba8b1f установлен,
release20261006T112818Z-d0a54673acb3-3206756, main/adminPID3208469/3208473,
active/NRestarts0;7 sourcefiles byte-equal. Env и runtime settings сохранены.
/health503 обусловлен историческими1389 uncertain delivery/1347 reconciliation,
storage_ready и dispatcher_ready=true. Никаких blind replay/receiptreset не делали.

Настоящий Helper click Careviolan/540308572 в5822: first selected checkmark
и zero buttons подтверждены readback, затем same5822 canonical native URL.
Диагностика01a110fb-a704-7302-a03f-0fa8100cb466 и planner01a110fc-07d7-7b82-bb96-51a8dda8602f
завершены; plan migration тоже same5822, distinct Telegram IDs ровно1, calls0.
После planmigration убран повторный старый AI выбор (не возвращать выбор после
уже принятого решения). Marker/link/планы и кнопка открытия остаются доступны.

Первая настоящая quiet canary inc_254de5d6834c44d89c8115584f639c5d нашла интеграционный
дефект: raw durable Hubjob b4d6144a4ee73dea1f98cf18331bb549 содержит notify_user=false
и русский reason, helper planner не создал; terminal adapter whitelist потерял
оба поля. Core корректно failopen отправил5824, поэтому quiet acceptance НЕ прошла.
Confirmed red actualstdout-wrapper→signed adapter→worker→core regression добавлен.
Fix сохраняет только strictbool и bounded/redacted reason, missing/invalid остаётся
failopen.58 related adapter/sourcechecks +28 card/HTTPchecks прошли. Owner01a10bc5
разрешил ONE necessary corrective reload и новую quiet canary после reviewedpush;
без намеренных Herder/Notice рестартов в финальном окне результата.

UserIO root получил PIDs/Helperwindow, own dispatcher4567622 restart и новую
важную benign Secretary→Nikita2301/receiver1981288 делает отдельно, no39204replay.
Ждём terminal HumanRequest/receipt перед corrective Notice reload, не прерывать
этот consumer path. Новый пользовательский ZCode queue/autopilot bug сохраняется
следующим ownedslice после Notice приёмки; root release adapter send/flush/queue
и Stop/UserPrompt hooks, без vendor-core/formatter/human-stop field изменений.

Автограм owner1: новая форумная тема5796 группы-1004322359393 и consumer_f9cc6bad25d9458585132b4158531259,
Telegram-only (phone off), maxcritical. Приватный новый token сохранён только
в autoseller-noticeplace.env с0600roomhacker; старые ключи/consumer_e9c… сохранены.
Это authorized routing setup: ни событий, ни звонков при создании не было.

## Final deployed acceptance

Corrective release d2361084cd3945d25e793929dd73340445380182 is live at
/opt/noticeplace-releases/20261006T115443Z-d2361084cd39-3453531,
main3455401/admin3455402 active/NRestarts0. Actual final quiet incident
inc_8748be7099304b029397d58e3521b9a6 completed via Hub5fda0ed782c28cdef462c1c1c85ba9b3
and native Codex01a11112-b1a4-7ac0-b1b0-023c16bc0f00 in42114ms. Its durable
decision is notify_user=false; Telegram delivery cancelled, zero sent messages,
zero calls and no planner. Result captured before Herder's next restart.

Authorized AutoGram ordinary routing card delivered5811 in topic5796, no
customer-interest event or call fabricated. UserIO owner separately proved
Secretary2304→Nikita1981441→event39515→HumanRequest userio-39515→topic5764
message5849; thresholds unchanged, ordinary39507 remained silent.

Ten-minute recipient delivery observation12:26:40–12:36:40 UTC captured11
deliveries:2 owned ZCode test decisions,7 TG Commentator per-batch loss alerts,
one unrelated genuine choice and one existing fleet external-site card edited
in place. TG producer violates its promised hourly bound and is owned in
TGC/account_loss_notifier.py, preserving every loss fact. No global muting.

Quiet test incidents and owned manual5822 incident were explicitly closed;
real AutoSeller customer-interest inc564f remains untouched. Quiet resolve
retains the cancelled Telegram receipt and creates no recovery card.

Production archiver is confirmed running: real CPU diagnosis native
01a11112-b90e-7f53-a7e1-050be7d0888f has durable health_controller_archive
status=archived, incidentinc_429a848a5beb4561afd6e2c48bd55fda; it appears in
desktop archived tasks. Additional resolved readonly controllers continue
archiving in the bounded existing daemon pool.

Found native contract defect during owned explicit test cleanup: first archive
POST01a110fb-a704-7302-a03f-0fa8100cb466 returned502; native desktop lists it
archived, while Herder details uses its now-missing old sessions rollout path
and returnsENOENT. No archive replay/SQL/fallback performed. Herder native
adapter owner01a10b3f owns the exact-ID archive/read repair; remaining3 owned
test controllers' cleanup waits that coherent correction. This blocks full
archive contract acceptance despite native archive visibility being proven.

ZCode queue source ad971cd published separately; root owns its manual-vs-timeout
origin integration and one combined restart. Real final queue proof pending.

## Subsequent real-path receipts and remaining gates

Final attachment/voice release df4befeb0b60203c012a7363f4c0ce2617385463
installed once: /opt/noticeplace-releases/20261006T133411Z-df4befeb0b60-172262,
main173733/admin173743. Existing source/quiet/card/native gates preserved.
UserIO owner proved short original5860 and long document5862 in topic5764;
7053 UTF-8 bytes match the exact original by recipient read and independent
Bot API download. A later explicit request adds old-card backfill5842/5845/5849
and composite original+AI Markdown; this next source/acceptance slice is owned
by 01a10bc5. No unreviewed reload or duplicate messages by this agent.

The phone owner alone performed the newly authorized one loud call after the
phone returned online: dlv_932dc89f7f3745b68652998cb2089c64 sent/attempt1,
call42e0e2f5-502c-4d8c-b88c-c1968dbfa275 answered with actual "Да, я услышал".
Owner confirmed channels0/calls0 and durable acknowledgement. Historical
uncertain receipts remain unchanged; this agent made no physical calls.

Herder0a4f846 immediate archive contract now passed for owned readonly planner
01a110fc-07d7-7b82-bb96-51a8dda8602f: archive200/ok/exactID, immediate
GET details200 with3messages. Root separately verified already archived
01a110fb GET200; archive was not repeated. Two remaining synthetic readonly
quiet controllers remain unarchived: 01a110fc-5046-7192-984a-7b0f40a8afb5 and
01a11112-b1a4-7ac0-b1b0-023c16bc0f00. Their incidents are resolved with0active
agent deliveries; details200/idle/no permissions and native SQLite archived0.
Cleanup preflight context subsequently timed out/reset when the user manager
and Herder runtime changed; no archive POST reached. Resume cleanup only after
strict context/readiness is restored, and preserve exact native IDs/history.

TGC source dadd04a+7809815 is committed and tested (8+6 focused checks), but
its main publication/runtime is owned by canonical TGC owner
sess_e6d8e941-5787-4c51-a3a1-c578e7825245. Required gates: quorum review,
HA cutover receipt and explicit protective-stop release. No TGC restart,
pre-push bypass or parent gitlink publication by this agent. This remains an
external completion blocker; source-only is not a deployed noise fix.

Owned ZCode sess5af9 real off/same-ID queue and archive checks passed. Final
acceptance found repeated terminal choices and injected coordination notes
mistaken for task context. Narrow index.ts/core regression fix is owned here;
root manages one build/restart after review. Signed human selection and final
held state must be proved after this correction; see Herder task tracker.

After the external user-manager reset recovered to Herder666185, strict
context again reported false for both owned quiet controllers. Each received
ONE archive200/exactID and immediate details200/3messages; all owned Codex
controller cleanup is complete. Evidence is final-controller-archives.json
in the task's private Codex visualizations directory. No archive replay.
Herder terminal/coordination-noise correction8a151ff is reviewed/pushed;
root owns the one intentional final build/restart and same-ID canary follows.

2026-10-06 17:50 MSK owner instruction supersedes the prior TGC gate tracking:
TelegramAuto work is handed off completely to ZCode
sess_e6d8e941-5787-4c51-a3a1-c578e7825245; publication/runtime pickup decisions
are his. Codex stops TelegramAuto edits, checks, monitors and control. Prior
source/test receipts are historical evidence, not current constraints. No
further TelegramAuto checkout access or publication by this agent.

The subsequent reviewed original-backfill/composite-MD ONE managed Notice
upgrade is explicitly handed to 01a10bc5, after his coherent clean source and
foreign phone-owner slice publication. No duplicate upgrade by this agent.

Latest original-message clarification is COMPLETE via delegated owner receipt:
live daa39d2ceaae712871854df2c7cfef8debc0d1c7,
release20261006T150303Z-daa39d2ceaae-953960, main955716/admin955719,
active/NRestarts0. Nine protected/new source files byte-equal, env/settings
preserved. Backfill SAME old Telegram IDs5842/5845/5849 shows exact originals;
resolved5849 retains Отправить1 and actors/choices/expiry/recipients/timestamps.
One authorized benign composite proof: card5899/document5900 in topic5764,
7955 UTF-8 bytes, SHA256
cb32f77313df8f5364a33b570c740374902e3e57a7c08d5f2eb110d3728ef96a
matches recipient read, independent Bot API download and expected formatter.
Full4000-character original and persisted AI analysis are both present.
URL https://t.me/c/4322359393/5764/5900. Independent UserIO root accepted.
No DM/AI/call/reply/account switch. Prior raw document5862 stays intact.
Current source0cc1f0165930126e3de460504b1cda25be84a5d9 docs-only after runtime,
fetched clean HEADorigin before this task's final documentation update.
No further Notice upgrade is needed for this slice. Full delegated evidence:
docs/human-request-originals.md and owner04358ca/0cc1f01.
Remaining original umbrella acceptance is Herder same-ID ZCode cold-resume/
signed-selection proof; TelegramAuto work was explicitly handed to ZCode.
