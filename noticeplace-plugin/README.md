# Notice Place Plugin

Portable [Agent Plugins 1.0](https://agent-plugins.org/specification) package for
repair-first incident work: report bounded progress, and ask the operator one
question whose answer returns to the same native session.

## Layout

The canonical package directory follows the Agent Harness Fleet layout:

```text
noticeplace-plugin/packages/noticeplace/0.1.2/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.2/mcp.json
noticeplace-plugin/packages/noticeplace/0.1.2/skills/noticeplace/SKILL.md
noticeplace-plugin/packages/noticeplace/0.1.2/.codex-plugin/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.2/.zcode-plugin/plugin.json
noticeplace-plugin/.agents/plugins/marketplace.json
noticeplace-plugin/.claude-plugin/marketplace.json
```

## Tools

| Tool | Purpose |
| --- | --- |
| `noticeplace_pending_questions` | Read-only list of this project's open questions (opaque references only). |
| `noticeplace_answer` | Record one human answer; the same Telegram card gets the accepted-option checkmark. |
| `noticeplace_ask_human` | Create one card from a Herder native binding; map `choice_id` to `value` and pass the binding's CWD. |
| `noticeplace_question_result` | Read the canonical answer in the resumed session. |
| `noticeplace_incident_progress` | Bounded executor observation; independent verification closes the incident separately. |
| `ask_human` | Legacy unbound notice or blocking question; it resumes no native session. |

## Install

Point an Agent Plugins 1.0-compatible client at:

```text
https://github.com/megamen32/noticeplace/tree/main/noticeplace-plugin
```

In Agent Harness Fleet: **Plugins + Marketplace → Import → Publish → choose the
target harness → Preview → Apply**. Native Codex and ZCode marketplaces are
included at the paths above and point to the same versioned package. Native CLI
installation and consumer acceptance belong to the release owner.

Portable clients and the native projections all reach the same shared Notice
Place MCP over Streamable HTTP at `https://notify.bezrabotnyi.com/mcp`. No
client starts a Notice Place server of its own, and the bundle contains no
secret values:

* Hermes reads `headers.Authorization: Bearer ${NOTIFY_MCP_TOKEN}` at
  connection time through the active profile secret scope; the portable manifest
  uses only the official schema fields. See [Hermes installation and proof](HERMES-INSTALL.md);
* Codex and ZCode read the bearer from the environment
  (`bearer_token_env_var: NOTIFY_MCP_TOKEN`), so the package only names the
  reference;
* OpenCode and Fast Agent cannot resolve a bearer from the environment, so
  their registration is written by the GPTAdmin installer
  (`gptadmin notice-place install`) into the client config, and the package
  keeps the skill only.

Validate it with:

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.2/scripts/validate_plugin.py
zcode plugins validate noticeplace-plugin/packages/noticeplace/0.1.2 --json
```

## Source

Maintained in [`megamen32/noticeplace`](https://github.com/megamen32/noticeplace).


## Hermes 0.1.2

The official loader accepts the shared HTTP endpoint and an Authorization header
reference. It rejects the old 0.1.1 portable manifest's `$comment` and
`bearer_token_env_var`. Version0.1.2 fixes those fields;0.1.0 and0.1.1 remain
unchanged. Codex/ZCode retain their native bearer environment reference.

Follow [HERMES-INSTALL.md](HERMES-INSTALL.md) for exact pinned installation,
credential prerequisites, native-config precedence and isolated transport proof.
A plugin install does not override an existing native `mcp_servers.noticeplace`
entry. No local Notice Place server, stdio wrapper or Hermes core patch is used.
