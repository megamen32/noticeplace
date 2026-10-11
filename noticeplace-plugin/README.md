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
```

## Tools

| Tool | Purpose |
| --- | --- |
| `noticeplace_pending_questions` | Read-only list of this project's open questions (opaque references only). |
| `noticeplace_answer` | Record one human answer; the same Telegram card gets the accepted-option checkmark. |
| `noticeplace_incident_progress` | Bounded progress receipt, plus an optional independent verification. |
| `ask_human` | Create the question. With `wait_seconds: 0` it returns immediately and the answer continues the same session. |

## Install

Point an Agent Plugins 1.0-compatible client at:

```text
https://github.com/megamen32/noticeplace/tree/main/noticeplace-plugin
```

In Agent Harness Fleet: **Plugins + Marketplace → Import → Publish → choose the
target harness → Preview → Apply**. The package declares no credentials; the
client holds the bearer token for `https://notify.bezrabotnyi.com/mcp`.

## Source

Maintained in [`megamen32/noticeplace`](https://github.com/megamen32/noticeplace).