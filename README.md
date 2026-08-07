# NoticePlace

[Русский](README.ru.md) · [中文](README.zh.md) · [GitHub](https://github.com/megamen32/noticeplace) · [Docs](docs/)

![NoticePlace is a universal inbox for AI-to-human attention](assets/hero-universal-inbox.png)

> The universal inbox that lets AI reach you through any available channel.

AI does not stop working because it needs your answer. NoticePlace keeps the
work running, routes important attention to calls, chats, messages, or email,
and gives the human one place to receive and acknowledge it.

- One universal inbox for AI-to-human attention.
- Calls, chats, messages, email, and other configured adapters.
- AI can continue its work while NoticePlace escalates what matters.
- Project-scoped producer events with severity limits.
- Optional acknowledgement/resolution waiting for Python and Node.js services.
- Protected operator console for producer scopes and Telegram topic routes.
- Optional signed GPTAdmin agent jobs selected only from each producer token's
  allowlist, with durable idempotency and terminal result tracking.

The public center hostname has one deliberate entry behavior: `GET /` returns
`303 See Other` to `/admin/`. The admin UI is not the producer API; nginx first
checks the existing `auth.bezrabotnyi.com` session and only then proxies the
request to the loopback admin service. The API and health endpoints remain
Bearer-authenticated separately.

## HTTP surface

- `POST /v1/events` — project-scoped event intake (`202 Accepted`).
- `GET /v1/incidents/{incident_id}` — read an incident with its project token.
- `POST /v1/incidents/{incident_id}/ack|resolve|snooze` — explicit incident
  actions with that token.
- `GET /health` — dedicated health-probe Bearer token; returns
  `notify.health.v1` JSON or `503` when readiness is degraded.
- `POST /mcp` — HTTP JSON-RPC MCP transport with its own `NOTIFY_MCP_TOKEN`.
- `/admin/` — SSO/cookie-protected operator console; it is not a public API
  credential boundary.

Delivery is durable and at-least-once: stable keys prevent routine duplicate
scheduling, but a worker crash after claiming a delivery can cause a retry and
therefore a duplicate external send. A successful adapter response means the
adapter accepted the request, not that a carrier or human completed a call.

## Install

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
```

## Production events

```bash
# Python
pip install 'git+https://github.com/megamen32/noticeplace.git#subdirectory=python'

# Node.js
npm install github:megamen32/noticeplace
```

Create a scoped producer token in the operator console, store it in the
project's secret store, then follow the detailed guide. `202 Accepted` means
the event was stored; an optional wait ends only at `acknowledged` or
`resolved`.

## Learn more

- [Producer SDK: curl, Python, Node.js and systemd](docs/producer-sdk.md)
- [Operator console](docs/admin.md)
- [Allowlisted GPTAdmin agent jobs](docs/gptadmin-agent-jobs.md)
- [MCP server](docs/mcp.md)
- [Legacy process watcher CLI](https://github.com/megamen32/notify-cli)
- [AI skill](docs/skill.md)

## License

[MIT](LICENSE)
