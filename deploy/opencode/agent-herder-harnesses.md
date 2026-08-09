# Agent Herder bridge for every harness

The full Agent Herder runs once as the HTTP service on
`http://127.0.0.1:18787`. Each harness keeps its own MCP entry and starts only
this stdio bridge:

```text
/usr/local/bin/node /home/roomhacker/agents-projects/agent-herder/dist/http-mcp-stdio.js
AGENT_HERDER_HTTP_URL=http://127.0.0.1:18787/mcp
```

## Codex

```toml
[mcp_servers.agent-herder]
command = "/usr/local/bin/node"
args = ["/home/roomhacker/agents-projects/agent-herder/dist/http-mcp-stdio.js"]

[mcp_servers.agent-herder.env]
AGENT_HERDER_HTTP_URL = "http://127.0.0.1:18787/mcp"
```

## Hermes

```yaml
mcp_servers:
  agent-herder:
    command: /usr/local/bin/node
    args:
      - /home/roomhacker/agents-projects/agent-herder/dist/http-mcp-stdio.js
    env:
      AGENT_HERDER_HTTP_URL: http://127.0.0.1:18787/mcp
    enabled: true
```

OpenCode uses the equivalent JSONC fragment in
[`agent-herder-http.jsonc`](agent-herder-http.jsonc). Do not point a harness
at `dist/index.js`: that command is reserved for the singleton systemd
service.
