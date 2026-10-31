# Отчёт: L01 + L02 + L03 (восстановление consumer-lifecycle ролей)

Дата: 2026-10-31. Исполнитель: opencode session `np_role_restore_1031` (`ses_e6dca8c4affeI4nq6BX2eLjTMI`), checkout `/home/roomhacker/agents-projects/noticeplace`, ветка `main`.
L04 (живая приёмка) — не выполнялся по условию листа.

## L01 — Восстановление v2-полей в карте токенов — DONE

Скрипт: `.agents/tasks/20261031-consumer-role-restore/restore-v2-scopes.py` (сухой прогон, затем apply, отдельно на каждом узле).

Источник: артефакт cutover `/var/lib/notification-center-admin/rollbacks/1793319447-32213782/notification-center.env`
- sha256 файла: `98e499ef42ee2496e76edc80be12b6772277d58c0e73bd3bcaa15a4ea81f0fef` (одинаков на 100/44/88)
- 53 токена, все с v2-ролями; `scope_hash` артефакта = `8914b2fd…2d602` — совпадает с reviewed candidate.

Было (до восстановления, одинаково на 100/44/88): 54 токена, `scope_hash=0a2a4e28…406a0e`, `incident_role` у **0**, проектов 28.

Стало (после, одинаково на 100/44/88): 54 токена, `scope_hash=3853c7e3…b8261e`, `incident_role` у **53**, `principal_id` непустой у всех 53 v2-токенов, проектов 28. `v2_subset_hash` (53 токена с ролями) = `8914b2fd…2d602` — точное совпадение с reviewed candidate на всех трёх узлах.

Единственный plain-токен без роли: проект «Олег и Никита» — сохранён plain, как требовалось.

Гарантии, проверенные скриптом и после записи:
- Значения токенов не изменились: `set(токенов)` до == после, `project` каждого токена до == после.
- Политические поля (`max_severity`, `agent_jobs`, `automatic_health_diagnosis`) взяты из живой карты, 0 расхождений с ней.
- Запись атомарная (mkstemp + fsync + rename + fsync каталога), `mode=0600`, `uid=0 gid=0` сохранены.
- Резервная копия перед записью + откат = восстановление копии:
  - 100: `/var/lib/notification-center-admin/rollbacks/20261031-v2-restore/notification-center.env.pre-v2-restore`, sha256 `74b356c8…a64a4`
  - 44: та же директория, sha256 `253f5f2d…91ae4`
  - 88: та же директория, sha256 `4d139a94…d8160`
- Callback-карта `/home/roomhacker/.config/gptadmin/health-callback.json` НЕ менялась (mtime 2026-10-28 21:55, sha256 `c4b35bb6…a6ad`).
- Продюсерские env не тронуты (mtime не изменились): `/etc/fleet-health.env` (28.10), `/etc/fail2ban/notify-center.env` (01.08), `/etc/gptadmin/notify.env` (05.09), `/etc/telegramauto-ha/tgc/runtime.env` (03.10), `runtime-v2.env` (30.10).
- Порты, БД и прочие настройки узлов не менялись.

Сервисы после записи: `noticeplace-mesh-api.service` перезапущен и `active` на 100, 44, 88; ошибок в journal (`-p err` за 5–6 мин) нет.

## L02 — Миграция source_principal в mesh-БД — DONE

БД: mesh `/run/noticeplace-mesh/sql.sock` на server-100 (доступ через `notification_center.mesh_store.connect`, не raw sqlite). Миграция однократная, транзакция `BEGIN IMMEDIATE … COMMIT` через контекст соединения.

Соответствие `credential:<sha256(токен)>` → `principal_id` вычислено из v2-карты артефакта; уникальность проверена заранее: 0 неразрешённых хешей, 0 коллизий (один хеш → один principal).

Фактические числа:
- Строк к замене: **37** (лист ожидал 36; между планированием и выполнением live-сервис записал один новый валидный эпизод `source:omniroute-guard` — та жеcredential-привязка, тот же principal, поэтому замена корректна; см. «Отклонения» ниже).
- Разрешено: 37/37, 0 unresolved, 6 различных целевых principal: `source:omniroute-guard` (19), `source:fleet-health` (11), `source:agent-herder` (4), `source:video-watching` (1), `source:winramp` (1), `source:codex-daemon.server-100` (1).
- Внутри транзакции проверено: после UPDATE `credential:%` = **0 строк**; `count(*)` таблицы не изменился (2409 → 2409); по каждому из 37 эпизодов все поля кроме `source_principal` побайтно совпадают со снапшотом до; каждый новый `source_principal` равен `principal_id` из v2-карты.
- COMMIT выполнен; после коммита: `credential:%` = 0, total 2409.
- `noticeplace-mesh-api.service` на 100/44/88 после рестарта `active`, ошибок в journal нет.

## L03 — Фикс AdminConfigStore._scopes + регрессионный тест — DONE

