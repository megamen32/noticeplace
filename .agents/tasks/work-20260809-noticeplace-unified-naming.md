# NoticePlace unified naming

Status: work

## Original request

Почему runtime продолжает называться notification center, если продукт уже NoticePlace? Сделать единое именование везде.

## Objective

Сделать NoticePlace единым публичным и operational-брендом, не ломая действующие producer API, env contracts, Python imports и существующую базу.

## Business canary

`https://notify.bezrabotnyi.com/admin/` продолжает открываться через текущий auth-flow; `noticeplace.service`/`noticeplace-admin.service` aliases показывают тот же healthy runtime; документация и service descriptions больше не показывают старый бренд как название продукта.

## Confirmed scope

- README/docs/user-facing labels.
- systemd service descriptions and NoticePlace aliases.
- deployment labels and watchdog presentation strings.
- compatibility note for retained technical identifiers.

## Explicit exclusions

- No database move or rename.
- No breaking rename of `notification_center` Python package or `NOTIFY_CENTER_*` API env variables.
- No producer token or adapter changes.

## Initial active-minute estimate

- optimistic: 20 minutes
- likely: 40 minutes
- pessimistic: 70 minutes

## Result

- Service descriptions now use NoticePlace; enabled compatibility aliases `noticeplace.service` and `noticeplace-admin.service` point to the existing units.
- User-facing API/operator docs and deployment prose use NoticePlace; legacy technical identifiers are explicitly documented as compatibility names.
- Live admin canary returned HTTP 200 with the authenticated dashboard; health and admin services remained active.
- Focused verification: 19 tests passed and Python compilation passed.
- Commits: `c440e56` (naming); previous admin DB fix remains `10f3feb`.

Status: complete
