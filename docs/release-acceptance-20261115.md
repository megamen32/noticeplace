# NoticePlace: итоговая приёмка 2026-11-15

Статус этого пакета: PASS_LIFECYCLE_WITH_BOUNDED_RECONCILIATION. Полный зелёный health ещё не заявляется: в общем mesh-store остаются 12 старых sending и 1411 uncertain delivery. Их исход нельзя установить этой приёмкой без независимых provider receipts, поэтому они сохранены и не replay'ятся.

## Установленный выпуск

- Версия: 0.1.17.
- Исходный release SHA: 800202ede0d344313a9efafac6aacea08da7c4d3.
- Один immutable runtime на server-100, server-44 и server-88: /opt/noticeplace-releases/20261115T022149Z-800202ede0d3-4082585.
- Нормализованный hash release: a1f236bece4525cd16d51ff07347f72165c25cd97fdc707a41dfea73057b51ca.
- На всех трёх узлах noticeplace-mesh-api.service active; service_ready/storage_ready/dispatcher_ready=true, /health возвращает 503 degraded из-за общего operational debt.

## Исправленный Herder route

CAS-запись policy для alias noticeplace-qa-stop-20261023b выполнена с исходным ETag e3d3954a2f80bb81c78adac00464a9c0 и подтверждена новым ETag 564990d4a3eb38c3ca477bd60b5a0e44.

Фактический route теперь:

- OpenCode: minimax-coding-plan/MiniMax-M3.1-Flash;
- fallback для этого alias: пустой список;
- OmniRoute/Luna для этого incident QA route больше не наследуются.

## Live consumer proof

Новый idempotency scope использовал отдельный fixture и не повторял старые операции:

- incident: inc_f6d7c2a6d75c472892b2b9870177c407;
- delivery: dlv_8b05a4e235594a7697e0ed4b567554a4;
- native session: ses_e2297f6dbffeiXmWSY4lK11gJc;
- repair attempt: np-attempt:f541e331efd14e2b99a1b1bee19cec9f8746bcc3174b9a3b4842511153ea0aaa;
- fresh source check: check_a36f2464419d4365ac25a96762a1a1ff.

Подтверждена цепочка HTTP503 → один manual agent delivery → work_finished → реальный repair attempt → fresh HTTP200 → resolved/healthy. Независимый readback из mesh readonly socket подтвердил одну delivery, human_non_cancelled_count=0, тот же session и minimax-coding-plan/MiniMax-M3.1-Flash. Native session завершена в idle. Fixture остановлен по owned PID; телефонные и Telegram human actions не выполнялись.

Старый UNKNOWN inc_a92140aec5324b7389a03d8d12a4d1e7 сохранён как fenced: без replay и без ручного resolve.

## Bounded reconciliation

Через штатный complete_delivery(cancelled) и per-row predicate recheck погашены 25 queued delivery, которые принадлежали уже resolved/discarded incident без native evidence, pending human request и session-notice payload. Внешних отправок не было; для каждой строки сохранён audit event delivery_terminal_reconciled.

После этой операции общий readback: queued=9, sending=12, uncertain=1411. Оставшиеся 9 queued сохранены: одна acknowledged карточка video-watching и восемь resolved outcome/session notices. Outcome-карточки намеренно не отменялись.

В main опубликован commit a62f3f4 с будущим bounded maintenance sweep. Он повторяет эти guards и пишет audit, не меняя sending/uncertain. Текущий live runtime остаётся exact accepted release 0.1.17; этот source slice войдёт в следующий immutable release.

## Тесты и скорость

- Focused maintenance: 43 passed.
- Focused lifecycle/discard/health/card set: 65 passed.
- Полный serial suite: 1350 passed, 2 skipped, 165.08s.
- Штатный pytest -c pytest-fast.ini -q: 1350 passed, 2 skipped, 38.01s — ускорение примерно в 4.3 раза за счёт xdist -n 8. Это основной практический способ сократить долгий прогон; отдельного одного патологически медленного теста в исследовании не найдено.

## Сохранённые границы

- Foreign dirty WIP, старые evidence и worktrees не reset/stash/delete и не попали в scoped commit.
- F02 24-hour/RustV1 observation остаётся owner-deferred; пять deferred листьев не возобновлялись.
- sending/uncertain не reconciled без независимых receipts.
- Диск server-100/44/88 остаётся высоким (97%/90%/93%); это отдельный инфраструктурный maintenance risk, не скрытый этим release proof.
