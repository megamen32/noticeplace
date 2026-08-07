# Notify browser QA

Status: work

## Assignment

Tester must inspect the rendered protected Notify admin surface through a browser, exercise the adapter builder, topic editor, live settings, and call toggle, and record every actionable UX or functional issue with evidence. Read-only QA: do not edit source files, do not change production configuration, and do not delete data.

## Acceptance

- Browser interaction attempted on the current Notify admin surface.
- Findings are concrete and ranked; no issue is inferred solely from source text.
- Any blocker includes the exact URL/action and evidence.

## Tester report — 2026-08-07

Verdict: `STOP_MISSING_REAL_SURFACE`

The required real surface is the protected Notify admin UI in a browser. The
available Touchpoint browser/desktop connector could not be reached: each of
the following read-only orientation calls failed with `Transport closed`:

- `touchpoint/diagnostics`
- `touchpoint/windows`
- `touchpoint/apps`

Because no browser window or URL could be inspected, I did not attempt any
navigation, clicks, form submissions, topic changes, adapter changes, call
toggle, service restart, or production-data mutation. No UX finding is claimed
without rendered evidence. Re-run this task when the browser connector is
available; then exercise the dashboard, unified adapter builder, topic
create/edit/delete flow, live settings, and call toggle end-to-end.
