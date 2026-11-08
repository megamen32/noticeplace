# Hermes: install 0.1.2 through the official HTTP client

Source-only handoff, 2026-11-08. No installed plugin, profile config, secret,
Hermes core, running session or production MCP was changed by this fix.
The new version and native catalogs are uncommitted and unpublished; do not pin
the old repository HEAD as though it already contains0.1.2.

## Verified contract

Official source: `/home/roomhacker/.hermes/hermes-agent`, commit
`71d34bad1045610252eda02ee79549436896e784`.

- `hermes_cli/agent_plugins.py:413`: portable top-level keys must be exactly
  `$schema,mcpServers`. `$comment` rejects the entire MCP component.
- `agent_plugins.py:345`: remote keys are `type,url,headers`.
  `bearer_token_env_var` rejects the server, even with `$comment` removed.
- `agent_plugins.py:355`: headers are copied literally; loader success alone
  does **not** prove authentication or environment expansion.
- `tools/mcp_tool_config.py:448`: `${VAR}` resolves through
  `agent.secret_scope.get_secret`. Portable config is merged after native
  config interpolation (:528–564), so it may still contain placeholders then.
- `tools/mcp_tool_discovery.py:161`: the official connection path interpolates
  again under the connection owner's fresh profile scope and requires resolved
  URL/headers. Missing credentials fail before HTTP, with `missing_credentials`.
- `tools/mcp_tool_transport.py:551`: the actual Streamable HTTP transport gets
  resolved headers and enforces the loader's `strict_redirect_headers=true`.

Therefore portable auth **is supported on this revision**.0.1.2 uses:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "NoticePlace": {
      "type": "streamable-http",
      "url": "https://notify.bezrabotnyi.com/mcp",
      "headers": {"Authorization": "Bearer ${NOTIFY_MCP_TOKEN}"}
    }
  }
}
```

The package contains a reference, never a token value. It does not load the
historical `ASK_HUMAN_ENV_FILE`, mint credentials or start a server. The profile's
existing protected secret source, `.env` or launch environment must supply
`NOTIFY_MCP_TOKEN`; a variable set only in an unrelated interactive shell is not
proof for a running gateway/profile. Use the existing credential provisioning
route, without copying a token into the package, command line or logs.

On an older Hermes revision without connect-time portable interpolation, do not
assume literal headers will authenticate. Use that version's documented native
`mcp_servers`/secret-provider configuration for the same HTTP endpoint, with a
fake-credential transport proof first; or upgrade through its official release
path. Never patch Hermes core or fall back to stdio/local Notice Place.

## Current installation boundary

Read-only snapshot (`evidence/20261108-hermes/runtime-config-readonly.json`):
installed plugin `~/.hermes/plugins/noticeplace` is0.1.1 and enabled. Native
`mcp_servers.noticeplace` and `mcp_servers.AskHuman` both reference the shared HTTP
endpoint and have authorization headers. Their values were not recorded.
Neither inspected header uses `${NOTIFY_MCP_TOKEN}`.

Native `noticeplace` wins over the plugin's normalized `noticeplace` registration
(`tools/mcp_tool_config.py:528`). Installing0.1.2 fixes the package but does not
switch that connection to portable auth. Keep current native config unchanged
for this release unless its owner separately approves a config cutover. Do not
remove `AskHuman` simply because the URL matches: its alias/tool consumers need
separate readback. Report a successful connection using a retained native entry
as **native-config HTTP acceptance**, not portable-plugin authentication proof.

If the release owner later chooses portable ownership, first verify the real
profile can resolve `NOTIFY_MCP_TOKEN`, preserve a protected config backup, and
remove only the reviewed native `noticeplace` entry through supported config
management. Reload and verify the plugin-owned registration. That config cutover
was neither performed nor included in the source fix.

## Installation commands for the release owner

1. Run the scoped checks from the implementation worktree:

```bash
cd /home/roomhacker/agents-projects/.worktrees/noticeplace-quiet-implementation-20261107
PYTHONDONTWRITEBYTECODE=1 python3 noticeplace-plugin/packages/noticeplace/0.1.2/scripts/validate_plugin.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_noticeplace_plugin_hermes.py -v
```

The integration test obtains the **already installed** interpreter via
`pm.environments.selected_venv/venv_command` (read-only selection, no `ensure` or
installation). This host's old `.venv/bin/python` lacks the MCP HTTP dependency
and is not the CLI's selected environment. Do not alter it to make this test pass.

2. After the owner publishes the owned package and catalogs, set
`NOTICEPLACE_RELEASE_SHA` to that exact published40-character commit, then run:

```bash
: "${NOTICEPLACE_RELEASE_SHA:?Set the published commit containing package0.1.2}"
[[ "$NOTICEPLACE_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
hermes plugins install \
  'https://github.com/megamen32/noticeplace.git#noticeplace-plugin/packages/noticeplace/0.1.2' \
  --ref "$NOTICEPLACE_RELEASE_SHA" --force --enable --yes-deps
hermes plugins info noticeplace
```

The source argument and flags are supported by current
`hermes_cli/plugins_cmd_install.py:532` and `hermes_cli/subcommands/plugins.py`.
No source publication or installation command was run by this worker. The real
commit cannot be supplied before the release owner publishes it.

3. In a new/reloaded Hermes session inspect the normalized registration and
actual owning config/plugin, then discover tools and call only
`noticeplace_instructions`. Verify version0.1.2, the shared URL, no loader
MCP diagnostics and the expected tools. Do not dump rendered headers/config to
chat. Do not call ask-human/progress/answer as a probe, recreate a question or
restart an active repair. Production authorization and native session binding
remain release acceptance; isolated tests do not claim them.

## RED / GREEN and immutable versions

`evidence/20261108-hermes/red.txt`: the current0.1.1 manifest fails the official
loader;6 failures/7 tests before package edits. The existing Lead evidence
`primary/.agents/review-artifacts/20261108-integrated/runtime-contracts-red.txt`
also contains the original failure among unrelated runtime-contract failures.

`green.txt`:9 tests passed. Tests exercise both independent schema rejection
causes,0.1.2 official loader, a package path containing spaces, unchanged native
HTTP projections/skill, version/catalog/validator, and native config precedence.

The network proof uses the real official `_connect_server`, `MCPServerTask` and
installed MCP SDK. Only the HTTP network transport is replaced by in-process
MockTransport; HTTP content-type preflight is bypassed and config-refresh is
held at the fixture. A fake environment token reaches the actual client request
headers; initialize → tools/list → tools/call(`noticeplace_instructions`) succeeds
against synthetic responses. No production URL is contacted and no fake token
leaves the process. Missing env yields zero requests and `missing_credentials`.
The test profile is temporary and subprocess environment excludes real secrets.
This does not prove real service availability or authorization.

`historical-hashes.json` and `manifest.json` retain/check every0.1.0/0.1.1 file
byte; neither historical version was rewritten. Native catalogs now select0.1.2.
`legacy-venv-probe.txt` records the test-environment mismatch before selecting
the actual PM runtime; it is deliberately not labelled GREEN.
