# Кворум: окончание работы, проверка источника и срок жизни карточки

Запрос владельца, 28.10.2026: позволить сессии сказать «работу закончил»,
не разрешая ей самой подтверждать восстановление; решить накопление открытых
карточек, TTL сутки и source-owned callback/watchdog без обязательной
переделки всех клиентов. Эта запись — результат архитектурного кворума,
не квитанция установки нового lifecycle или массового закрытия карточек.

## Состав и процедура

Независимые предложения: GPT-6.1 Sol (`/root/incident_lifecycle_a`) и
GPT-6 Luna (`/root/incident_lifecycle_b`), затем взаимная критика.
Синтез — текущая ведущая Codex-сессия
`01a17ea3-5600-7580-aba9-1badd2187dd5`.
Свежая проверка синтеза без истории участников: GPT-6 Luna
(`/root/lifecycle_synthesis_review`). Две разные модели, один провайдер;
разнообразие провайдеров не заявляется. Делегирование только read-only,
без рекурсивного найма и production mutations.

Herder catalogs подтвердили обе модели. Live Codex quota 15:44 и повторно
15:52 МСК: weekly used6%, allowed=true, limit_reached=false;
secondary_window=null — дополнительное окно не считалось «0% used».
Обе применённые модели входят в frontier tier council skill. Остаток94%
применимого опубликованного окна выше20%-gate.

## Проверенные исходные факты

Read-only live snapshot NoticePlace, server100, около15:47МСК:
3675 unresolved записей, 3615 старше24ч. Счётчик меняется с новыми событиями.
Среди них notification/notification1359, notification/agent.done352,
incident/incident901, incident/health.degraded847, incident/NULL85,
health.external_site23, operator.call_test15. Следовательно, только ремонт
health lifecycle не уберёт почти половину очереди:1711 записей — два обычных
информационных вида. Production DB и карточки не изменялись.

Уже существует:

- Herder `src/automation/noticeplace-health-observer.ts`: по terminal status
  или трёхминутному idle без активного хода пишет «Исполнитель закончил
  работу… ждём независимую проверку». Это progress, не отдельный durable
  контракт заявленного завершения. Error/stop/idle не доказывают успешную работу.
- NoticePlace `health_workflow.record_verification`, core
  `record_health_verification`/`resolve_health_incident`: отдельные проверки
  и gates source identity/fingerprint/progress.
- `core._source_health_recovery_matches`: typed `health.recovered` от исходного
  source/host/signal/correlation уже может закрывать эпизод.
- ServersAdmin `automation/fleet-health/fleet_health.py:_post_recovery` уже
  отправляет это событие после своей штатной проверки; unknown metrics не
  объявляет восстановлением. Все producer-ы ради callback переписывать не нужно.
- Herder source-group reuse сейчас рассчитан на unfinished sessions. Окончание
  попытки нельзя приравнять к уничтожению binding, если degraded должен
  продолжать ту же сессию.

## Выбранное решение

Три независимых состояния:

1. **Инцидент:** открыт / подтверждённо устранён. Возраст и слова агента
   состояние здоровья не определяют.
2. **Попытка исполнителя:** working → work_finished; отдельно blocked,
   waiting_human, failed, aborted. Явный work_finished идемпотентно относится
   к incident/session/attempt и означает «закончил изменения, проверьте источник».
   Observer idle/error/stop остаётся отдельным наблюдением, не подменяет
   декларацию агента и не выдаёт сбой за успешный результат.
3. **Проверка:** due → pending → healthy/degraded/unknown, с самостоятельным
   сроком ожидания. Агент вправе запросить проверку, но не выдать её квитанцию
   от лица checker-а.

Healthy от актуального авторитетного источника закрывает эпизод. Degraded
сохраняет его и доставляет новые факты в тот же resumable session_id для
следующей попытки. Unknown означает проблему/неполноту проверки: backoff и
понятный статус, без автоматического запуска ремонта сервиса по одному timeout.
HumanStop блокирует продолжение, но не независимую проверку/подтверждённое закрытие.
Удалённая или невозобновляемая сессия — явный blocker; автоматическая замена
на другую сессию не считается сохранением того же ID.

Основной путь — уже работающая периодическая проверка producer/watchdog и
существующий source recovery. Для ускорения opted-in producer получает durable
verification request в своём текущем цикле (pull). При необходимости фиксированный
зарегистрированный callback только будит ту же проверку. Произвольные URL или
shell commands из incident/ответа модели не становятся проверяющими.
Секреты остаются у интеграции. Права verifier определяет authenticated principal
и серверная регистрация source, а не присланная строка actor/verifier_id.

## TTL сутки и накопленные карточки

- Обычные уведомления/agent.done:24ч TTL активной очереди с сохранением истории,
  без выдуманного события «сервис восстановлен».
- Неподтверждённый health incident:24ч означает overdue/unverified. Можно убрать
  его из списка «сейчас исполняется» в отдельную видимую очередь «не подтверждено».
  Критические неотработанные эскалации не скрывать молча. Не писать resolved.
