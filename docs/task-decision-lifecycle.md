# Notice task-decision lifecycle (N61–N63)

This source slice extends Notice's existing `human_requests` row. That row
remains the canonical question, access-policy and human-answer record. The
`TaskDecisionStore` adds task mandate metadata and guarded state transitions;
it is not a second question table or scheduler.

## Issuance and identity

`NotificationCenter.create_task_decision_request` remains the internal storage
seam and is exposed by the existing `POST /v1/task-decisions` REST route. The
route requires a startup-configured `BoardTaskDecisionVerifier`. It reads the
current manager-protected Board card, binds its latest execution receipt and
version/owner, derives answer actors from the existing explicit operator
allowlist, and fixes the recipient to `NOTIFY_CENTER_RECIPIENT`. An alternate
caller-selected recipient is rejected. The request ID is derived from
`(task_ref, purpose, version)`; an identical replay reuses the same row, and
changed canonical input for that identity is rejected. The timeout option must
be one of the immutable allowed choices.

The existing `bin/notify-center` startup wiring reads the protected references
`EXMANAGER_KANBAN_URL`, `EXMANAGER_KANBAN_TOKEN`, `NOTIFY_CENTER_RECIPIENT`,
and one explicit supported Human Request actor allowlist. If any required
reference is absent or invalid, the verifier remains unset and task-question
creation fails closed. The source wiring is published; this does not prove the
references are loaded in production, every API replica runs this revision, or
that a live task-origin/UserIO grant was verified. The fake verifier in
`tests/test_task_decisions.py` is only a local test fixture.

## Receipt-started deadline

The existing Telegram HumanRequest sender reserves one notification attempt
before I/O. A successful `sendMessage` response must include a message ID, and
the existing `bind_human_request_message` transaction stores that exact
receipt and the first success timestamp together. The due time is exactly
1,800 seconds after that persisted timestamp and is immutable. A queued request,
missing configuration, failed send, malformed response, or ambiguous send
outcome leaves the clock unarmed. `sending` and `unknown` attempts are not
automatically replayed.

The existing `DeliveryWorker.run_once` processes due task questions. Its
conditional update commits one timeout choice only when the persisted due time
has arrived, the success receipt exists, the current mandate verifier still
allows the timeout, and no answer/control transition has won. A timeout is
stored as `task_decision_source=timeout`; it does not populate human response
fields or fabricate an answer. Restart reads the same timestamp and due time.

## Answer and control races

Human answers use the existing `human_requests.state` compare-and-set and the
current verified actor policy. The timeout uses a competing SQL compare-and-set
on the same row. A late answer is appended as a new instruction with its actor
and value evidence; it cannot replace a committed timeout or replay its
continuation.

An authenticated `stop` or `hold` is a separate durable control intent with a
stable control ID and increasing epoch. `control_command()` now carries the
exact verifier-derived execution binding with task reference, Board operation
ID/version, owner, host, harness, native session, and native turn. The action
owner must serialize its CAS against that immutable binding and make
`revoke(command, control_id)` idempotent for the stable ID. An acknowledgment is
accepted only when it echoes the same control ID and full execution binding,
matches the current control epoch, and carries a durable receipt reference;
accepted and unknown prior effects are retained.

After a worker restart or ambiguous reply, the worker calls
`query_control(control_id)` before any retry. An acknowledged query result must
echo the same control ID, execution binding, and effective epoch. A pending or
unknown result stays blocked. Only an authoritative `not_found` result with
`retry_safe: true` and the same control ID/execution binding permits a retry
using that same ID. A durable short lease coordinates concurrent worker claims;
expired `sending` and `unknown` controls are enumerated for reconciliation.
Without the query contract, the worker leaves the control unknown and never
blindly repeats a revoke. Missing adapters, transport exceptions, mismatched
receipts, or ambiguous outcomes leave continuation blocked. A hold requires a
new explicit resume reference after acknowledgment. An acknowledged stop is
terminal for its immutable task/purpose/version; new authorization needs a new
version and request. This does not claim cross-database atomic cancellation or
undo already accepted/unknown actions.

Native bound HumanStop requests remain governed by the existing Agent Herder
pending gate. Their `expires_at` does not expire them here, and this code does
not synthesize a human answer or resume a native session.

## Remaining integration gates

- Verify the protected references and installed verifier on every serving API
  replica before enabling task-question admission.
- Add the Board-facing immutable `request_id` reference under the backend's
  current-card owner/CAS authorization so the authenticated UI can read the
  existing Notice projection; the Board card has no such reference today.
- Connect an authenticated Herder/UserIO action-owner operation/query API that
  serializes revocation against the exact native turn and returns a durable
  control-ID/execution-binding/epoch acknowledgment. Current Board/Herder source
  exposes no such task-control operation; session-wide `stop_agent` text is not
  an acknowledgment.
- Verify provider-specific successful-notification evidence and action-owner
  receipts on the supported production transport.
- Exercise a real task through the consumer path, including restart and late
  answer/control reconciliation. No live task, provider call, runtime change,
  or 30-minute business canary was performed by this source slice.

## ExManager same-client question transport addendum (2026-10-21)

For the fixed `airlock` project, Notice can use the existing ExManager Node
capability instead of its separate Bot API. The adapter accepts only the
dedicated TLS base URL `https://notify.bezrabotnyi.com/_exmanager`, uses the already
authorized Airlock project Bearer, and pins readiness/receipts to the existing
`@deus_excode` account and selected peer `-1004352022764`. It does not own a
Telegram client or fall back to Notice Bot delivery. Each immutable Notice
request uses `requestId=td-…` and stable `operationId=notice-question:<requestId>`.
The POST contains only `requestId`, `operationId`, fixed `chatId`, and rendered
text with every exact allowed value. A successful receipt binds the exact
provider message and starts the existing 30-minute deadline.

If a prior attempt is `sending` or `unknown`, Notice only calls the Node query
route. An accepted query receipt can finish the existing Notice row; `unknown`,
`not_found`, transport failure, or malformed receipt never triggers another
POST. A provider-declared definitive failure is stored as `failed` and is not
replayed. The read-only readiness check proves the Node account and peer are
addressable; it does not prove group membership, task authority, or an answer.

The accepted receipt contract includes integer `acceptedAt` epoch seconds,
derived by Node from the first persisted accepted operation. Notice rejects
missing, boolean, or fractional timestamps and uses this persisted time when
recovering an accepted query result, so a late readback does not restart the
30-minute window. A query result of `sending` is normalized to `unknown` and
remains query-only.

The Node bridge itself is bound to the server-100 host bridge and direct
read-only checks from server-44 and server-88 timed out. The canonical
infrastructure source contains the narrow TLS vhost route for the three
authenticated readiness/question/query paths and was published on `main` in
commit `cfef8e0838ec87dc55e33c08f54093eef577edd1`. Its site-scoped apply was
refused because the live managed Nginx tree differs from its applied manifest;
the read-only diff also shows unrelated VPN and `conf.d` drift. No live file
was adopted or edited, so the public base URL is not yet verified from all
replicas. Task admission also remains closed until the Board verifier,
selected recipient and current task-operation authority are loaded and
independently read back. No live question, send, provider action, or Notice
configuration reload was performed by this source addendum.
