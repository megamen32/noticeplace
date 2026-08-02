# Notify MCP

**Languages:** English | [Русский](README.ru.md) | [中文](README.zh.md)

![Notify MCP sends Telegram pings from AI agents](docs/assets/readme-hero.png)

> Runtime baseline checked with Node.js 22.22.3 and npm 10.9.8.

**Ping the human when the agent needs attention.**

Notify MCP is now primarily a lightweight Telegram ping tool for AI agents: use it at the end of work or before asking the user a question. Long work and returning to the correct chat should be handled by `agent-resume`. Legacy process watchers are still available when you specifically need Telegram on process completion.

For long builds, migrations, test suites, backups, and deployments, prefer `agent-resume` for waiting/resume. Use `notify.send_message` as the human-facing final ping: “I finished, please check.”

## Quick start: install in Codex

If Codex is your MCP client, this single command adds Notify and starts it through the real GitHub package launcher:

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

Restart or reload Codex, then run `codex mcp list` to confirm the server is registered. For OpenCode, VS Code, or a local stdio process, use the client-specific examples below.

## Why agents use it

- **Human ping mode:** send a simple Telegram note at the end or before asking for user input.
- **No context responsibility:** `agent-resume` handles long waits and resume; Notify only nudges the human.
- **Telegram completion fallback:** legacy watchers can still send success/failure/log alerts when explicitly needed.
- **Self-describing MCP:** usage rules are embedded in `tools/list`; no skill is required for MCP-aware clients.
- **Simple final ping:** “I finished X, please check” stays one MCP call.

## 30-second MCP install

For MCP clients, the simplest install is `npx`:

```bash
npx -y github:megamen32/notify
```

That command starts the stdio MCP server. It bundles the Bash watcher and automatically sets `NOTIFY_BIN`, so the MCP server can run without a separate clone.

For Telegram notifications, create secrets for the user that runs the MCP client:

```bash
mkdir -p ~/.config/secrets
cat > ~/.config/secrets/notifier.env <<'ENV'
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_CHAT_ID=123456789
ENV
chmod 600 ~/.config/secrets/notifier.env
```

Want the raw CLI too?

```bash
npm exec -y --package github:megamen32/notify -- notify-install
# or download notify-mcp-v*.tar.gz from Releases
```

## Add to Codex

```bash
codex mcp add notify -- npx -y github:megamen32/notify
codex mcp list
```

Or edit `~/.codex/config.toml` / `.codex/config.toml`:

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/notify"]
```

## Add to OpenCode

Add this to `~/.config/opencode/opencode.jsonc` or your project `opencode.jsonc`:

```jsonc
{
  "mcp": {
    "notify": {
      "type": "local",
      "command": ["npx", "-y", "github:megamen32/notify"],
      "enabled": true
    }
  }
}
```

## Add to VS Code

Create `.vscode/mcp.json` in the workspace, or use **MCP: Open User Configuration**:

```json
{
  "servers": {
    "notify": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "github:megamen32/notify"]
    }
  }
}
```

Then run **MCP: List Servers** and start `notify`.

## What the agent sees

The MCP server itself teaches the model the rule:

```text
Use send_message as an extra Telegram ping at the end of work or before
asking the user a question. For long work, waiting, and returning to the
correct chat, use agent-resume.

run_and_notify / attach_pid / attach_query are legacy process-completion
watchers. Use them only when the human specifically needs Telegram when a
process exits.
```

## Downloadable release assets

Each GitHub release includes:

- `notify-mcp-vX.Y.Z.tar.gz` — portable bundle with `install.sh`
- `notify-mcp-vX.Y.Z.zip` — same bundle for zip-based workflows
- `notify-mcp-X.Y.Z.tgz` — npm package tarball
- `SHA256SUMS` — checksums

## Docs

- [Notify Center producer SDKs](docs/producer-sdk.md) — small Python and Node.js
  clients for scoped operational events and optional acknowledgement waiting.

- [MCP server](docs/mcp.md) — tools, schemas, agent behavior, client configs.
- [CLI tool](docs/cli.md) — `/usr/local/bin/notify` usage, Telegram secrets, logs.
- [AI skill](docs/skill.md) — optional fallback for clients that do not expose MCP tool descriptions well.

## Do I still need the skill?

Usually **no**. For Codex, OpenCode, VS Code, Claude, and other MCP-aware clients, the important instructions are now embedded directly in the MCP tool descriptions. Keep the skill only as a fallback for runtimes that do not surface MCP schema descriptions reliably.

## License

MIT


### AI usage rule

`notify` is now a human ping, not the main long-job control plane.

Use `agent-resume` for long work, waiting, and returning to the correct agent chat. Use `notify.send_message` only as an extra Telegram nudge:

- at the end: “I finished rebuilding X, please check”;
- before asking the user a question: “I need your input on Y, please look”;
- when a human-visible milestone matters.

Do not use `notify` as the default mechanism for long waiting/resume. That job belongs to `agent-resume`.

### MCP: send a plain message

`notify-mcp` includes `send_message` for simple human-facing status notes. Use it as a final/prequestion Telegram ping, without watching a process:

```json
{
  "message": "I finished rebuilding the index, please check the result.",
  "title": "GPTAdmin"
}
```

This is separate from `agent-resume`, which is the preferred mechanism for long work and resuming the correct chat. `run_and_notify`/`attach_pid` remain legacy process-completion helpers.
