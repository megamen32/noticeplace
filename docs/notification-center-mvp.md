# Notification Center MVP

This is a low-RAM service beside the existing Notify MCP process watcher. It
uses Python's standard library and SQLite in WAL mode.

## Current HTTP surface

- `POST /v1/events`: bearer-authenticated `notify.event.v1` intake;
- `GET /v1/incidents/{incident_id}`: project-token incident read;
- `POST /v1/incidents/{id}/ack|resolve|snooze`: project-token incident
  actions;
- `GET /health`: dedicated health-probe Bearer token and secret-safe
  `notify.health.v1` JSON;
- `POST /mcp`: bearer-authenticated HTTP JSON-RPC MCP transport using the
  dedicated MCP token;
- `GET /`: `303` redirect to `/admin/`. The public `/admin/` route is protected
  by nginx SSO; it is documented in [admin.md](admin.md).

`POST /v1/events` returns `202 Accepted` after durable storage. A stable
`Idempotency-Key` makes an exact retry safe; reusing it for changed data returns
`409`. A distinct key with the same active `dedup_key` adds an occurrence to
the existing incident.

## Delivery, calls, and health

The SQLite outbox tracks delivery attempts, acknowledgements, resolutions,
snoozes, and cancellation. Delivery is at-least-once: stable delivery keys
prevent routine duplicate scheduling, but a worker crash after an adapter
accepts a request and before Notify records success can expire the bounded
claim lease and cause a duplicate external send. A successful adapter response
proves acceptance only, not that a carrier connected or a person answered.

Matrix call escalation is an optional real adapter path. When configured, the
worker schedules Matrix, Android Telegram, and Android phone calls according to
live runtime timers and only while the incident remains deliverable. An
unconfigured or failed adapter is a retryable delivery failure. Disabling
automatic calls cancels queued/claimed call deliveries but cannot interrupt a
call already in progress.

The operator console migrates missing timing values from startup environment
variables into SQLite as defaults. Later timer changes are read by the running
worker and apply to newly scheduled/retried work without a restart. Telegram
topic create/edit/delete is also stored in the live runtime registry: seeded
and custom topics share the same behavior, and deleting one removes Notify
routing without deleting Telegram history.

The consumer form is a visual adapter builder for generic linked steps:
`platform`, `action`, `target`, retry interval, repeat limit, and optional
`previous_step_id`. Generic policies require one root and valid predecessor
references. Server-side validation remains authoritative.

The health endpoint returns `200` only when SQLite storage and the dispatcher
heartbeat are ready. It requires a dedicated probe credential; do not reuse a
producer token:

```bash
curl --fail-with-body https://notify.example/health \
  -H 'Authorization: Bearer HEALTH_PROBE_TOKEN'
```

```json
{
  "schema": "notify.health.v1",
  "service": "notification-center",
  "status": "ok",
  "storage_ready": true,
  "dispatcher_ready": true
}
```

Missing, malformed, producer, and incorrect Bearer credentials receive the
same safe `401` response. Dependency failures receive `503` with
`status: "degraded"` and boolean readiness fields; exception messages, paths,
tokens, and configuration are never returned.

## vpn2 boundary

Install the primary service on the primary host and only the independent
watchdog service/timer on `vpn2`. The watchdog uses the public HTTPS health
route and rejects redirects, HTML, LAN endpoints, and bare TCP listeners as
proof of health. It has its own state directory and direct alert credentials;
it never imports the center, uses its SQLite database, or posts to
`/v1/events`.

Deployment examples and the watchdog gate are in [`deploy/`](../deploy/).
