# Notice Place Plugin

Portable [Agent Plugins 1.0](https://agent-plugins.org/specification) package for
repair-first incident work: report bounded progress, and ask the operator one
question whose answer returns to the same native session.

## Layout

The canonical package directory follows the Agent Harness Fleet layout:

```text
noticeplace-plugin/packages/noticeplace/0.1.0/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.0/mcp.json
noticeplace-plugin/packages/noticeplace/0.1.0/skills/noticeplace/SKILL.md
noticeplace-plugin/packages/noticeplace/0.1.0/.codex-plugin/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.0/.zcode-plugin/plugin.json
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

Every projection invokes `/opt/noticeplace/bin/ask-human-mcp`, using the existing
protected `ASK_HUMAN_ENV_FILE` path. The bundle contains no secret values and
requires no manual skill copies. Validate it with:

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.0/scripts/validate_plugin.py
zcode plugins validate noticeplace-plugin/packages/noticeplace/0.1.0 --json
```

## Source

Maintained in [`megamen32/noticeplace`](https://github.com/megamen32/noticeplace).
