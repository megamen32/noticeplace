# S16/S17 и M01–M13: проверка установленных consumers, 8 ноября 2026

Срез 14:36–14:47 UTC. Это read-only инвентаризация и рекомендации для существующих листьев, не новый план и не приёмка миграции. Код verifier заморожен. Backend/runtime deployment остаётся у Lead. Никаких отправок, звонков, запуска watchdog/producers, изменения flags/credentials/registry или коммитов не выполнено. Единственный запуск verifier — проверенный ранний `--check-config`, без probes/HTTP/state writes. В контейнерах запускался только Python для чтения файлов и package metadata.

## Evidence и границы достоверности

Основной каталог: `/home/roomhacker/agents-projects/noticeplace/.agents/review-artifacts/20261108-verifier/`.

- `manifest.json`: пять owned файлов, SHA-256, base `a7d139293b35789853d01eade01d658188c7ea3c`, исходные RED/GREEN stdout с session/call/line/exit provenance. Девять логов сохранены из сессии, не восстановлены предположениями. 91 combined + 1 отдельный HTTP-error unit = 92 isolated tests; 19 focused входят в combined, их не прибавлять повторно.
- `source/`: точные копии замороженных файлов. `final-snapshot-check.json`: все пять файлов worktree и копий совпадают с manifest; все девять логов совпадают. Тесты повторно не запускались: source не менялся.
- `inventory-server{100,44,88}.json`: units, timers, ExecStart, allowlisted flags, hashes, cron references.
- `supplement-server{100,44,88}.json`: SDK/source hashes, container image identities, безопасные scopes процесса API, flags. Значения credentials не сохранены.
- `container-server44.json`: чтение внутри текущих AutoSell containers, SDK/module hashes и runner.
- `scope-readonly.json`: `Connection(..., readonly=True)` + `PRAGMA query_only=ON`, только SELECT из общей mesh БД; SmartEdge token matching и агрегаты aliases за 7 дней. Не создавался `NotificationCenter`, не вызывался GET verification-requests (он может rearm expired requests).
- Probe scripts рядом позволяют повторить ровно описанное чтение. Полнота fleet discovery не заявляется.

Источники сравнения: текущий worktree `docs/consumer-lifecycle-registry.json` и primary `.agents/tasks/20261107-noticeplace-quiet-operations/evidence/terra-consumer-inventory-20261108.{md,json}`. Primary-код не использован как текущий. Canonical infra HEAD/origin-main `7295c36b091bcd39606b5c9798ffc20c8a7a567f`; использованы root AGENTS, `infra/access-topology.md`, inventory README. Маршруты: local100; `ssh -o BatchMode=yes -o ConnectTimeout=5 roomhacker@192.168.2.5` → `server-44`; `...@192.168.2.75` → `roomhacker-server-88`. На peers infra checkout не создавался.

Terra отражает 26 registry rows и 24 проекта с событиями на 2026-11-07T21:19:04Z. Сейчас registry содержит 27 rows, включая SmartEdge, `coverage.complete=false`. События за неделю, scope, marker или наличие ключа сами по себе не доказывают активный источник/успешную проверку. Пустой env контейнера не доказывает отсутствие конфигурации: wrapper может читать файл внутри него.

## Конкретные оставшиеся активные gaps

