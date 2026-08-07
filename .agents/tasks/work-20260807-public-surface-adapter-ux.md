# Remove public landing and improve adapter builder

Status: work

## Original request

Убрать бесполезную публичную морду Notify, сделать набор адаптеров удобным, затем запустить браузерного тестировщика и записать все замечания в TODO.

## Objective

Keep only the protected operator surface for configuration, replace raw adapter JSON entry with a visual step builder, and obtain an independent browser QA report.

## Business canary

Root Notify endpoint no longer serves a public landing page; protected admin loads a visual adapter builder and can submit a valid multi-step policy; browser tester records actionable UX defects.

## Confirmed scope

- Public `/` landing removal while preserving `/health`, `/v1/*`, and protected `/admin/*`.
- Visual adapter policy builder using the existing generic policy contract.
- Independent browser tester and durable TODO report.

## Explicit exclusions

- No removal of API or health endpoints.
- No adapter transport implementation changes in this slice.

## Initial estimate (immutable)

- Optimistic: 60 active minutes
- Likely: 120 active minutes
- Pessimistic: 240 active minutes

## Initial plan (Russian)

1. Убрать публичную landing-страницу без изменения API.
2. Сделать визуальный конструктор adapter steps.
3. Прогнать браузерного тестировщика и записать TODO.

## Implementation progress (English)

- Public API root now returns 404 JSON; `/health`, `/v1/*`, `/mcp`, and protected admin routes remain unchanged.
- Replaced the raw consumer `policy_json` textarea with a visual adapter-step builder for platform, action, target JSON, retry interval, repeats, and predecessor.
- Focused and broad verification: 32 focused tests passed; full suite excluding the pre-existing inactive `notice` route test passed 114 tests.
- Independent browser Tester was launched, but the Touchpoint connector returned `Transport closed` for diagnostics, windows, and apps. No browser click-through or production mutation occurred; evidence is recorded in `work-20260807-notify-browser-qa.md`.

## Blocker

Live browser QA remains pending until a working browser connector is available.
