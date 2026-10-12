# Notice Place Plugin

Portable [Agent Plugins 1.0](https://agent-plugins.org/specification) package for
repair-first incident work: report bounded progress, and ask the operator one
question whose answer returns to the same native session.

## Layout

The canonical package directory follows the Agent Harness Fleet layout:

```text
noticeplace-plugin/packages/noticeplace/0.1.1/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.1/mcp.json
noticeplace-plugin/packages/noticeplace/0.1.1/skills/noticeplace/SKILL.md
noticeplace-plugin/packages/noticeplace/0.1.1/.codex-plugin/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.1/.zcode-plugin/plugin.json
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

Portable clients resolve `./bin/ask-human-mcp` inside the versioned package; it forwards to the existing `/opt/noticeplace/bin/ask-human-mcp`. Native Codex/ZCode/OpenCode/FastAgent projections keep their original absolute runtime command, using the existing
protected `ASK_HUMAN_ENV_FILE` path. The bundle contains no secret values and
requires no manual skill copies. Validate it with:

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.1/scripts/validate_plugin.py
zcode plugins validate noticeplace-plugin/packages/noticeplace/0.1.1 --json
```

## Source

Maintained in [`megamen32/noticeplace`](https://github.com/megamen32/noticeplace).


Hermes supports the portable package directly. Replace the existing Notice plugin
using its official installer, pinned to the published source commit:

```bash
hermes plugins install 'https://github.com/megamen32/noticeplace.git#noticeplace-plugin/packages/noticeplace/0.1.1' --ref <published-main-SHA> --force --enable --yes-deps
```

Version `0.1.0` is unchanged and remains historical. `0.1.1` fixes command-token
conformance with [Agent Plugins 1.0 section7.2.1](https://agent-plugins.org/specification#721-discovery-and-configuration).
No manual MCP entry, copied token, project grant, runtime-core edit, question replay
or native repair restart is needed. Existing protected `ASK_HUMAN_ENV_FILE` stays
at its original path. Verify read-only `noticeplace_instructions` through Hermes'
registered MCP tool after installation.