Изменено:
- `notification_center/admin.py` — `_scopes()` больше не отбрасывает v2-поля: сохраняет `incident_role`, `principal_id`, `verification_sources`, `verification_owned_sources`, `verification_consumer_ids` и валидирует их значения **по каждому scope отдельно** (по образцу проверок `deploy/consumer-lifecycle-credentials.py`): роль из {producer, verifier, executor}; `principal_id` — непустая строка ≤128 байт без control-символов; `verification_sources` — список явных уникальных exact-идентичностей без `*`; `verification_owned_sources` — boolean; `verification_consumer_ids` — список явных значений без `*`. Валидация project/max_severity/agent_jobs/automatic_health_diagnosis не ослаблена. **Глобальная уникальность principal в `_scopes()` сознательно НЕ введена** (по уточнению ведущего: это ограничение подготовки кандидата в deploy-скрипте, а не правило чтения живой карты). Новых отвержений известных полей нет; неизвестные поля, как и раньше, молча отбрасываются (существующее поведение сохранено).
- `tests/test_admin_console.py` — 6 новых тестов: сохранение v2-полей при нормализации; сохранение v2-полей при `set_project_severity` (меняется только severity); отбрасывание неизвестных полей (как раньше); отвержение невалидных v2-значений (5 кейсов); duplicate-principals НЕ отвергаются (per-scope правила); (шестой кейс входит в проверку невалидных значений по subTest).
- `tests/test_health_auto_diagnosis_scope.py` — 2 устаревших теста чинены только в чтении env: теперь через канонический `parse_environment` (они парсили JSON вручную и сломались ещё до моей задачи — на HEAD `eae1374`, изменившем формат записи env на quoted-UTF-8; проверено: падают на чистом `eae1374` без моих правок).

RED→GREEN доказан:
- RED: 4 новых теста падают на коде без фикса (`git stash` → run → 4 failed, 1 passed).
- GREEN: после фикса все 5/5 целевых тестов проходят.
- Полный набор: `pytest tests/test_admin_console.py tests/test_admin_topic_routes.py tests/test_health_auto_diagnosis_scope.py` — **48 passed, 0 failed** (после чиненых 2 устаревших).
- `pytest tests/ -k 'admin or console or scope'` — зелёный в части моей области; 3 оставшихся failed в `tests/test_repair_first_incident.py` — **предсуществующие** и вне листа: падают одинаково с моими правками и без них (проверено на чистом HEAD), касаются Herder repair-first шаблонов, не admin/scopes.

Живая карта (acceptance L03): нормализация `_scopes()` на реальной `/etc/notification-center.env` после L01 — 54 токена, `incident_role` у **53** (было бы 0 до фикса), `principal_id` у всех v2, проектов 28, `set(токенов)` совпадает, нормализованный хеш == хешу файла (нормализация теперь идемпотентна и ничего не теряет).

Деплой L03 тем же релизным путём, что и L01 (`deploy/noticeplace upgrade` из опубликованного checkout):
- 100: release `/opt/noticeplace-releases/20261031T144217Z-c031ab6dfb7e-3225467`, verified published source `c031ab6…ac21`.
- 44: release `20261031T144648Z-c031ab6dfb7e-2756135` (клон `megamen32/noticeplace` в /tmp, там нет постоянного checkout).
- 88: release `20261031T144700Z-c031ab6dfb7e-820940` (аналогично).
- На всех трёх: `_validate_lifecycle_field` присутствует в установленном `/opt/noticeplace/notification_center/admin.py`, `noticeplace-mesh-api.service` active, ошибок в journal нет.

## Коммит

- `c031ab6dfb7e5df48d57dfb11d645c623683ac21` — `fix(admin): console actions preserve lifecycle-v2 scope fields` — запушен в `origin/main` (main синхронизирован, рабочая копия по моим путям чистая).
- Пути коммита: `notification_center/admin.py`, `tests/test_admin_console.py`, `tests/test_health_auto_diagnosis_scope.py`. Скрипт восстановления и этот отчёт — в `.agents/tasks/20261031-consumer-role-restore/` (незакоммиченные, служебные; коммитить не стал, чтобы не расширять область — ведущий может закоммитить при желании).
- Чужой WIP не тронут: `bin/notify-center` (модифицирован кем-то другим, остался в рабочей копии без коммита), `.tmp/ui-screens/*`, `.agents/shared-session/knowledge/code-map.json`.

## Отклонения от листьев

1. **L02: строк 37, а не 36.** Между написанием листа и выполнением миграции live-сервис записал один новый эпизод, привязанный к credential-principal. Хеш разрешается тем же principal (`source:omniroute-guard`), коллизий нет, замена идентична остальным 36. Заменены все 37 — иначе остался бы один «застрявший» credential-эпизод, и acceptance «0 строк credential:%» не был бы достигнут.
2. **L03: дополнительно чинены 2 устаревших теста** в `tests/test_health_auto_diagnosis_scope.py`. Причина: acceptance L03 требует зелёный `pytest tests/ -k 'admin or console or scope'`, а эти 2 теста падали ещё на HEAD `eae1374` (до моих правок) из-за ручного парсинга env-JSON, сменившего формат в `eae1374`. Исправление только в чтении тестов (канонический `parse_environment`), производственный код не менялся.
3. **L03: глобальная уникальность principal не проверяется в `_scopes()`** — по прямому уточнению ведущего (это ограничение подготовки кандидата в `deploy/consumer-lifecycle-credentials.py`, не правило чтения живой карты).
4. **Деплой на 44/88** выполнен из свежего клона `megamen32/noticeplace` в `/tmp` (постоянного checkout на этих узлах нет), тем же `deploy/noticeplace upgrade` с тем же verified SHA; временные каталоги удалены после установки.

## Отчётные отпечатки (без значений токенов)

- Артефакт: файл sha256 `98e499ef42ee…f0fef`, scope_hash `8914b2fd…2d602`.
- До: scope_hash `0a2a4e28…406a0e`, ролей 0.
- После: scope_hash `3853c7e3…b8261e` (100 == 44 == 88), ролей 53, v2_subset_hash `8914b2fd…2d602` == candidate.
- Backup-копии: 100 `74b356c8…a64a4`, 44 `253f5f2d…91ae4`, 88 `4d139a94…d8160`.
- Коммит: `c031ab6dfb7e…ac21` в `origin/main`.
- Релизы: 100 `20261031T144217Z-c031ab6dfb7e-3225467`, 44 `20261031T144648Z-c031ab6dfb7e-2756135`, 88 `20261031T144700Z-c031ab6dfb7e-820940`.