- Источника проверки нет: показать это прямо и не гонять агента бесконечно.
- Часы TTL определить явно: информационная запись — последнее существенное
  событие producer-а, verification deadline — окончание конкретной попытки.
  Heartbeats, polling и перерисовка карточки не продлевают сроки. Возраст
  created_at из live-снимка измеряет backlog, но не доказывает отсутствие
  свежего degraded и сам по себе не является правилом скрытия аварии.
- Старый backlog сначала классифицировать и показать preview. Зарегистрированные
  источники можно перепроверять ограниченными пакетами; без массового replay
  старых AI jobs, accepted/UNKNOWN sends или фиктивных healthy receipts.

## Перекрёстные возражения и проверка синтеза

Sol: новый явный finish-contract обязателен, observer не может быть его
авторитетной заменой; новая проверка нуждается в durable attempt/request binding.
Luna: нельзя требовать nonce/check_id от всех старых producer-ов, потому что
действующий source-owned recovery уже работает. Challenge вводится для нового
внеочередного пути; совместимость должна быть определена отдельно.
Синтез сохраняет оба условия: explicit finish плюс observer как дополнительный
сигнал проверить, legacy recovery без обязательной callback-миграции.

Свежий reviewer: **SHIP-AS-DESIGN**, с обязательным уточнением freshness и legacy
совместимости до приёмки реализации. Нельзя приписать legacy payload гарантию,
которой его протокол не даёт.

### Проверенный контрпример

Изолированный TemporaryDirectory + SQLite, текущий NoticePlace source;
production tokens/endpoints/workers не использовались:

1. health.degraded с correlation episode-1 и дополнительным observed_at=100;
2. новый degraded того же incident с observed_at=300;
3. поздно доставленный health.recovered того же source/correlation,
   observed_at=200.

Фактический результат: same_incident=true, recovery accepted/resolved=true,
final_state=resolved. Сейчас matcher не использует время/поколение.
observed_at здесь — диагностическое дополнительное поле, **не доказательство
нарушения обещанного публичным API временного контракта**. Явный
resolve_health_incident отдельно проверяет последнюю verification; обход этого
пути данным экспериментом не доказан. Риск — принять запоздалое старое recovery
за текущее состояние в новом протоколе без causal binding.

Новый контракт должен связывать результат с episode/generation либо одноразовым
request_id/nonce и актуальной попыткой. Старый ответ не закрывает более новое
degraded. Legacy sources без такого доказательства нельзя автоматически считать
fresh: сохраняем существующий контракт там, где нет неоднозначности; при
неоднозначности требуется свежая зарегистрированная проверка или unverified.
Server receive time не заменяет время/последовательность исходного измерения.

## Сравнение альтернатив

| Подход | Польза | Стоимость/сложность | Риск и задержка | Минимальная проверка |
|---|---|---|---|---|
| Existing periodic probe + typed recovery | Не требует нового клиента | Наименьшие, нужна проверка coverage | Задержка до тика; молча умерший producer оставляет unverified | Источник сам подтвердил healthy после работ |
| Durable source-owned pull request; optional registered callback wake | Явно проверяет после work_finished | Средние; один opt-in adapter, общая очередь запроса/receipt | Потерянный wake переживается штатным tick; generation fence обязателен | degraded→та же session→fresh healthy→closure |
| TTL24ч как resolved | Быстро уменьшает число открытых | Малый видимый код, высокая цена неверной семантики | Скрывает реальную неисправность | Неисправный источник после24ч опровергает подход |

Квота, деньги и latency будущего checker-а не измерялись; численных оценок
стоимости реализации/инференса здесь нет.

## Первый вертикальный выпуск и критерий приёмки

1. Явные executor work_finished/attempt и verification_due, доступные общей
   интеграции Herder→NoticePlace; без зависимости от UI progress toggle.
2. Разделение active attention и incident truth;24ч TTL сначала для
   informational views, health overdue отдельно. Existing producer recovery
   продолжает работать.
3. Один зарегистрированный checker adapter (первый кандидат — existing fleet
   watchdog), request/receipt generation, principal-scoped verifier authority.
4. Incident-bound same-session continuation после work_finished, с сохранением
   STOP/UNKNOWN/no-replay. Не объявлять завершённую попытку «вечно unfinished».
5. Один изолированный цикл: work_finished не закрывает карточку; degraded
   возвращает факты в тот же ID; следующий fresh healthy независимо закрывает.
   Проверить дубликат finish/receipt, старое healthy после нового degraded,
   unknown без нового ремонта, STOP без resume, TTL без resolved и cleanup.

Непройденный переход до внедрения: источник возвращает доказуемо актуальную
проверку именно завершённой попытки, а её degraded продолжает ту же уже
закончившую попытку сессию. Его нельзя объявить работающим по existing progress
строке, возрасту карточки или прежнему canary на reuse unfinished sessions.
Данные production не мигрировались, TTL не включался, старые инциденты не закрывались.
