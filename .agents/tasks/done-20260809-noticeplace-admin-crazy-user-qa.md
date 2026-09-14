# NoticePlace admin crazy-user browser QA

Status: complete

## Original request

«Пусть он будет безумным типичным пользователем» — прогнать тестировщика по
всей админке NoticePlace.

## Objective

Use an isolated NoticePlace admin instance and a browser to exercise the
operator surface as an impatient, error-prone user: navigation, forms,
filters, create/edit/delete flows, settings, and adapter buttons. External
phone/message delivery must remain blocked.

## Business canary

The browser completes the safe CRUD/settings/history flows and records every
failed interaction with the page and HTTP response.

## Scope / exclusions

Only isolated admin UI QA. No production data mutation and no real phone call,
Telegram message, Matrix delivery, or other external adapter side effect.

## Initial estimate (immutable)

- Optimistic: 30 active minutes
- Likely: 60 active minutes
- Pessimistic: 120 active minutes

## Completion

- The first runner attempt exposed two harness mistakes (late helper
  declaration and missing `page.inner_text` selector); these were fixed before
  accepting the final result.
- Final isolated Playwright run: 12/12 scenarios passed, including auth,
  project CRUD, settings, call toggle, topic CRUD, generic consumer creation,
  history filtering, and both adapter buttons.
- Phone/message adapter buttons were exercised with external delivery safely
  blocked by missing isolated MCP configuration; no real outbound action ran.
- Product fix: generic `policy_json` no longer requires legacy numeric fields.
- Regression suite: `tests/test_admin_console.py` 9 passed.
- Commit `93b3653 fix: accept generic consumer policies from admin` is on
  `origin/main`; admin service was restarted with matching source.
