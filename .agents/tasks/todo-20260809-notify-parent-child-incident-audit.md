# TODO: parent-child incident audit graph

Status: todo

## Original request

«Было бы неплохо, если бы Notify ... мог отслеживать дочерние инциденты ...
Упал сайт — у него есть ребёнок, что сайт остановился. Аудит.»

## Idea

Allow NoticePlace to represent an incident tree: a parent operational incident
(for example, “site is down”) may have linked child incidents/events (for
example, “service stopped”, “health check failed”, or “restart attempted”).

## Desired audit behavior

- Every parent/child link is explicit, durable, and queryable.
- The audit view shows who created the child, when, by which project/producer,
  and the triggering event/job correlation.
- Parent and child state transitions remain independently visible; resolving a
  child must not silently resolve the parent.
- Telegram/operator cards can show a bounded “child incidents” summary without
  leaking credentials or untrusted command text.
- Duplicate/retried child events remain idempotent and do not create a second
  link for the same observation.

## Smallest evidence / current blocker

Current NoticePlace incidents have stable `incident_id` and event/delivery
records, but no explicit parent-child relation or incident-tree audit contract.
This is a product/data-model task; do not implement it as part of the current
GPTAdmin supertest or phone work.

## Explicit exclusions for this TODO

- No schema migration, API change, UI work, or runtime deployment yet.
- No automatic inference of parent/child relationships from free-form titles.
