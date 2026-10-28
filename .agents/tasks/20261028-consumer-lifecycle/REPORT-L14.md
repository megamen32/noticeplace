# L14 — известные потребители и TGC

Работа ограничена уже обнаруженными ingress и точными source seams. Повторный
общий GrepMesh-аудит не запускался. Секреты, конфигурация, сервисы и уведомления
не менялись; реальных вызовов API/БД/Telegram ради теста не было.

## Реестр

`docs/consumer-lifecycle-registry.json` сохраняет 26 известных записей и
`coverage.complete=false`. У каждой записи есть центральный поисковый маркер
`NOTICEPLACE_CONSUMER_V2`; отдельно проверяется наличие маркера в исходнике.
`central_registry_only` не выдается за установленный adapter.

- Инфраструктурные health adapters принадлежат L07; Fleet wrapper сохраняет
  чужой основной collector. Отсутствующий timer не включается при миграции.
- AutoSell hourly/proxy: опубликованные source L08, приложение/SDK/container
  и переезд watch unit ещё требуют L06.
- Video/winramp: source L10 опубликован в `origin/dev`; cron читает canonical
  checkout, но V2 opt-in выключен. Bot bundle остаётся release-owned. Rmdream
  — административное уведомление, а не health watcher.
- ATV cron: L11 source опубликован, grant/SDK/flag ещё не установлены.
- Hermes: review alarm имеет `neuroseller.review.failed`, обычный incident;
  hermes-unified-inbox создает связанные вопросы человеку, не health source.
- UserIO: Gmail notification плюс transport `noticeplace_calls.py`; transport
  не получает права независимой проверки источника.
- Herder/harness-health-runtime — executor/вопросы; агент не подтверждает
  сам себе восстановление.
- Fail2ban — security ban/unban notification, не автоматическое AI-лечение.
- Universal inbox — notification-only. Архивный GitHub repository не
  разблокировался; `cf4d05a` остаётся local-only marker commit. Центральная
  TTL/projection совместимость применяется без нового health checker.
- Integration/QA — тестовые ingress, не постоянно работающие producers.
- Точный `nginx-dev` caller найден в
  `/home/roomhacker/nginx-dev/automation/notify-failure.sh`: подтверждённый
  failed snapshot user-unit → обычный important incident. Это notification
  совместимость; внешний site-monitor использует отдельный `fleet-health`.

## TGC: действующая граница

Точный root — вложенный Git repository
`/home/roomhacker/PycharmProjects/TelegramAuto/TGC`, remote TG_Commentator.git.
Unit `tgc-engine-watchdog.service` запускает canonical
`scripts/tgc_engine_watchdog.py` через локальный `.venv/bin/python3`.
Watchdog читает protected `/etc/telegramauto-ha/tgc/runtime-v2.env`.
Никакие значения credential не читались и не выводились.

Registered identity `consumer_1df8b4668ab24b89b88859f1929d2880`, project
`tg-commentator`, source `tgc-engine-watchdog`, host `server-100`.
В reviewed plan добавлен supplemental verifier grant этого registered
consumer, без нового source bearer; существующий executor переиспользуется.
План НЕ применён.

Исправления исходников:

1. Исторический read-post отказ ошибочно отправлялся как `engine` — теперь
   отдельные `signal_type/problem_type=read_post`.
2. При V2 новые измеренные отказы расходятся по точному `check.name`, со
   стабильным dedup; readiness, CO health, DB, ingest и work не смешиваются.
3. Независимый `SourceVerifier`: GET запроса → свежая конкретная read-only
   проверка → nonce-bound результат через SDK. Recovery/business main не
   вызывается из callback. Неизвестная/чужая группа остается unknown.
4. Старый общий `engine` мог означать CO generation/DB/ATV/killswitch, поэтому
   зелёный readyz не закрывает его. Активная `co_generation` тоже unknown:
   callback не создаёт новую LLM генерацию. Это честная непройденная проверка,
   а не основание объявить сервис здоровым.
5. V2 не посылает legacy resolve, не превращает отклоненный typed event в
   обычный incident и не запускает вторую локальную headless AI эскалацию.
6. Account-loss и task-queue сообщения остаются обычными уведомлениями.

RED: отдельный запуск старого `HEAD:scripts/tgc_engine_watchdog.py` с fake
sender завершился AssertionError: read-post emitted engine. GREEN: 163
targeted/existing watchdog/client tests прошли за 43.68s; SQLAlchemy warning
исторический. Изменённые чужим владельцем watchdog tests только запускались,
не редактировались и не включаются в мой commit.

## Source collision / публикация

Текущая существующая branch `feature/cloudtelethon` HEAD
`025f2b82d0b43ef59097de9d908e57bb78cd85ce`, origin/main
`119bef49e84e97efa457c27373455ac26e346e80`; divergence 5 behind / 22 ahead.
Branch/worktree не менялись. В том же watchdog есть чужие незакоммиченные
CO timeout/unknown-probe hunks. Их нельзя захватывать `commit --only` целого
файла. По разрешению Root новый verifier вынесен в отдельный
`scripts/noticeplace_source_v2.py`; hook hunks выбраны в отдельный Git index
от HEAD, общий index/worktree не менялись. Commit `5edf9f6` содержит ровно
четыре owned paths, 161 insertions / 4 deletions. Чужие CO hunks остаются
working-tree diff (167 insertions / 11 deletions), не попали в commit.
Frozen tree `421858ff79f23fb59c12bdc5e92cd749ee805aa6` проверен отдельно:
три новых regression tests и py_compile прошли без чужого WIP.

Первый push явно отклонён до pytest/mutation: test2git не знал ограниченного
test mapping нового adapter path. Root разрешил исправить штатный mapping
и parametrized fixture в parent TelegramAuto. Никакой bypass не применялся.
Source readiness не равна installed acceptance; публикация feature branch
не означает включение в main.

## Внешний monitor — точный handoff L16

`/home/roomhacker/lol-nginx-admin/monitor/ingest.py` сохраняет telemetry,
отправляет legacy `health.external_site`/resolve и при любой ошибке Notice
Place вызывает `notify_direct`; `monitor/probe.sh` имеет второй прямой
Telegram путь. Дефект подтвержден чтением source, без единой отправки.
L16 уточнил актуальную установку: server100
`/opt/external-site-monitor/probe.sh`, ingest из canonical checkout; VPN2
сценарий — legacy. Действующий probe/SSH dispatch и stale-unit принадлежат
L16. Необходимо независимое свежее измерение после GET, а не replay
`latest.json`; source `external-site:<vhost>`, host `<vhost>`, type `https`.
Отдельный telemetry source имеет type `telemetry`.

## Оставшиеся границы

L06 должен установить SDK/grants/flags/frozen bundles и проверить реальный
consumer path. L16 adapter ещё исполняется. Сервер88 discovery был
частичным/усеченным — весь fleet не объявляется найденным на 100%.
Новые runtime/incident/session proof здесь не заявляются.
