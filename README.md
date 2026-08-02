# Notify

[Русский](README.ru.md) · [中文](README.zh.md) · [GitHub](https://github.com/megamen32/notify) · [Docs](docs/)

> Send a human ping from an agent, or publish a durable operational event to
> Notify Center without giving every service delivery credentials.

- MCP notifications for agents.
- Project-scoped producer events with severity limits.
- Optional acknowledgement/resolution waiting for Python and Node.js services.
- Protected operator console for producer scopes and Telegram topic routes.
- Optional signed GPTAdmin agent jobs selected only from each producer token's
  allowlist, with durable idempotency and terminal result tracking.

## Install

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

## Production events

```bash
# Python
pip install 'git+https://github.com/megamen32/notify.git#subdirectory=python'

# Node.js
npm install github:megamen32/notify
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
- [CLI](docs/cli.md)
- [AI skill](docs/skill.md)

## License

[MIT](LICENSE)
