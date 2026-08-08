# OpenCode + Agent Herder Notify transport

This bundle keeps the demo path versioned without committing credentials:

`GPTAdmin webhook → Agent Herder → OpenCode → Notify MCP shim → Notify HTTP → adapter`

The OpenCode process never receives ADB or phone-adapter credentials. Only the
central Notification Center owns delivery adapters.

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
4. Merge [`opencode.notify.jsonc`](opencode.notify.jsonc) into the existing
   OpenCode config; do not replace the operator's full config.

The snippets are intentionally secret-free. Roll back by reverting the commit
and removing only these two drop-ins; do not delete the user's credential
files.
