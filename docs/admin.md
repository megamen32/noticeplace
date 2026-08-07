# NoticePlace operator console

`GET /` redirects to `/admin/`. `/admin/` is the protected operator surface,
not a producer API. Nginx uses the existing `auth.bezrabotnyi.com` login flow,
then overwrites `X-Notify-Admin: 1` before proxying to the loopback-only admin
service. The admin listener rejects requests without that trusted header.

The producer API (`/v1/events` and incident actions), health (`/health`), and
HTTP MCP (`/mcp`) have separate Bearer-token boundaries. Admin SSO does not
grant any of those tokens. See [the producer guide](producer-sdk.md) and the
[MCP guide](mcp.md) for client contracts.

## What it manages

- one producer token per project, with a maximum allowed severity;
- Telegram forum routing through the active mode set. The current deploy
  activates `emergency`, `important`, and `log`;
- Telegram topic CRUD through one live editor. Seeded and custom topics can
  both be created, renamed, enabled/disabled, or deleted. Delete removes a
  topic from Notify routing but does not delete Telegram history. A blank
  thread ID creates a Telegram forum topic only when auto-create is enabled;
  otherwise an existing positive thread ID is required;
- automatic call enablement and live timing values for Matrix calls, Android
  Telegram calls, Android phone calls, and critical Telegram repeats;
- scoped consumers through a visual adapter builder;
- per-consumer quiet hours. New and existing consumers currently default to
  `01:00–09:00 Europe/Moscow` for calls only: messages continue, while queued
  calls are deferred until 09:00. This is intentionally not a global quiet
  window; each consumer stores its own policy.

The consumer builder emits generic linked steps. Each step has a platform,
action, target, retry interval, repeat limit, and optional predecessor. Generic
policies require unique step IDs, exactly one root step, and valid
`previous_step_id` references; server-side validation remains authoritative.

When a project or consumer is created, its token is displayed exactly once.
Copy it into that project's secret store. The console subsequently displays
only a short SHA-256 fingerprint, never the token itself.

## Live settings and restart boundaries

The operator store reads timing values from SQLite. On migration, missing
values fall back to the corresponding startup environment values and are then
available as runtime defaults. Saving the settings writes SQLite and does not
restart Notify; the running worker reads the values for newly scheduled or
retried deliveries. An adapter call already in flight is unchanged.

Disabling automatic calls cancels queued/claimed call work but cannot interrupt
an in-flight call. Producer-scope and legacy environment route changes are a
different boundary: they are applied atomically and restart the main Center,
with rollback if that restart fails. Every accepted mutation appends a root-only
audit record without a raw producer token.

## Delivery and escalation semantics

Notify persists deliveries, claims them with a bounded lease, and retries
transport failures. Stable delivery keys prevent routine duplicate scheduling,
but a worker crash after an adapter accepts a request and before Notify records
success can cause lease reclaim and a duplicate external send. Delivery is
therefore at-least-once, not exactly-once.

Matrix, Android Telegram, and Android phone calls are optional real adapter
paths. An unconfigured or failed adapter is a retryable delivery failure. A
successful HTTP/GPTAdmin response proves adapter acceptance only, not that a
phone carrier connected or a person answered.

## What remains server-owned

Telegram/Matrix/Android credentials, health tokens, phone targets, and fixed
transport credentials stay in server-owned configuration. Producer code must
continue to use its project-scoped token and the documented producer API.
