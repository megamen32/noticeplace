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

## Acceptance follow-up: buttons and canonical UUID references

Root observed that the real answer card removed its two buttons, and Herder
rejected an old opaque result hash before native admission because its variant
bits were not RFC UUID bits. The first real answer was read once through the
installed `noticeplace_question_result` tool and saved in ignored
`.tmp/native-noticeplace-acceptance.json`. No answer, create, resume, phone call,
topic or runtime change was replayed by this executor.

Choice human cards and incident choice cards now retain the original signed
callbacks for every option, prefixing only the canonical winning option with
`✅`. A repeat acknowledges that canonical label and cannot invoke a second
incident native callback. Text-edit fallback updates the same complete keyboard.
Free-text and legacy analysis card behaviour remains covered separately.

Future answers persist a deterministic RFC UUIDv5 reference in the answer
transaction. `human_request_result_receipt` reads an existing completion's
`result_ref` first. A migration freezes older resolved answer references in
`response_result_ref`, using the existing outbox reference when present and the
original hash only for historical rows without an outbox. No existing outbox
reference, answer or selection is rewritten.

Root has the explicit scoped core seam
`reconcile_unadmitted_human_completion(token, request_id, result_ref,
verified_herder_record, evidence_ref, actor)`. It accepts only the caller's
resolved ordinary question, an `unknown` outbox with the exact canonical
reference, and a freshly verified Herder `pending` record for the same native
target with no attempt, result admission, receipt or lease. It preserves the
reference and records `human_completion.pre_admission_reconciled` with the
operator and evidence. The worker and ordinary answer flow never call this
seam. Root must reconcile the specific verified pre-admission failure after
install; this executor did not reset the live outbox.

Evidence for the Telegram edit contract was read in order: local official
TL `replyInlineMarkup`, `keyboardInlineButton` and `messages.editMessage`;
[Android BotInlineKeyboard](https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/messenger/BotInlineKeyboard.java)
renders every supplied row and each button's text; the installed Notice Bot API
wrapper accepts a method and payload using stdlib HTTP. No topic creation or
new router was added. Root owns the existing Debug topic and project route.

Focused release: **122 tests passed in 50.69 seconds**, including real HTTP,
MCP, sender and callback handlers, canonical native single-delivery regressions,
historical-reference reopen and rejected reconciliation proofs. Fast reference
checks expect 1 second / maximum 5; focused card cases expect 2 / maximum 10.
The combined run is bounded at 180 seconds, 2 GiB and two CPU slots. Portable
plugin validation passed; its immutable `0.1.0` instruction bundle was not changed.

## Phase 2: one incident/family card and bounded native readback

The verified VUSA duplicate sent `health.diagnosis_session` as message 6460
before `telegram.main:initial` created message 6461. All future phases now
select the first **confirmed send from any phase**, ordered by actual receipt
time. The existing delivery table supplies the atomic creation fence across
SQLite connections. A concurrent `sending` or an unresolved `uncertain`
creation cannot authorize another message. Telegram uncertainty does not gate
incident ingress or native repair jobs. A recovery during initial transport
preserves its actual receipt and edits that message. Historical message IDs,
cards and documents are retained; no runtime migration or deletion was run.

`incident_card.render` supplies the Russian projection and controls for initial,
session, outcome, progress, question, answer and legacy edit paths. Critical and
emergency cards offer “Прочитал”; this acknowledges attention without resolving
the incident or cancelling queued repair. Confirmed session receipts replace
“Исправить через ИИ” with “✅ ИИ запущен”, linking that exact session.
Notification muting uses the project/recipient/event-family scope and leaves
monitoring and repair admission running. Accepted choice callbacks remain
visible with the canonical `✅` marker, including later question/progress edits.
Replies to incident questions add no separate confirmation message.

An incident question binds explicitly by `incident_id`, or by an unambiguous
exact harness/native ID recorded on the incident. An ambiguous match requires
the explicit ID. Its question is queued on the original card. Standalone Debug
questions retain their separate single card. Closed incidents withdraw pending
ordinary questions with an audit reason: late HTTP/voice, button and text answers
return `no_accept`, create no completion and send no native message. Historical
accepted answers and result references remain canonical. Definite unadmitted
completion rows are cancelled on closure; unknown/in-flight admissions are
never replayed or rewritten. Linked legacy analysis/progress goes through the
same incident renderer and cannot replace recovery/title/controls with plain
question text.

Similar incidents retain independent records, jobs, audits, states and links.
Only matching project, recipient, consumer, event type, signal and issue family
share an original family card. The family is explicit where available; typed
host/fleet load/cpu/ram/disk signals can group across hosts. Unrecognized checks
retain their source identity. Existing shown cards are not reassigned. Partial
recovery in either member order says that incidents remain open, and never says
that the entire group recovered. Reading/answering one member does not resolve
or acknowledge the others. A group with a closed parent and an open critical child offers a host-labelled ACK for that specific child; after ACK the next unread critical member is offered on the same card.

`IncidentProgressReader` uses spare capacity in the existing agent worker pool,
at most two reads per batch. It reads the **stored** harness/native ID via
`/api/sessions/{harness}/{nativeID}/details?limit=5&history=auto`, not `/progress`
or `quick=1`. The exact live session contract was checked read-only in this
Codex session. Projection contains session status/permission/activity and only
the last assistant `parts.type=text`, redacted and capped at 500 characters.
Reasoning, tool arguments/output and raw `message.text` are excluded. A read
failure does not invent idle or activity. Incomplete history is presented as
the last available answer, with its timestamp.

