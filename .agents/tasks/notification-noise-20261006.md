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

Автограм owner1: новая форумная тема5796 группы-1004322359393 и consumer_f9cc6bad25d9458585132b4158531259,
Telegram-only (phone off), maxcritical. Приватный новый token сохранён только
в autoseller-noticeplace.env с0600roomhacker; старые ключи/consumer_e9c… сохранены.
Это authorized routing setup: ни событий, ни звонков при создании не было.
