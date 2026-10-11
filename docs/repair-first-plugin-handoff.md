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
| `admission_unknown`, `human_hold`, `in_flight` | terminal for this `operation_id`; `in_flight` is a reservation whose result is unknown, so repeating it never starts a second executor |
| `pre_admission_rejected` | terminal here; retrying is Agent Herder's job, inside its own attempt list |
| `no_harness` | hard failure naming the project |
| anything else | hard failure; an unknown outcome is never guessed |

For a **configured** project the route owns harness, model, and workspace root;
the profile is only a hint and its values are ignored (visible in
`routing_reason`). Notice Place therefore arms the autopilot and polls the
session with `selected_harness`, `model`, and `cwd` from the response receipt,
never from the profile. The profile default still applies to an unconfigured
project.

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
3. The answer (Telegram button, Telegram free-text reply, HTTP, voice, phone)
   goes through **one** canonical completion: the first accepted value wins, the
   same card is marked from the durable selection receipt, and the bound native
   session resumes exactly once. A conflicting repeat changes nothing: no second
   card edit, no second resume. Card outcome and native delivery are reported
   separately. Notice Place calls Agent Herder's existing
   `POST /internal/human-requests/ask-user-completion` with the same opaque
   canonical `result_ref`. The answer and `human_request_completions` outbox row
   commit in one transaction. Concurrent and conflicting answers reuse that
   row; a card edit failure never stops the native attempt. The existing
   `bin/notify-center` agent pool processes overdue continuations and failed card
   edits with at most two rows per task. No new scheduler or autopilot is added.
   Herder's durable native admission is single-winner.
4. The resumed session calls `noticeplace_question_result(request_id)`, which
   returns the canonical answer from storage plus its opaque `result_ref`; a
   secret request returns only the reference.

A metadata-only Herder request (no message) creates no card: `message` is
required by `noticeplace_ask_human`, and `choices` without a message is not a
question.

A question bound to a native session is owned by the Agent Herder pending gate:
Notice Place never expires it, and cancels it only through the Herder emergency
exit, which requires an explicit `reason`, is terminal, and never resumes the
native session. Unbound legacy and metadata-only requests keep their original
TTL and cancel behaviour. `kind` is explicit: `secret` requests may not carry
`native_target`, every ordinary resolution route rejects them, and historical
stored results return only an opaque reference. Secret entry uses AskSecret/SSS.
An expired or cancelled unanswered question returns `answer_status: no_accept`
with no value, no card edit and no native attempt.