Changed background projections are persisted on existing incident rows and
flushed through the existing durable delivery queue, no sooner than 15 seconds
after the previous edit. Identical projections coalesce. The latest queued
state survives restart; ingress and real repair jobs are not rate limited.
Human choice/ACK and explicit recovery reactions retain priority. Only a new
send has `disable_notification`: critical/emergency is audible by default,
routine sends are silent, and edits never produce sound. Native fast-ACK
`queue:true` coupling is Root's separate canonical `088` change; this executor
did not edit that foreign WIP.

AdminConfigStore and the existing settings form expose: card edit floor 15s,
family window 300s, grouping on, native progress on, all notifications visible,
critical sound on, routine sound off. All are live settings; invalid booleans,
nonfinite values, excessive windows and an edit floor below 15s are rejected.
No scheduler, router, manual skill copies, token move or plugin instruction
change was introduced; bundle `0.1.0` stays immutable.

Focused regressions: fast reference/render tests expect <1s/max5s; the real
worker/Telegram transport fence tests expect 2s/max10s; SDK/HTTP/button/reply
and linked-progress tests expect 3s/max10s. These are fast unit or focused
integration tests; no slow/nightly/broad suite was run. The final combined
release evidence is recorded in `.agents/tasks/noticeplace-phase2-ready-20261012.md`.
Root owns installation and actual Debug Telegram/phone acceptance after the
frozen branch SHA. Source/fixture checks are not that live acceptance.


## Phase 2 repair constructor and native completion binding

The actual AutoSellBot session contained a persisted diagnose-only/no-tools
instruction followed by appended repair-first prose. Diagnosis and legacy
orchestrator profiles now use a whole source-owned GSD execution contract;
their persisted `instruction` is replaced before bounded profile validation.
URL, CWD, harness, model policy, protected config-file and callback scope checks
remain enforced. Planner mapping is no longer a prerequisite. Existing legacy
JSON observations do not create another planner session or a three-plan menu.
The example profile now describes repair and has no planner mapping.

The prompt includes bounded, redacted original incident and health source,
host, signal, fingerprint, correlation and evidence/trace references. The
existing delivery-payload and GPTAdmin event projections retain original
`evidence_refs`, including consumer endpoints. It directs the native harness
to discover and read installed original `gsd-debug`/`gsd-quick`, read project
AGENTS.md/runbooks, reproduce the consumer failure, repair its cause and verify
that consumer without a new planning bootstrap. Missing Notice MCP does not
forbid repository/tools/repair: the prompt names the protected AskHuman env
file for human questions and the existing project-scoped callback JSON for
progress, without exposing credentials. Source observations cannot resolve.

A plain terminal assistant/GSD turn completes the bounded observer without a
JSON gate. If the observation window expires while native work continues, the
helper returns `observer_only/awaiting_executor`; GPTAdmin preserves these flags
and core accepts the observation without a failure/verification/recovery claim.
Actual native terminal failure still fails. Existing autopilot keeps the same
executor; no repair was replayed and no new live session was created here.

`arm_session_autopilot` carries optional
`noticeIncident:{incidentId:<original>,project:<original>}` in the existing PUT
body, together with the authoritative launch receipt's harness/native ID/CWD.
Herder owns persistence and original-card completion/progress delivery. Exact
protected callback schema and endpoint contract were sent to that native owner;
Root owns runtime integration/GSD installation and real original-session repair.


Automated `noticeplace_incident_progress` uses only the existing project-scoped
Notice authorization; it no longer calls the unused human-actor configuration.
Question/result handlers still require human actors. A global Hermes plugin token
cannot post progress to another project; use the protected original-project
callback map/HTTP seam described above. The real MCP/HTTP/DB regression proves
missing actors do not gate Autosell progress, wrong-project tokens fail, and
question actor validation remains enforced. No new MCP or token scope was added.


## Generic executor display identity (2026-10-12)

Generic done/human producer events may include optional `execution_identity`.
It is independent of the summary body and uses the existing event JSON storage;
no schema version, plugin bundle, table, scheduler or topic is added.

Required keys: `harness` (opencode/codex/hermes/zcode/claude/qoder/fast-agent/chatgpt),
`session_id` (actual native ID, nonempty <=512 chars, no padding/control chars).
Optional string keys: `model` <=256, absolute `cwd` <=2048, `session_url` <=2048.
Only those keys are accepted. If supplied, session_url must be exactly
`https://agent.bezrabotnyi.com/#/session/` plus JavaScript
`encodeURIComponent(harness + ':' + session_id)`. Missing URL stays missing.
Invalid metadata is rejected at event ingress; old unvalidated metadata is ignored.

Core projects latest validated display identity separately from `incident.body`.
It never creates `ai_session`, native bindings/jobs, launch/resume rights or native
readback. Existing project authorization and top-level forbidden authority fields
remain intact. Real registered health/session receipts outrank display metadata.
The central card prominently identifies the actual executor/model/CWD, retains the
actual native URL as a separate session button and shows the technical project as
separate context. Display metadata does not claim confirmed launch or current work.
Legacy cards without metadata report that the executor is unspecified, without
inferring it from project or prose and without inventing a link. Summary truncation
and same-message edits cannot remove the independent identity/button.

Focused producer-payload/HTTP/DB/restart/card-worker regression uses an 8KiB summary,
checks one create/message42 and subsequent edit42, executor metadata, scope/authority
rejection, legacy/no-URL behavior and bound native receipt/current-progress priority.
Herder owns writing genuine producer metadata; Notice owns this validator/projector/
renderer. Root owns install and actual Telegram acceptance; no historical generic
router notification was replayed or changed by this executor.
