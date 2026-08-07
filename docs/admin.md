# Notify Center operator console

`/admin/` is the protected operator surface, not a producer API. It uses the
existing `auth.bezrabotnyi.com` login flow through nginx and keeps both the
admin service and the Center bound to loopback.

## What it manages

- one producer token per project;
- maximum allowed severity for that project;
- Telegram forum routing uses an explicit active-mode set. The current deploy
  activates only `emergency`, `important`, and `log`; inactive catalog modes do
  not create topics or deliver to Telegram.
- On startup, the bot can create missing active forum topics (`Emergency`,
  `Important`, `Log`) and persist their thread IDs without deleting old topics.

When a project is created, its token is displayed exactly once. Copy it into
that project's secret store. The console subsequently displays only a short
SHA-256 fingerprint, never the token itself.

## What it intentionally does not manage

Telegram/Matrix/Android credentials, health tokens, phone targets and delivery
adapter configuration stay in server-owned configuration. Producer code must
continue to use its project-scoped token and the documented producer API.

## Safety model

Nginx authenticates `/admin/` with an internal cookie-check subrequest and
overwrites `X-Notify-Admin` before proxying to the loopback-only admin service.
The service rejects requests without that header, requires a short-lived CSRF
form token for every mutation, writes configuration atomically, restarts the
main Center, and restores the prior file if that restart fails. Every accepted
mutation appends a root-only audit record without a raw producer token.
