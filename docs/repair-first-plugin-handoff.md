# Repair-first handoff for root

Scope of this branch (`codex/noticeplace-repair-first`, base `origin/main` = `e42bf57`):
NoticePlace-side repair-first incident contract, one canonical selected-card
updater, the Agent Plugin 1.0 package, and the MCP fixes the phone consumer
needs. Deployment, phone canary, and the release remain root's.

## 1. What now happens on a health incident

1. `POST /v1/events` with `kind='incident'`, `event_type='health.degraded'` and
   `source_id/host_id/signal_type/correlation_id` admits the incident.
2. Exactly **one** OpenCode / `minimax-coding-plan` / `MiniMax-M3.1-Flash-Preview`
   session is launched (`_incident_profile`) and its per-session Herder autopilot
   is armed immediately (`arm_session_autopilot`), also for the diagnosis stage.
3. That session repairs first. No planner session, no mandatory JSON menu, and no
   three-plan card in the standard path.
4. Its terminal receipt is posted through the existing health seams:
   `progress` → `verification` → `resolve`. The core applies the unchanged
   fail-closed gate: `source_id != verifier_id`, matching fingerprint, real
   `evidence_refs`, independent verification actor. A claim without independent
   proof is terminal, posts nothing, and resolves nothing.
5. A question needs **two** facts only the agent has:
   `agent-herder_human_request_create` creates the Herder binding (it delivers
   nothing by itself), then the new `noticeplace_ask_human` tool persists the
   Notice Place question with that `request_id` plus harness/`sessionId`/`cwd`,
   renders the card, and stores the native target. It returns immediately and
   never polls. On the answer, Notice Place calls Herder's existing
   `POST /internal/human-requests/ask-user-completion` once with an opaque
   `result_ref`; Herder's `claimResume` is single-winner, so a duplicate answer
   cannot start a second executor. Legacy blocking `ask_human` without a target
   still exists for plain notifications.
6. Any operator answer — Telegram button, voice, phone, API — lands in one
   `selection_receipts` row and is rendered by one updater on the same card.

## 2. Per-project harness order (needs the Herder side to publish it)

`_incident_profile(profile, runner, project)` reads `GET /api/automation/launch-policy`
**once per job** and resolves the route from the incident's authoritative
`incident.project`:

- `projectRoutes[<project>]` present → its `harnesses` order is walked once;
  each candidate must be in `allowedHarnesses`, have a model in the route's
  `models` or the policy `models`, and appear in `/api/models?harness=<harness>`.
  First match wins.
- No route for that project → unchanged pinned
  `opencode / minimax-coding-plan / MiniMax-M3.1-Flash-Preview` behaviour.
- No candidate available → hard failure, never a silent downgrade.

Accepted `projectRoutes` shapes (both read, so the Herder owner can pick one):

```json
"projectRoutes": { "TelegramAuto": { "harnesses": ["opencode", "zcode"], "models": {"zcode": "account:zai-individual-coding-plan/GLM-5.3-Flash$high"} } }
```

```json
"projectRoutes": { "TelegramAuto": ["opencode", "zcode"] }
```

Execution profiles are derived from the policy model string
(`provider/model$reasoning`), never invented. Storage and replay use
`bounded_health_execution`; only the launch resolver enforces the per-project
allowlist. No second fallback loop exists — the order is read once and applied
once.

## 3. MCP fixes the phone consumer needs

- **Protocol negotiation fixed.** The server now echoes the client's requested
  handshake revision when supported and otherwise offers `2025-11-25`. It never
  answers `2026-07-28`, which is a stateless per-request envelope this stdio
  server does not implement and which made the installed Python SDK raise
  `RuntimeError: Unsupported protocol version from server`. Supported handshake
  revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`.
- `noticeplace_ask_human` — creates the bound question and its card; returns
  immediately with `continuation: "resume"`, never polls. Requires the Herder
  `request_id` and the agent's own harness/`session_id`/`cwd`.
- `noticeplace_pending_questions` — bounded human-readable question, options
  `{value,label}`, `incident_id`, and the exact native routing
  (`native.harness`, `native.session_id`, `native.session_url`) taken from the
  incident's live agent session. It never returns the stored answer, the
  attached document, or `original_text`. A `project: "*"` token sees every
  project it is already authorized for; a single-project token sees only its own.
- `noticeplace_answer` — answers either shape and returns the canonical value
  plus the card edit receipt.
- `noticeplace_incident_progress` — posts progress through
  `POST /v1/incidents/{id}/health/progress` with a stable `Idempotency-Key`, and
  an independent verification through `/health/verification`.

## 4. HTTP surface for the voice/phone resolver

| Call | Purpose |
| --- | --- |
| `GET /v1/human-requests?state=pending&limit=20` | Open questions with options and native routing. |
| `POST /v1/human-requests` | Create the bound question (`native_target` = harness/session_id/cwd). Renders and binds the card. |
| `POST /v1/human-requests/{request_id}/resolve` | Record a free-form or choice answer; marks the bound card and resumes the bound native session once. |
| `POST /v1/incidents/{incident_id}/choice-result` | Record the canonical choice for an incident card. Body: `request_id`, `choice_id`, `actor`, `source`. |
| `POST /v1/incidents/{incident_id}/health/progress` | Bounded progress receipt. |
| `POST /v1/incidents/{incident_id}/health/verification` | Independent verification receipt. |

`choice-result` response:

```json
{"schema":"noticeplace.choice_result.v1","incident_id":"...","request_id":"...",
 "choice_id":"inspect","label":"Проверить юнит","requested_choice_id":"repair",
 "accepted":false,"conflict":true,"card":{"edited":true},
 "card_status":"marked"}
```

`resolve` also returns `{"native": {"status": "resuming", "continuation": "resume"}}`
when the question carried a native target, and `{"telegram": {"status": ...}}`
where the status is the **real** card outcome (`marked`, `already_marked`,
`keyboard_only_retry`, `card_is_not_bound`) — never a promised checkmark.

The first accepted answer is canonical; a conflicting repeat returns
`accepted:false, conflict:true` and never re-marks the card. Free-form answers
carry a stable opaque `choice_id` (`answer-<sha256[:32]>`) while the human text
stays in the receipt label, so the durable identity always satisfies the ASCII
constraint.

## 5. Exact install

Package (validated by both the bundled validator and the real Fleet reader
`harness_fleet.package_model.PackageModel.read`):

```text
noticeplace-plugin/packages/noticeplace/0.1.0/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.0/mcp.json
noticeplace-plugin/packages/noticeplace/0.1.0/skills/noticeplace/SKILL.md
```

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.0/scripts/validate_plugin.py
# PASS noticeplace 0.1.0
```

Agent Harness Fleet: **Plugins + Marketplace → Import →
`noticeplace-plugin/packages/noticeplace/0.1.0` → Publish → target harness →
Preview → Apply**. The package declares no credentials; the MCP server is the
existing `https://notify.bezrabotnyi.com/mcp` (streamable-http) and the client
holds the bearer token. Fast Agent keeps using the existing
`/opt/noticeplace/bin/ask-human-mcp` wrapper — no new proxy is introduced.

## 6. Tests

`tests/test_repair_first_incident.py` covers the vertical slice: verified repair
without planner or plan card, a rejected claim that resolves nothing, a pending
question that keeps one session, per-project route selection, canonical answer
wins over a conflicting repeat, the shared card marker, the pending-question
read, and the MCP handshake plus tools list. The four remaining suite failures
(`test_http_api` remote-MCP trio and `test_notification_center_clients`) fail
identically on base `e42bf57` and are not caused by this branch.