| Приоритет | Проверено | Что необходимо перед приёмкой |
| --- | --- | --- |
| SmartEdge | Watchdog timer100 активен. Оба installed executable совпадают с canonical infra. Typed flag отсутствует (default0). Ключи непустые и различаются; `--check-config` exit0. Но verifier credential отсутствует в scopes процесса API100 **и** среди consumer tokens общей mesh БД. | Владелец backend должен установить правильный exact-source verifier grant, проверить peers и scoped negatives. Затем fixed SDK artifact и явный typed cutover. Сейчас cutover заблокирован авторизацией, не импортом SDK. |
| OmniRoute, M02 | На100/44/88 timer active/enabled. Installed `~/.local/bin/omniroute-guard.py` SHA `1ab661ad7dbbcd41a22f35fa9e2ea6b40d6ad7bd465b9927e4e61be2a4fc63ab`, одинаковый на3. `health.degraded` есть; SourceVerifier отсутствует. Installed:1173 emits typed; :1353 использует старый `/health/verification`. Canonical source:1085,1692 уже содержит source verifier. | Это активный legacy adapter, а не inactive row. Поставить canonical adapter через infra owner, сохранить существующую episode/state identity, затем проверить нормальный timer и новый source receipt. Last service status100/88 failed1;44 success. Причина failed1 этим slice не диагностировалась. |
| Router health, M02 | Timer100 active/enabled, `router-health-lite.service:7` запускает `~/.local/bin/router-health-notice.py` в ExecStartPost. Installed notice adapter:36–39 — обычный notify-producer/`--resolve`, без SourceVerifier. Canonical `ops/router-health-lite/router-health-notice.py:52,257` содержит новую проверку. | Разделить существующий ordinary flow и целевой typed lifecycle. Обновлять именно installed notice adapter, не только collector. Его project `openwrt-router-health` не равен SmartEdge `health-monitor`. |
| AutoSell, M03/M08/M09 | Runtime теперь44: `autofind-client`, отдельный `autosell-proxy-source`;100 app отсутствует и hourly timer disabled. На44 timer enabled, runner `~/.local/share/autogram-watch/current/run_watch.py` загружает hourly watcher из installed image через `watch_adapter.py`. V2=1, но `NOTICEPLACE_HOST_ID=server-100` и в app, и в watch env. | Мигрировать фактические образы44 и wrapper; не включать старый timer100. Сначала решить, host_id — сохраняемая logical identity или ошибка переноса: простая замена создаст другую family и оставит старые incidents. Сверить active episodes/ownership до изменения. Последний hourly service44 failed1 — требуется отдельный bounded разбор владельцем, не ручной запуск producer здесь. |
| SDK-копии | `/opt/noticeplace/notification_center/consumer_lifecycle.py` на100/44/88 и отдельный SmartEdge SDK имеют SHA `641d3ab38d039c395f3a897ce0bf03ef093450450a333b829008852217ab6192`; AutoSell containers — `ac0f3c01e1d42a9927d8bec9f1c9c7a20440582c3a30f39288b3a68084df51d6`. Ни один не содержит нового observed_at contract. | Backend update не обновляет Docker/pip copies автоматически. Перечисленные installed hashes — основание для SDK delivery/readback; имя версии0.2.0 недостаточно. Legacy timestamp-free результаты сохраняются в TTL, но pending expiry/restart и sweep fixes требуют обновления SDK. |
| GPTAdmin, S17 | `gptadmin-ci-autofix.timer` user100 active/enabled; service success0; ExecStart прямо canonical `scripts/github_ci_autofix.py once`. :928–937 строит старый verification_id и вызывает `/health/verification`; нового request/generation source protocol нет. | Отдельный incident producer/adapter contract в S17; не считать любой event проекта доказательством этого источника. В7d aggregate большинство `gptadmin.server-100` events вообще без event_type/source_id. Нужен intake→source mapping остальных ordinary notices. |
| ATV coverage, S17 | `atv-views-coverage-{hour,day,week}.timer` enabled/active100. Wrapper передаёт canonical `ATV/ops/watchdog/views_coverage_watchdog.py` внутрь `telegramauto-atv-ha`. Source `views-coverage-watchdog`, custom HTTP client:185–189 читает только первую страницу; :688–746 не продолжает next_cursor и не отправляет observed_at. | Это отдельный активный adapter, не тождественный `telegramauto-cron`. Сохранить producer/consumer split, добавить pagination/checkpoint и stale/readback semantics в рамках владельца ATV либо использовать публичный SDK. Не приписывать ему исправления SDK, который он не вызывает. Legacy timestamp omission пока совместим с TTL. |
| Video bot/selfheal, M04 | `rutubeserver.service` active100; installed `/opt/rutube/current/server/UrlsSevr/telegramcontrol/noticeplace_producer.py` SHA `3b49ebc7fa31f3e857cba603dd2a0e837d6b94d797d4da1f067ae818d49d87cf` emits typed, SourceVerifier в этом файле отсутствует. Source-published/install-pending registry остаётся непроверенной интеграцией. | Проверить фактический call site, script-loaded `~/.config/notify/video-watching.env` и независимый fresh bot predicate; absence verifier в одном файле не доказывает отсутствие другого consumer. Пока install/closure acceptance нет. |

