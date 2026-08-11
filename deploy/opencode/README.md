# OpenCode + Agent Herder Notify transport

This bundle keeps the demo path versioned without committing credentials:

`GPTAdmin webhook → Agent Herder HTTP → harness stdio shim → harness`

Agent Herder is one singleton HTTP control plane. Every harness keeps the
`agent-herder` MCP capability, but its local stdio process is only the small
[`http-mcp-stdio.js`](../../../agent-herder/src/http-mcp-stdio.ts) bridge; it
does not start another Herder or another adapter set. The same bridge is used
by OpenCode, Codex, and Hermes.

The OpenCode process never receives ADB or phone-adapter credentials. Only the
central NoticePlace owns delivery adapters.

## Install

1. Create `~/.config/notify/opencode-mcp.env` from
   [`opencode-mcp.env.example`](opencode-mcp.env.example), set the central
   `NOTIFY_MCP_TOKEN`, and keep the file owned by the user with mode `0600`.
2. Install the OpenCode drop-in and reload the user unit:

   ```bash
   install -Dm644 opencode.service.d.conf \
     ~/.config/systemd/user/opencode.service.d/notify-mcp.conf
   systemctl --user daemon-reload
   ```

3. Create `~/.config/agent-herder/opencode.env` with the local OpenCode
   server password and mode `0600`, then install
   `agent-herder.service.d.conf` into the Agent Herder user unit drop-in.
4. Merge [`opencode.notify.jsonc`](opencode.notify.jsonc) and
   [`agent-herder-http.jsonc`](agent-herder-http.jsonc) into the existing
   OpenCode config; do not replace the operator's full config.

The snippets are intentionally secret-free. Roll back by reverting the commit
and removing only these two drop-ins; do not delete the user's credential
files.

The Agent Herder choice callback is stateful. Keep the
`AGENT_HERDER_AUTOPILOT_STATE_DIR` line from the drop-in: the Codex Stop hook
and the HTTP callback endpoint must read the same `choices.json`, otherwise
Telegram buttons can be delivered successfully but their callbacks cannot
find the pending request.
