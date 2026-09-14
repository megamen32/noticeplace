# NoticePlace event history and incident audit

Status: complete

## Original request

«Ставь как цель сделать историю событий на сайте: кто с какого IP по какому
плагину в какой тип события когда стучался, какие дети, какие были совершены
нотификации, какой из результат.»

## Objective

Create an admin-site event history that reconstructs one complete operational
story: authenticated producer/client, source and proxy IP metadata, target
plugin/adapter, event type and timestamp, parent/child incidents, notification
attempts, and terminal outcomes.

## Acceptance shape

- Search/filter by time, project, producer/client, event type, incident, and
  correlation/idempotency key.
- Show trusted connection metadata separately from forwarded proxy metadata;
  preserve raw values only under the existing access policy and redact secrets.
- Show the parent incident and linked children as a navigable audit tree.
- Show each notification attempt (channel, consumer/profile, attempt/retry,
  state, timestamps, and bounded error/result) without pretending that an
  accepted request means delivery succeeded.
- Show the final outcome for every event/incident/child/delivery and retain
  independent parent and child state transitions.
- Retries and duplicate event submissions remain linked to the same durable
  records through idempotency and correlation keys.

## Scope

- NoticePlace persistence/API/admin UI design and implementation plan.
- Integration points for producer/plugin identity, source/proxy metadata,
  parent-child incident links, and delivery audit.

## Explicit exclusions for this pass

- Additive schema migration, admin history UI, and production deployment are
  in scope for this implementation.
- No automatic parent/child inference from free-form text.
- Do not expose bearer tokens, HMAC secrets, phone numbers, or arbitrary shell
  command contents in the history view.

## Initial estimate (immutable)

- Optimistic: 90 active minutes
- Likely: 180 active minutes
- Pessimistic: 360 active minutes

## Current evidence and next step

NoticePlace already persisted event, incident, delivery, idempotency, and retry
records. The implementation now adds the explicit parent-child relation and a
unified operator history contract.

## Implementation evidence — 2026-08-09

- Added durable provenance fields to events/incidents: event type, producer,
  plugin, correlation, parent event/incident, peer/source/proxy IP, and bounded
  forwarded-for metadata. Existing databases migrate with additive columns.
- Added NotificationCenter.list_event_history() with bounded filtering,
  parent/child links, notification status/attempt/error, audit records, and
  aggregate outcome; audit values redact secret-like keys.
- Added an Event history table and filter to the protected NoticePlace admin
  console. Focused core/admin/http/delivery/policy tests pass: 56 passed.
- Migration rehearsal against a copy of the current production SQLite database
  succeeded; all new columns were added and three history rows were readable.
- Live deployment: source modules were installed to `/opt/noticeplace`, both
  Notify services restarted successfully, and health returned HTTP 200 with
  storage/dispatcher ready.
- Live authenticated admin canary: `/admin/` returned HTTP 200 with Event
  history; bounded empty and filtered queries also returned HTTP 200.
- Commit/push: `49360cd feat: add event history audit to admin` is on
  `origin/main`.

## Completion

- Focused verification: 56 tests passed; `git diff --check` passed.
- Runtime verification: source and `/opt/noticeplace` module hashes match;
  `notification-center.service` and `notification-center-admin.service` are
  active; health and authenticated `/admin/` history canaries returned 200.
