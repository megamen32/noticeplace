# Notice task-decision lifecycle (N61–N63)

This source slice extends Notice's existing `human_requests` row. That row
remains the canonical question, access-policy and human-answer record. The
`TaskDecisionStore` adds task mandate metadata and guarded state transitions;
it is not a second question table or scheduler.

## Issuance and identity

`NotificationCenter.create_task_decision_request` is an internal adapter seam,
not an HTTP or MCP operation. It requires an installed mandate verifier to
return the authenticated task reference, purpose/version, mandate reference,
current allowed actors, permitted options, and the bounded timeout
continuation. The request ID is derived from `(task_ref, purpose, version)`;
an identical replay reuses the same row, and any changed canonical input for
that identity is rejected. The timeout option must be one of the immutable
allowed choices.

No production verifier is configured by this slice. Creation therefore fails
closed by default. The fake verifier in `tests/test_task_decisions.py` exists
only for local tests; it is not evidence of a live task-origin or UserIO grant.

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
stable control ID and increasing epoch. The existing delivery worker passes
that intent to an injected action-owner adapter. `revoke(command, control_id)`
must be idempotent for that stable ID. After a worker restart or an ambiguous
reply, the worker calls `query_control(control_id)` before any retry: an
acknowledged result is committed, a pending/unknown result is left blocked, and
only an authoritative `not_found` result with `retry_safe: true` permits a
retry using the same ID. A durable short lease coordinates concurrent worker
claims; expired `sending` and `unknown` controls are enumerated for that
reconciliation. Without the query contract, the worker leaves the control
unknown and never blindly repeats a revoke. Only a matching owner receipt and
effective epoch records acknowledgment; accepted and unknown prior effects are
retained. Missing adapters, transport exceptions, mismatched receipts, or
ambiguous outcomes leave continuation blocked. A hold requires a new explicit
resume reference after acknowledgment. An acknowledged stop is terminal for
its immutable task/purpose/version; a new authorization must use a new version
and request. This does not claim cross-database atomic cancellation or undo
already accepted/unknown actions.

Native bound HumanStop requests remain governed by the existing Agent Herder
pending gate. Their `expires_at` does not expire them here, and this code does
not synthesize a human answer or resume a native session.

## Remaining integration gates

- Install a supported verifier that proves the real task origin, immutable
  mandate/version, and current actor/timeout policy before creation or timeout.
- Connect an authenticated action-owner adapter that serializes revocation
  against admission and returns a durable epoch-bound acknowledgment.
- Verify provider-specific successful-notification evidence and action-owner
  receipts on the supported production transport.
- Exercise a real task through the consumer path, including restart and late
  answer/control reconciliation. No live task, provider call, runtime change,
  or 30-minute business canary was performed by this source slice.
