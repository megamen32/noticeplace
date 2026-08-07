# Notify MCP server

**Languages:** English | [Русский](mcp.ru.md) | [中文](mcp.zh.md)

Notify MCP is a stdio MCP server for non-interactive long-running shell jobs. It starts commands detached, writes logs to disk, attaches `/usr/local/bin/notify`, and returns without keeping the agent awake.

## Main tool: `run_and_notify`

Use this for commands that are expected to take more than 3 minutes and do not need stdin, prompts, a TUI, or a real terminal.

```json
{
  "command": "pytest -q",
  "cwd": "/home/me/project",
  "log_mode": "tail",
  "replace": true,
  "wait_seconds": 180,
  "hard_timeout": "30m",
  "note": "full test suite"
}
```

Behavior:

1. Creates `~/.local/state/notify-mcp/jobs/<job_id>/`.
2. Writes `runner.sh`, `job.log`, `meta.json`, and `status.json`.
3. Starts the command with `setsid` in the background.
4. Attaches `/usr/local/bin/notify` to the process PID.
5. Waits up to `wait_seconds`.
6. If still running, returns `alive=true` with `job_id`, `pid`, and `log_file`.

The agent should stop waiting when `alive=true`. Telegram will report completion or timeout.

## Built-in agent instructions

Notify MCP is self-describing. Its `tools/list` response includes the long-job policy in tool descriptions and JSON schema property descriptions. This is intentional: the model can learn the behavior from MCP metadata without a separate skill file.

The embedded rule is:

- For non-interactive jobs expected to take more than 3 minutes, prefer `run_and_notify` over blocking shell calls.
- Use `log_mode="tail"`, `replace=true`, `hard_timeout="30m"`, and `wait_seconds <= 180`.
- If the result has `alive=true` or `state="running"`, stop polling and report `job_id`, `pid`, and `log_file`.
- Use `job_status` or `job_tail` only for small bounded checks.
- Use `pty-mcp` for prompts, TUIs, editors, interactive installers, or keystrokes.

## Tools

### `run_and_notify`

Start a new detached non-interactive shell command and notify on completion.

Returns: `job_id`, `pid`, `cwd`, `log_file`, `status_file`, `runner`, `notify_attached`, `hard_timeout`, `wait_seconds`, `waited_seconds`, `alive`, and `status`.

### `attach_pid`

Attach Telegram notification to an already-running PID.

Use when a process was started outside Notify MCP but should notify on completion.

### `attach_query`

Find a process by command substring and attach notification. It fails on multiple matches unless `first=true`.

### `job_status`

Small metadata/status check for a known job.

### `job_tail`

Bounded log tail. Keep `max_bytes` small to avoid token waste.

### `list_jobs`

List recent jobs without dumping logs.

### `kill_job`

Send `TERM`, `INT`, or `KILL` to the job process group.

## Install in MCP clients

### Codex

Recommended npx install:

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
codex mcp list
```

Manual TOML:

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/noticeplace"]
```

Local Python install is also supported:

```toml
[mcp_servers.notify]
command = "/usr/bin/python3"
args = ["/home/YOU/.local/share/notify/mcp/notify_mcp.py"]
```

### OpenCode

```jsonc
{
  "mcp": {
    "notify": {
      "type": "local",
      "command": ["npx", "-y", "github:megamen32/noticeplace"],
      "enabled": true
    }
  }
}
```

### VS Code

`.vscode/mcp.json` or user `mcp.json`:

```json
{
  "servers": {
    "notify": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "github:megamen32/noticeplace"]
    }
  }
}
```

## Release assets

GitHub Releases include portable archives and npm package tarballs:

- `notify-mcp-vX.Y.Z.tar.gz`
- `notify-mcp-vX.Y.Z.zip`
- `notify-mcp-X.Y.Z.tgz`
- `SHA256SUMS`

The archive contains `install.sh`, `bin/notify`, `mcp/notify_mcp.py`, docs, and the npm wrapper.

## Environment

- `NOTIFY_BIN` — path to the CLI, default `/usr/local/bin/notify`.
- `NOTIFY_MCP_STATE_DIR` — state directory, default `~/.local/state/notify-mcp/jobs`.
- `MAX_TAIL_BYTES` — default maximum log tail bytes returned by `job_tail`.

## When not to use it

Use `pty-mcp` instead of Notify MCP when the command may ask questions, open an editor, need Ctrl+C/Enter input, render a TUI, or depend on terminal behavior.

## Client documentation references

- Codex config basics: https://developers.openai.com/codex/config-basic
- Codex config reference: https://developers.openai.com/codex/config-reference
- OpenCode MCP servers: https://opencode.ai/docs/mcp-servers/
- VS Code MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- VS Code MCP configuration: https://code.visualstudio.com/docs/agents/reference/mcp-configuration
