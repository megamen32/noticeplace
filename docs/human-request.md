# Notify in the Human Request stack

This note is a boundary document, not an implementation claim. The current Notify behavior below is code-backed; the HumanRequest section is proposed architecture only.

## Current proven behavior

- Notify is the durable operator-attention layer: it persists incidents, delivery attempts, acknowledgements, resolutions, snoozes, and cancellation in SQLite.
- Notify accepts `notify.event.v1`, enforces project-scoped bearer tokens, a maximum allowed severity per token, stable `Idempotency-Key` handling, and returns durable `event_id` plus `incident_id`.
- Incidents are deduplicated by `project + recipient + dedup_key`, while delivery work is tracked separately with stable delivery keys and retry-safe claims.
- Severity routing is centralized in Notify. Telegram destinations are selected from allowlisted severity routes rather than from untrusted event routing data.
- Operator controls are intentionally narrow. Exact critical incidents can expose acknowledgement and snooze controls; acknowledgement and resolution cancel queued or claimed future delivery.
- Agent-job follow-up is also bounded. When an allowlisted `agent_job` is present, Notify passes safe incident telemetry only and rejects authority-bearing fields such as commands, URLs, credentials, tokens, secrets, prompts, or callback URLs.

## Proposed HumanRequest boundary

- HumanRequest should own the upstream request lifecycle and correlation. `request_id` should be the stable cross-system identifier for one human request.
- Notify should receive a sanitized HumanRequest event envelope that already carries `request_id` as correlation metadata, while Notify continues to create and manage its own `event_id`, `incident_id`, and `delivery_id`.
- The boundary should stay event-shaped, not command-shaped: HumanRequest expresses what happened and what attention is needed, while Notify decides how to route, persist, escalate, acknowledge, and cancel within its own contract.
- HumanRequest-specific call policy, Ask User / Ask Secret lifecycle rules, and
  future human-in-the-loop branching remain proposed here only. Notify itself
  does implement bounded optional call escalation through configured Matrix,
  Android Telegram, and Android phone adapters; its delivery guarantee is
  at-least-once rather than exactly-once.

## What Notify must not receive

- secret plaintext or raw credentials
- arbitrary agent commands, shell snippets, prompts, tool calls, or executable instructions
- unbounded operator text that has not already been sanitized into a safe event payload
- direct process-control intent that bypasses the allowed handoff boundaries below

## Required handoffs

- SSS: owns secret collection, secret-aware operator workflows, and any Ask Secret step. Notify may receive only an opaque reference, status, or summary.
- agent-resume: owns long-wait and return-to-chat workflow. Notify may notify the operator that attention is needed, but it does not own resume state.
- Agent Herder: owns allowlisted agent jobs and durable job execution receipts. Notify may schedule or record a bounded job reference, but it must not receive a free-form command or prompt.

## Practical rule

If the payload would let Notify act as a secret store, command runner, or general agent supervisor, the payload is too broad for this stack boundary.
