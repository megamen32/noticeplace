# NoticePlace child incident links

Status: complete

## Original request

«Да» — сделать дочерние инциденты кликабельными и запустить тестировщика.

## Objective

Make every child incident in Event history a link that opens the bounded
history filtered to that child, then verify the protected admin surface with
a browser-style acceptance check and a live canary.

## Business canary

From `/admin/#event-history`, a visible child link is clickable and lands on a
200 response whose history filter selects that child incident.

## Scope / exclusions

Scope is the NoticePlace admin history UI and its acceptance test. No changes
to delivery semantics, authentication, or unrelated admin sections.

## Initial estimate (immutable)

- Optimistic: 20 active minutes
- Likely: 35 active minutes
- Pessimistic: 60 active minutes

## Completion

- Red regression failed before implementation, then `test_history_page...`
  passed.
- Isolated Playwright browser acceptance clicked the child link and verified
  the filtered URL, selected history value, and HTTP 200.
- Admin service was restarted with the new source and is active.
- Commit `5d86532 feat: link child incidents in history` is on `origin/main`.