Native completion retry contract (coupled to the Herder owner's change):

| Receipt | Notice Place action |
| --- | --- |
| `resuming` / `resumed`, without `in_flight` | mark completion sent |
| `resume_failed`, `reason=agent-resume:native-unavailable`, `retry=true` | retry the same request/result reference after 30 seconds |
| `pending`, `retry=true` | retry the same request/result reference after 30 seconds |
| loopback connection refused before sending | retry the same request/result reference |
| `retry=false`, `in_flight`, admission unknown, generic HTTP error, lost/malformed response or timeout | preserve unknown outcome; no automatic replay |

A crash after the answer transaction but before sending leaves a due `pending`
row. A crash during sending leaves `sending`, which is not replayable. The
Herder owner confirmed the coupled contract in its owned worktree: HTTP 502
`{request_id, status: resume_failed, continuation: resume,
reason: agent-resume:native-unavailable, retry: true}` allows safe re-admission
of the same canonical reference, retaining attemptId, idempotencyKey and the
original admittedAt. Conflicting references and unknown admission return
`retry: false`; `in_flight` is query-only even after an expired lease. Herder
production HTTP recovery regression passed; root integrates the two delivered
branches and performs the live acceptance. Its worktree was not modified here.

Retries are deduplicated: Agent Herder sends `Idempotency-Key:
human-request:<request_id>` and Notice Place rejects any key that does not match
the request id, so one binding can never produce two questions. A choice
question needs between two and four options; one option is refused, exactly as
in the Agent Herder choice registry. Resumed turns reference
`noticeplace_question_result` only for `answer_source=notice_question`; a
metadata-only or secret question carries no Notice Place answer.

## 4. MCP surface

| Tool | Purpose |
| --- | --- |
| `noticeplace_ask_human` | One bound question and one card from a Herder binding. |
| `noticeplace_pending_questions` | Bounded question text, options, incident, exact native routing. Never returns a stored answer, document, or secret. |
| `noticeplace_answer` | Records an answer for a question or an incident choice card; returns the canonical value and the real card outcome. |
| `noticeplace_question_result` | The canonical answer plus its opaque reference, for the resumed session. |
| `noticeplace_incident_progress` | Bounded executor observation with a stable Idempotency-Key; independent verification uses its own HTTP producer. |
| `ask_human` | Unchanged legacy notification/blocking question; binds nothing. |

HTTP additions:

| Call | Purpose |
| --- | --- |
| `POST /v1/human-requests` | Create the bound question (`native_target` = harness/session_id/cwd). |
| `GET /v1/human-requests/{request_id}/result` | Canonical answer receipt for a resumed session. |
| `POST /v1/human-requests/{request_id}/resolve` | Canonical completion: `{state, value, label, card, native}`. `card` is the real Telegram outcome, `native` is the resume status, reported separately. |
| `POST /v1/human-requests/{request_id}/cancel` | Refused while the question is bound to a native session. |
| `POST /v1/incidents/{incident_id}/choice-result` | Canonical choice for an incident choice card. |
| `POST /v1/incidents/{incident_id}/health/progress` | Bounded progress receipt. |
| `POST /v1/incidents/{incident_id}/health/verification` | Independent verification receipt. |

MCP protocol negotiation echoes the client's requested handshake revision and
never answers `2026-07-28`, which is a stateless per-request envelope this
server does not implement.

## 5. Exact install

```text
noticeplace-plugin/packages/noticeplace/0.1.0/{plugin.json,mcp.json,mcp.codex.json,skills/noticeplace/SKILL.md}
noticeplace-plugin/packages/noticeplace/0.1.0/.codex-plugin/plugin.json
noticeplace-plugin/packages/noticeplace/0.1.0/.zcode-plugin/plugin.json
noticeplace-plugin/.agents/plugins/marketplace.json
noticeplace-plugin/.claude-plugin/marketplace.json
```

```bash
python3 noticeplace-plugin/packages/noticeplace/0.1.0/scripts/validate_plugin.py
# PASS noticeplace 0.1.0
zcode plugins validate noticeplace-plugin/packages/noticeplace/0.1.0 --json
# {"ok": true, "diagnostics": []}
```

The package wraps the **existing authenticated stdio** server, so no credential
is duplicated and no new proxy exists:

```json
"NoticePlace": {"type": "stdio", "command": "/opt/noticeplace/bin/ask-human-mcp",
 "args": [], "env": {"ASK_HUMAN_ENV_FILE": "/home/roomhacker/.config/ask-human/env"}}
```

Only protected file paths are named; no secret value appears in the package, and
the validator rejects inline credentials. The same bundle ships native
projections for every harness:

| File | Target |
| --- | --- |
| `mcp.json` | Agent Plugins 1.0 / Fleet / Hermes portable package |
| `mcp.codex.json` | Codex and ZCode native MCP declaration |
| `fast-agent.yaml` | Fast Agent `mcp.servers` entry |
| `opencode.json` | OpenCode `mcp` local server entry |

Both native manifests reference `skills: ./skills/` and
`mcpServers: ./mcp.codex.json`. Codex marketplace entry source is
`{source: local, path: ./packages/noticeplace/0.1.0}`; the ZCode marketplace entry
source is `./packages/noticeplace/0.1.0`. The installed skill explicitly maps
Herder `{choice_id, label}` to tool `{value: choice_id, label}` and requires
`cwd=binding.native_target.cwd`. Empty choices create a free-text question.

Agent Harness Fleet: **Plugins + Marketplace → Import → Publish → target
harness → Preview → Apply**. Install and the live phone canary remain root's.

## 6. Tests

`tests/test_ready_mcp_binding.py` runs the real Notice Place HTTP stack and the
real MCP tool handlers against the two network peers (Telegram and Agent Herder)
replaced by a fake Bot API and a local HTTP server. It proves the agreed chain: one Herder binding, one
`noticeplace_ask_human` call, exactly one card, a replay that creates no second
card, `noticeplace_pending_questions` with the exact native routing, a voice
answer that marks that card, exactly one Herder completion callback, and
`noticeplace_question_result` returning the canonical answer and reference.

The new regressions run real handlers and the production `worker_loop` with two
workers; the peers supply failures without contacting a real person or phone.

| Scenario | Purpose / detected defect | Category | Expected / maximum seconds |
| --- | --- | --- | --- |
| HTTP card failure | card failure cannot suppress native delivery; same card recovers | focused integration | 2 / 8 |
| Native unavailable + restart | durable retry retains the same canonical result reference | focused integration | 2 / 8 |
| Button and free text + both failures | all surfaces use the same canonical completion and worker | focused integration | 2 / 10 |
| Answer/outbox storage failure | answer cannot commit without the owed continuation | focused integration | 2 / 8 |
| Lost/unknown native receipt | uncertain admission must not replay the native transport | focused integration | 2 / 10 |
| Concurrent HTTP answers | one canonical winner, one card edit, one native attempt | focused integration | 2 / 8 |
| Expired/cancelled HTTP answer | explicit no acceptance, no invented value, no effects | focused integration | 2 / 8 |
| Secret HTTP/legacy answer | reject ordinary secret completion and keep historical values opaque | focused integration | 2 / 8 |
| Authoritative project launch | arming and polling use receipt harness/model/CWD | fast unit | 1 / 3 |
| Legacy analysis callbacks | retain immediate progress confirmation and retry visibility | fast unit | 2 / 8 |
| Bundle validation | native manifests, component sources and marketplace paths agree | fast unit | 2 / 5 |

Ordinary focused verification is capped at 180 seconds including package
validation, with 2 GiB address-space and two CPU slots. No nightly or broad
baseline suite is used for this handoff. Native installs and the real phone
acceptance remain root-owned.

Final owned-change verification: **100 passed in 43.11 seconds** across
`test_ready_mcp_binding`, `test_repair_first_incident`, `test_human_requests`,
`test_human_request_progress`, and `test_health_session_lifecycle`.
Portable validation passed; actual `zcode plugins validate` returned `ok: true`
with no diagnostics; `git diff --check` passed. Combined run took **47.03 seconds**.

`tests/test_repair_first_incident.py` covers the executor contract: verified
repair without a planner or plan card, a rejected claim that resolves nothing, a
pending question that keeps one session, per-project route selection and aliases,
the admission receipt being authoritative, canonical answer over a conflicting
repeat, the shared card marker, and the MCP handshake plus tools list.

The earlier handoff recorded four broad-suite baseline failures (`test_http_api`
remote-MCP trio and `test_notification_center_clients`) on base `e42bf57`.
That historical claim was not rerun in this finite focused verification.
