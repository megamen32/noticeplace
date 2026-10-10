# Admin UI refresh — verified pages

Screenshots taken with headless Chrome at 1280 px width from the rendered
`notification_center.admin_http` templates (dashboard rendered from a synthetic
snapshot; every template function exercised). No real tokens or secrets appear.

Before (live service HTML fetched 2026-10-10, old design):

- `before-top.png` — /admin/ viewport at the top
- `before-health.png` — health dashboard section
- `before-full.png` — tall overview (health incidents dominate)
- `before-forms.png` — forms/tables overview (layout preview)

After (all template pages):

- `after-01-top.png` — /admin/ top: topbar, health dashboard, add producer
- `after-02-health.png` — health dashboard with fleet targets and incidents
- `after-03-full.png` — whole /admin/ page: producers, calls, adapters, timers,
  consumer builder, delivery profiles, event history, Telegram topics
- `after-04-error.png` — `_page()` (validation error / 403 / 404 surface)
- `after-05-denied.png` — access denied page
- `after-06-test-result.png` — adapter test result page
- `after-07-producer-token.png` — producer one-time token page
- `after-08-consumer-token.png` — consumer intake token page

Zoomed sections (1:1):

- `zoom-health-edit.png` — health monitor config editor (details open)
- `zoom-projects.png` — producer projects table with severity badges
- `zoom-timers.png` — live delivery timers form (Russian labels preserved)
- `zoom-consumer.png` — scoped consumer builder with adapter step fieldset
- `zoom-profiles.png` — delivery profiles table
- `zoom-history.png` — event history with notification/outcome badges
- `zoom-topics.png` — Telegram topics table and edit cards