AutoSell images: app `sha256:33411e750d6d0b0e46a605c2a92d65e21c39299bde125bb2f0dc8b3e0e3c4a2c`; proxy source `sha256:f05f5baee8347aa14980c0774f9a404f0bfa066f1b97fbdcf6d584fb4bc4e8a3`. В обоих `noticeplace_client.py` = `a0a7160a12c16abfa1a999a887351f9f7054c52318db3c8fe5795dc5f8640488`, hourly watcher = `9d0ec29aafcb804df7329b3341f91996d4295dd8009c21ca6b4db366b6bbb48e`. SDK используется как vendored module, distribution metadata не найдена; выдумывать pip version нельзя.

## Решения для существующих M-листьев

| Leaf / owner boundary | Текущее доказательство | Оставшаяся миграция / acceptance |
| --- | --- | --- |
| M01 fleet-health / health-incident-monitor; infra | fleet timer100 active, V2=1, installed source hash совпадает registry (`d99e4a…`). Старый health-incident-fleet timer disabled/inactive. | Fresh scope/receipt и SDK readback для fleet. Старый timer не включать; inactive row не требует production canary. |
| M02 omniroute / router-health; infra | Три active Omni timers; active router timer100; installed adapters старее canonical. | Exact installed adapter replacements + state-preserving acceptance, как выше. |
| M03 codex / autosell; infra + AutoFindClient | Codex timer100 active и исполняет canonical `watchdog.py`; verifier вызывается :658 без V2 env gate. AutoSell runtime44 обнаружен. | Codex flag absent не означает inactive: сверить credentials и receipt на фактическом коде. AutoSell SDK/image/host identity — отдельная граница. |
| M04 video-watching / bot producer; video owner | alert_selfheal cron*/5, helper `noticeplace_health.py` содержит SourceVerifier; installed bot producer найден в active server100. | Script-loaded env и exact predicates, SDK; independent bot verification ещё не доказана. Не запускать business jobs. |
| M05 telegramauto-cron / harness-runtime; TelegramAuto + harness | ATV env V2=1, source helper установлен; socproof balance timer активен. Harness registry executor-only. | Readback новых SDK и source grants ATV; custom coverage source отдельно S17. Harness не превращать в source verifier. |
| M06 universal-inbox / fail2ban | Registry ordinary/security notification paths; legacy events не равны health source. | Проверить сохранение ordinary доставки в release acceptance. В этом slice полный runtime inventory этих notifications не выполнялся; новой health migration не обосновывает. |
| M07 hermes / TGC; owners | TGC engine timer active100; cfg читает `/etc/telegramauto-ha/tgc/runtime-v2.env`, V2=1. Source wrapper `tgc_engine_watchdog.py:2125` → `scripts/noticeplace_source_v2.py:20–22` → `/opt/noticeplace`; exact consumer grant в API. | SDK readback и fresh exact predicates (engine/read_post/CO). Grant≠работающий verifier; broad historical engine/CO proof остаётся отдельным. Hermes ordinary notice row не делает все Hermes incidents покрытыми. |
| M08 agent-herder / autosell-proxy | Herder row install_pending; source path TS — producer, installed resolver/verifier не доказан. Proxy container44 работает. | Herder owner подтверждает deployed build и собственный checker; proxy owner обновляет vendored module в image и проверяет state/identity. |
| M09 autosell-hourly / external-site-monitor | Hourly100 retired,44 active replacement (last failed1). External-site probe/stale active100, V2=1, last success. | Не оживлять100. На44 verify normal timer после owner fix. External-site current hashes отличаются registry; update evidence только после current receipt, не по одному hash. |
| M10 capacityforecast / socproof; video owner | Capacity timer100 active; topup-watch root cron*/30; winramp env V2=1. | Обновить shared helper SDK и independently read fresh balance/capacity; покупки/пополнения/повторы jobs не нужны и не выполнялись. |
| M11 rmdream / vtop; video owner | rmdream cron7,22,37,52; vtop23,53; winramp env V2=1. | rmdream административный notice, сохранить ordinary flow; VTOP — fresh source verification через helper. Не путать source marker с central registry marker. |
| M12 nginx-dev / userio | Registry ordinary deployment notifications и transport/Gmail. | Совместимость ordinary notices + recipient policy; transport не verifier. Installed messaging acceptance вне этого slice. |
| M13 integration / QA | QA rows; старый live canary open/unknown, executor sent1 по owner evidence. | Только изолированные tests; старую QA операцию не повторять. Ни production coverage, ни successful native repair этим не доказаны. |

