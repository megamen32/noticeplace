# Repair-first handoff for root

Scope of this branch (`codex/noticeplace-repair-first`, base `origin/main` = `e42bf57`):
NoticePlace-side repair-first incident contract, one canonical selected-card
updater, the Agent Plugin 1.0 package, and the MCP fixes the phone consumer
needs. Deployment, phone canary, and the release remain root's.

## 1. What now happens on a health incident

1. `POST /v1/events` with `kind='incident'`, `event_type='health.degraded'` and
   `source_id/host_id/signal_type/correlation_id` admits the incident.
2. Exactly **one** session is admitted through
   `POST /api/automation/project-launch`; Agent Herder picks the harness and
   model for the incident's project, and Notice Place arms the per-session
   autopilot (`arm_session_autopilot`) immediately, also for the diagnosis
   stage.
3. That session repairs first. No planner session, no mandatory JSON menu, and no
   three-plan card in the standard path.
4. Its terminal receipt is an **observation**: the step it performed, the
   evidence it read, and the identity plus fingerprint of the original source it
   touched. Notice Place posts it as progress only
   (`POST /v1/incidents/{id}/health/progress`, plan `repair`, actor
   `agent-herder`). The executor never posts a verification and never resolves
   the incident from its own message.
5. A healthy observation leaves the incident **open** with
   `awaiting_verification: true` and `verification_required: {source_id,
   source_fingerprint, plan_id}`.
6. An independent producer closes it through the existing verification seam:

```http
POST /v1/incidents/{incident_id}/health/verification
Authorization: Bearer <project token>
Idempotency-Key: <stable per probe>
{"source_id": "<original source_id>", "verification_id": "<probe id>",
 "observed_state": "healthy", "fingerprint": "<original source_fingerprint>",
 "evidence_refs": ["<probe evidence>"], "actor": "<probe identity>"}
```

   Notice Place resolves the repair-first incident only when that receipt is
   independent (`actor` is neither `agent-herder` nor `health-remediation`,
   `verifier_id != source_id`), matches the original source and fingerprint, and
   useful progress exists for the executor plan. Any other combination leaves
   the incident open. No second executor and no planner is involved.
## 2. Launch: one Agent Herder admission call

Notice Place no longer picks a harness and no longer loops over fallbacks. For
every health stage it makes exactly one call:

```http
POST /api/automation/project-launch
{"project": "<incident.project or workspace root>", "cwd": "...",
 "message": "...", "harness": "<declared preference>", "model": "<declared preference>",
 "mode": "queue", "operation_id": "<stable sha256[0:32] of incident:stage:plan>"}
```

The response is consumed as the authoritative receipt:
`outcome`, `selected_harness`, `model`, `mode`, `session_id`, `delivery`,
`routing_reason`, `attempts[]`, and the echoed `operation_id`.

Behaviour in Notice Place:

| `outcome` | Notice Place |
| --- | --- |
| `accepted` | one executor session; autopilot armed; receipt published |
| `handoff_required` | the returned working session is recorded as the native binding |
| `admission_unknown`, `human_hold` | terminal for this `operation_id`; no second executor, no local retry |
| `pre_admission_rejected` | terminal here; retrying is Agent Herder's job, inside its own attempt list |
| `no_harness` | hard failure naming the project |
| anything else | hard failure; an unknown outcome is never guessed |

The stable `operation_id` is derived from `incident id + stage + plan`, so a
worker replay after a crash re-reads the same durable log instead of starting a
second session.

Project routes are read from `GET /api/automation/launch-policy`
(`projectRoutes: {path: {harnesses[], models?{}, aliases[]}}`). Matching is
case-insensitive and accepts the card project id, the canonical name, an alias,
or the absolute workspace root. Notice Place matches exactly what Agent Herder
matches and never guesses a route from a similar component name; an undeclared
project keeps the pinned OpenCode/minimax route. AutoSellBot-style names that
nobody declared stay on that default.

## 3. One question, one card, one native resume

1. Agent Herder `human_request_create` (or `POST /internal/human-requests`)
   binds the native session and returns `binding`.
