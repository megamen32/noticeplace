# NoticePlace Python client

Dependency-free producer client for a NoticePlace deployment. See
[`docs/producer-sdk.md`](../docs/producer-sdk.md) in the repository for usage
and the event contract.

## Typed health and source verification

`NotificationCenterClient.emit` accepts `event_type="health.degraded"` with
`source_id`, `host_id`, `signal_type`, `problem_type`, `correlation_id` and a
positive epoch `observed_at`. An optional `source_context` mapping carries bounded
producer evidence (schema `notify.source-context.v1`); the server validates and
sanitizes it. Persist an explicit idempotency key before the
call; reconcile an uncertain outcome instead of repeating it. Existing untyped
`emit` calls remain supported.

`from notify_center_client import SourceVerifier, VerificationPending` exposes
the same verifier used by `notification_center.consumer_lifecycle`. Call
`tick(probe)` on the existing source timer before reporting another observation.
The probe must take a fresh measurement and return `observed_state` plus
`evidence_refs`; an unknown result never means healthy. Optional `accept_group`
selects an owned episode before probing or sending a result. Optional
`max_checks` and `deadline_seconds` bound admission of further checks; each
transport and probe still needs its own timeout. Unprocessed due checks remain
on the server. The verifier follows scoped cursor pages, with at most 100 pages
per tick, respecting `max_checks` and the deadline before starting more work.
A pending unknown POST is reconciled from its exact receipt, never re-sent.
The paginated server supports a wait of up to five seconds without holding its
lock. The HTTP pagination endpoint must be deployed before this is available.

The verifier source file is linked to the canonical module in the repository;
the built wheel contains its own regular copy and is independently installable.
Source and executor principals must remain distinct; source updates must use
the incident's bound source principal and a valid verification grant.