## S17 aliases и дополнительные источники

Read-only mesh SELECT в `scope-readonly.json` уточнил:

- `atv-views` → typed source `views-coverage-watchdog` (1 event); текущий wrapper/credentials также обслуживает `autotelegramviews`. Это source mapping, но historical `atv-views` episode нельзя автоматически перепривязать другому project/consumer.
- `autosell-landing` → `lead.created`, producer `lead_tg`, source `lead_tg:neural-landing` (3). Business lead, не health migration; текущий installed publisher location в bounded source search не подтверждён.
- `codex-daemon.server-100` → `codex-daemon-watchdog` (1 typed event), exact active source уже M03; не добавлять дубликат consumer из-за имени проекта.
- `noticeplace` → `executor.launch_failed`, source `executor-launch-guard` (30). Это внутреннее lifecycle событие, не внешний health checker; не создавать ещё один scheduler/verifier только из event count.
- `rustdesk-autoheal.server-100` → active timer100 и `/usr/local/sbin/rustdesk-autoheal`, notify-producer calls :29 и далее; 18 ordinary events без typed identity. Добавить mapping/status ordinary autoheal notifications, без выдуманной SourceVerifier acceptance.
- `health-monitor` не равен одному SmartEdge: ordinary170, отдельно6 typed `service:aivpn-fallback-access`, также selftest sources. Точный active runtime fallback-access этим slice не локализован; selftest не production coverage.
- `infra`: 2 ordinary events без source_id; active enabled origin остаётся unknown. Не включать guessed producer.
- `gptadmin.server-100`:421 ordinary events плюс установленный CI adapter выше; не считать CI объяснением всех421 без dedup/callsite evidence.

Минимальные S17 remaining owner checks: fallback-access active runtime; ordinary infra/GPTAdmin intake mapping; deployed lead publisher; deployed Herder checker; custom ATV coverage pagination. Registry нельзя объявить complete по этому bounded срезу100/44/88: VPN2/VUSA/HAOS/Mac consumers и runtime aliases полностью не обследованы. Наличие их fleet-health collectors не означает покрытие всех producers на этих узлах.

## SmartEdge: установленный контракт и точный cutover seam

Canonical source: `/home/roomhacker/ServersAdministartion/ops/router-health-lite/`.

| Объект | Проверенная установка |
| --- | --- |
| watchdog | `/usr/local/sbin/router-haproxy-watchdog`, SHA `75cf9f945a9af8e3546d522fb969802cf9c909c825b0383ccde59d8932308879` = canonical |
| source verifier | `/usr/local/libexec/router-haproxy-source-verifier`, SHA `7a655434d9f4b3d879fa5b9826575c817753701b734b3153b8bdb74e17674742` = canonical |
| SDK | `/usr/local/lib/router-haproxy-watchdog/python/notify_center_client`, distribution0.2.0; lifecycle hash `641d3a…`; emitter `ea00056a71554161f50a2f218e557c2b1e2591c2cc6c3fabd7ccdfbde67b053f`, `emit(source_context=...)` config-check passed |
| source tuple | project `health-monitor`, source `router-haproxy-watchdog`, host `router`, signal/problem `https`, correlation `router-haproxy:<episode>` |
| credentials | `/etc/health-incident-monitor.env`: producer+verifier present, distinct=true; producer scope project health-monitor, role verifier with owned_sources=true; separate verifier credential **unrecognized on API100** and no consumer DB match |
| flag/state | `NOTICEPLACE_TYPED_HEALTH` absent/default0; state file `healthy`; active timer. Это snapshot, не разрешение очистить будущий pending state. |

Исполняемый guard watchdog:203 требует distinct credential, executable verifier и successful `--check-config`; verifier:112 требует token+URL+public SDK exports. Infra README `router-haproxy-watchdog.md:40` ещё описывает fallback token, противоречащий этому guard: при следующей owner doc правке исправить prose, не менять рабочий guard ради prose.