2. `noticeplace_ask_human(binding.request_id, binding.native_target.*, message, choices)`
   performs exactly one `POST /v1/human-requests` and renders exactly one card.
   It never polls; a replay returns the same row with `idempotent: true` and no
   second card.
3. The answer (Telegram, voice, phone, API) is recorded canonically, the same
   card is marked, and Notice Place calls Agent Herder's existing
   `POST /internal/human-requests/ask-user-completion` once with an opaque
   derived `result_ref`. Herder's `claimResume` is single-winner.
4. The resumed session calls `noticeplace_question_result(request_id)`, which
   returns the canonical answer from storage plus its opaque `result_ref`; a
   secret request returns only the reference.

A metadata-only Herder request (no message) creates no card: `message` is
required by `noticeplace_ask_human`, and `choices` without a message is not a
question.

## 4. MCP surface

| Tool | Purpose |
| --- | --- |
| `noticeplace_ask_human` | One bound question and one card from a Herder binding. |
| `noticeplace_pending_questions` | Bounded question text, options, incident, exact native routing. Never returns a stored answer, document, or secret. |
| `noticeplace_answer` | Records an answer for a question or an incident choice card; returns the canonical value and the real card outcome. |
| `noticeplace_question_result` | The canonical answer plus its opaque reference, for the resumed session. |
| `noticeplace_incident_progress` | Bounded progress and independent verification with a stable Idempotency-Key. |
| `ask_human` | Unchanged legacy notification/blocking question; binds nothing. |

HTTP additions:

| Call | Purpose |
| --- | --- |
| `POST /v1/human-requests` | Create the bound question (`native_target` = harness/session_id/cwd). |
| `GET /v1/human-requests/{request_id}/result` | Canonical answer receipt for a resumed session. |
| `POST /v1/human-requests/{request_id}/resolve` | Record an answer; mark the card; resume the bound native session once. |
| `POST /v1/incidents/{incident_id}/choice-result` | Canonical choice for an incident choice card. |
| `POST /v1/incidents/{incident_id}/health/progress` | Bounded progress receipt. |
| `POST /v1/incidents/{incident_id}/health/verification` | Independent verification receipt. |

MCP protocol negotiation echoes the client's requested handshake revision and
never answers `2026-07-28`, which is a stateless per-request envelope this
server does not implement.

## 5. Exact install

```text
noticeplace-plugin/packages/noticeplace/0.1.0/{plugin.json,mcp.json,skills/noticeplace/SKILL.md}
```

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.0/scripts/validate_plugin.py
# PASS noticeplace 0.1.0
```

Agent Harness Fleet: **Plugins + Marketplace → Import → Publish → target
harness → Preview → Apply**. The package declares no credentials. The MCP
server is the existing `https://notify.bezrabotnyi.com/mcp` (streamable-http);
the client holds the bearer token, for Fast Agent:

```yaml
mcp:
  servers:
    NoticePlace:
      transport: http
      url: https://notify.bezrabotnyi.com/mcp
      auth:
        oauth: false
      access_token: <project token, kept in fast-agent.secrets.yaml>
```

Keep the existing authenticated AskHuman MCP entry on the phone; this package
is a separate, currently authless component that only becomes runtime tools
after that native installation projection.

## 6. Tests

`tests/test_ready_mcp_binding.py` runs the real Notice Place HTTP stack and the
real MCP tool handlers against the two network peers (Telegram and Agent Herder)
replaced by local servers. It proves the agreed chain: one Herder binding, one
`noticeplace_ask_human` call, exactly one card, a replay that creates no second
card, `noticeplace_pending_questions` with the exact native routing, a voice
answer that marks that card, exactly one Herder completion callback, and
`noticeplace_question_result` returning the canonical answer and reference.

`tests/test_repair_first_incident.py` covers the executor contract: verified
repair without a planner or plan card, a rejected claim that resolves nothing, a
pending question that keeps one session, per-project route selection and aliases,
the admission receipt being authoritative, canonical answer over a conflicting
repeat, the shared card marker, and the MCP handshake plus tools list.

The four remaining suite failures (`test_http_api` remote-MCP trio and
`test_notification_center_clients`) fail identically on base `e42bf57` and are
not caused by this branch.