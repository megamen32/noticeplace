# Notification Center MVP

This is a new low-RAM service beside the existing Notify MCP process watcher.
It uses only Python's standard library and SQLite in WAL mode. The MCP API is
unchanged.

## Current scope

- `POST /v1/events`: bearer-authenticated `notify.event.v1` intake.
- Request idempotency and active-incident collapse by project, recipient, and
  `dedup_key`.
- Durable SQLite outbox with a Telegram adapter and safe transport retries.
- Explicit `ack`, `resolve`, and `snooze` actions. A read receipt is not ACK.
- Bearer-authenticated, secret-safe `GET /health` contract for independent
  monitoring.
- A standalone `vpn2` watchdog which has its own state file and directly calls
  Telegram. It never imports the center, uses its SQLite database, or POSTs to
  `/v1/events`.

Matrix call escalation is deliberately an adapter boundary, not a fake feature:
the worker reports an unconfigured channel as a retryable delivery failure.
Before enabling it, a separate Matrix dialer must prove a real inbound answer,
translate that answer to an ACK, and terminate the call. Telegram button
callbacks and Matrix adapters are Tier-B work.

## Event request

```bash
curl --fail-with-body -X POST https://notify.example/v1/events \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Idempotency-Key: hermes-gateway-20260730-01' \
  -H 'Content-Type: application/json' \
  --data '{"schema":"notify.event.v1","project":"hermes","recipient":"me","kind":"incident","severity":"critical","title":"Hermes unavailable","body":"Three failed checks","dedup_key":"hermes:gateway:100","ack":{"required":true}}'
```

`202` creates an incident. Retrying the identical request key returns the same
identity. Reusing that key for different data returns `409`. A distinct key with
the same active `dedup_key` adds an occurrence to the existing incident.

## Health contract and vpn2 boundary

The externally published endpoint must return `200` only when the SQLite store
and dispatcher heartbeat are ready. It requires a dedicated probe credential;
do not reuse a producer token:

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

Set `NOTIFY_CENTER_HEALTH_TOKEN` on the primary service. Missing, malformed,
producer, and incorrect Bearer credentials receive the same safe `401` response.
Dependency failures receive `503` with `status: "degraded"` and boolean
readiness fields; exception messages, paths, tokens, and configuration are never
returned.

Install the service on the primary host and only the watchdog service/timer on
`vpn2`. Use a public HTTPS hostname for `PRIMARY_HEALTH_URL`; no LAN endpoint,
redirect, HTML page, or bare TCP listener counts as a healthy center. Give the
watchdog the matching dedicated health credential, its own direct Telegram and
Matrix failover credentials, and its own state directory. Its exact deployment files are in
[`deploy/`](../deploy/).

## Deployment gate

1. Put primary and vpn2 environment files in `/etc` with mode `0600`.
2. Create the dedicated system users and install the matching unit files.
3. Run `systemd-analyze verify` on both unit sets before enabling anything.
4. Confirm the publicly routed HTTPS health JSON from vpn2.
5. Demonstrate: two failures produce no alert, third failure produces exactly
   one direct watchdog alert, repeated failures do not flood, and two valid
   probes produce exactly one recovery alert.
6. Only after that point configure a real Matrix dialer and add its own
   integration tests.