Backend owner seam: существующий защищённый `NOTICEPLACE_VERIFIER_TOKEN` должен разрешаться на всех обслуживающих API узлах в **отдельный** scope: project health-monitor, incident_role verifier, principal_id `source:router-haproxy-watchdog`, verification_sources `["router-haproxy-watchdog"]`, verification_owned_sources=false; не executor, не producer fallback. Применять через существующий protected scope rollout владельца backend, без вывода token и без ручного редактирования registry. Это предлагаемый минимальный scope, сейчас он не установлен. Проверить его отрицательными тестами чужого source/consumer/executor до flag.

Команды подготовки SDK для владельца release (НЕ выполнялись):

```bash
TASK_SOURCE=/home/roomhacker/agents-projects/.worktrees/noticeplace-quiet-implementation-20261107
SDK_STAGE=$(mktemp -d /tmp/noticeplace-smartedge-sdk.XXXXXX)
python3 -m pip wheel --no-deps --wheel-dir "$SDK_STAGE" "$TASK_SOURCE/python"
python3 -m pip install --no-deps --no-compile --target "$SDK_STAGE/python" "$SDK_STAGE"/notify_center_client-0.2.0-*.whl
sha256sum "$SDK_STAGE"/*.whl "$SDK_STAGE/python/notify_center_client/consumer_lifecycle.py"
# consumer_lifecycle.py должен быть a32c04d25b19097681b69fc5e7dd29a46cc22fb345a23bba2a41018c5a8e73f6
```

Установка этого exact artifact в отдельный SmartEdge SDK path — release step владельца, с rollback copy и последующим import/hash readback. Version0.2.0 неизменна, поэтому `pip install latest`/version-only readback недостаточны. В этом slice wheel не собирался и installed SDK не заменялся.

После SDK/grants/quiet-policy/native executor readiness, до flag, выполнить только config check:

```bash
sudo bash -c 'set +x; set -a; . /etc/health-incident-monitor.env; set +a
  test -n "$NOTICEPLACE_VERIFIER_TOKEN" && test "$NOTICEPLACE_VERIFIER_TOKEN" != "$NOTICEPLACE_HEALTH_TOKEN" || exit 1
  PYTHONDONTWRITEBYTECODE=1 /usr/local/libexec/router-haproxy-source-verifier --check-config'
```

Точный conditional cutover command приведён в соседнем evidence `smartedge-cutover-command.txt`. Он **не запускался**. Он требует fresh local API exact-source scope, fixed SDK hash, canonical executable hashes, healthy state, затем меняет только typed flag в существующем env с сохранением mode/uid/gid и rollback copy. Peer grants, quiet policy и backend/native executor acceptance — предварительные owner gates; локальный command их не доказывает. Сейчас command завершится отказом на scope, flag останется0. Не выполнять watchdog вручную, не restart/enable timer: следующий штатный tick читает EnvironmentFile.

После owner cutover: config-check + installed hashes + безопасный readback effective env следующего normal timer; для **нового естественного** episode проверить producer receipt, ровно один executor, request/generation/deadline, независимый source result и resolved receipt с точной identity. Не провоцировать outage и не переотправлять legacy/UNKNOWN episode. Только check-config и healthy state не дают lifecycle PASS. Изолированная end-to-end приемка возможна отдельно с QA-only destination, без вызова этого production watchdog.

## Непокрытые acceptance

S16-1 частично: текущие100/44/88 placements/flags/SDK подтверждены для перечисленных sources; полный enabled-origin inventory и aliases ещё не закрыты. S16-2 разделение health/ordinary/executor/QA обосновано, но сохранение ordinary business flow не тестировалось. S16-3 есть совместимый TTL/SDK seam и SmartEdge recipe; installed receipts по каждому источнику отсутствуют.

S17-1 существенно уточнён, S17-2/S17-3 не закрыты: новые source mappings/owner fixes ещё не поставлены, unknowns перечислены выше. `coverage.complete=true` не обоснован. M01–M13 нельзя закрыть blanket-проходом registry; inactive, ordinary и QA не требуют выдуманных health canaries.

S08/S09 source fixes отдельно подтверждены замороженными unit logs. Они не доказывают deployed source_context/executor consumption, source-death continuation в existing server tick, fleet SDK updates или fresh production closure. Server deadline —300s после due; legacy result без observed_at принимается в этом окне как bounded trust. Абсолютной свежести legacy measurement сервер не доказывает. Новый SDK отправляет observed_at; нельзя выдавать cached healthy за новый sample. Повтор UNKNOWN/accepted POST запрещён; expiry400 требует readback supersession до нового probe/request identity.
