# NoticePlace: live acceptance 0.1.18 — 2026-11-15

Статус этого пакета: **PASS_LIVE_LUNA_WITH_BOUNDED_DEBT**. Один и тот же
опубликованный source SHA установлен на трёх mesh-узлах и прошёл свежий live
consumer canary. Общий health остаётся degraded из-за исторических delivery и
incident debt; эти строки не списывались массово и не replay'ились.

## Выпуск и fleet readback

- Версия: `0.1.18`.
- Source: `3969f5a24250c9924c60c7cf04c4155183dba82a` (`origin/main`).
- Один переносимый git bundle: SHA-256
  `81a2ef03784db4ef6b278117bca7312fb02a10ebef6d5f4a272085b4280ee6a1`.
- Одинаковые хеши установленного `VERSION` и
  `notification_center/maintenance_expiry.py` на 100/44/88:
  `447836d46f39c5900faf74ad6a6554b8e1fafeca1f78de0416d5f05c6e09a080` и
  `6c2210cf4c4e5868ebeb533e08a891bf4cd48ba7c1716620548ac94b13c31ba2`.
- server-100: `/opt/noticeplace-releases/20261115T133937Z-3969f5a24250-2366970`.
- server-44: `/opt/noticeplace-releases/20261115T134252Z-3969f5a24250-402786`.
- server-88: `/opt/noticeplace-releases/20261115T134316Z-3969f5a24250-217105`.
- `noticeplace-mesh-api.service` active на всех трёх узлах; предыдущий
  `0.1.17` сохранён через `previous` для rollback.
- Read-only fleet snapshot:
  `queued=2`, `sending=12`, `uncertain=1413`, `failed=3048`,
  `open incidents=6688`. Sending/uncertain остаются fenced до независимых
  provider receipts.
- После rollout в journal этого unit на всех трёх узлах новых записей нет;
  факт работоспособности подтверждён service/readback и live consumer path.

Полный машинный snapshot сохранён в
`.tmp/live-acceptance-0.1.18-20261115/release-fleet-readback.json`.

## Live consumer proof

Для QA alias был выполнен CAS update Herder policy:

- старый ETag: `"564990d4a3eb38c3ca477bd60b5a0e44"`;
- новый ETag: `"c73b48720c5dc65b65ca2c1e9aa4af20"`;
- effective model: `omniroute/cx/gpt-6-luna`;
- `fallbackModels.opencode=[]`;
- новый fixture: `/home/roomhacker/agents-projects/noticeplace/.tmp/np-live-20261115-luna-7b8d3e1a/fixture`.

Один свежий muted QA-сценарий подтвердил полную цепочку:

`event → одна agent delivery → Luna session → ровно одна repair attempt → fresh source HTTP 200 → check-result → resolved`.

Идентификаторы и результат:

- incident: `inc_957844a330ad4f1a8df78e8378e8f460`;
- delivery: `dlv_e5f9358ef9bc46bc8ac9540a38ac8051` (`sent`, attempt 1);
- session: `ses_e20827cc5ffeGGLFL2sxyIsbeY` (`idle`,
  `omniroute/cx/gpt-6-luna`);
- repair attempt: `np-attempt:3fc5b6e187b239ccfd3efe58a64e35cd2c12d85d79df570748b00ab1cbad0e68`;
- fresh check: `check_319be2bb40bb47b98ab61121a057de7e` (HTTP 200);
- independent mesh readback: `resolved`, `work_finished`,
  `verification_state=healthy`, `verification_due=null`, ровно одна delivery,
  `human_non_cancelled_count=0`.

Evidence находится в `.tmp/live-acceptance-0.1.18-20261115/luna-run/`:
`final.json`, `operation-check.json`, `native-session-readback.json` и
`independent-readback.json`. Телефонные и человеческие Telegram-доставки не
использовались. Старый MiniMax canary не replay'ился.

## Проверки и скорость

- `tests/test_maintenance_expiry.py`: 43 passed.
- deploy/publication guards: 12 passed.
- `pytest -c pytest-fast.ini -q`: 1350 passed, 2 skipped, 35.21 s.
- Источник bounded terminal-delivery sweep опубликован в `a62f3f4` и теперь
  входит в установленный `0.1.18`.

Основное ускорение тестов даёт `xdist -n 8`: ранее тот же полный набор в
serial занимал около 165 s. Доказательств Python CPU hot spot, который имело бы
смысл переносить в Go, в этом прогоне нет.

## Сохранённые границы

- Чужие `.agents/**`, evidence, backup-файлы и worktrees сохранены; в release
  попали только опубликованные scoped changes.
- F02/RustV1 24-hour observation и остальные owner-deferred leaves не
  возобновлялись.
- Исторические `sending`/`uncertain` не закрывались без независимых receipts;
  старый UNKNOWN incident остаётся fenced.

