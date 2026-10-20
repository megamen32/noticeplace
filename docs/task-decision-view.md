# Task decision read projection

`notification_center.task_decision_view.TaskDecisionView` is a read-only
projection of the canonical `human_requests` row created by N61–N63. It does
not create a second question store and does not send, resolve, schedule, or
mutate anything.

## Authorization contract

Construct the view with a trusted `authorize(principal, request_id)` callback.
Each `read` calls that callback before querying the task row. The callback must
use the authenticated principal, resolve the request's current project scope,
and return literal `True` only when current access is allowed. A caller-provided
actor string is not an authority. If no callback is configured, reads fail
closed. The canonical Notice HTTP API registers this projection at
`GET /v1/task-decisions/{request_id}`. It authorizes the bearer project token
against the existing human-request/project scope before returning the view;
the route does not trust a client-supplied project or actor. No MCP tool or
dashboard route is registered. The consuming Board UI must call the Notice API
through its trusted server-side integration and must not expose a Notice token
in the browser.

`POST /v1/task-decisions` accepts only a verified `mandate` and returns this
same projection with `delivery_status` and `idempotent`. It requires the
protected Board verifier at startup; without the current Board URL, manager
token, fixed `NOTIFY_CENTER_RECIPIENT`, and explicit actor allowlist, the
verifier stays unset and task-question creation is denied. The existing
`POST /v1/human-requests/{request_id}/resolve` remains the answer route; the
DeliveryWorker owns timeout evaluation. The view may show stop/hold state, but
does not imply an action-owner acknowledgment unless its receipt matches the
current control epoch.

```python
view = TaskDecisionView(center, trusted_project_guard)
projection = view.read(authenticated_principal, request_id)
```

## Projection semantics

The HTTP read response uses schema `noticeplace.task_decision_view.v1` and
includes the request ID, project-scoped question/choices/reference, notification
attempt state/receipt/deadline, durable winner, control status/effects and late
instructions. The request ID returned by task-decision creation should be
retained with its owning execution record so the consumer can read the same
canonical row after restart.

- Question text, choices, project, recipient, and task/purpose/version/mandate
  references come from the task-scoped canonical request row.
- Notification attempt state remains `queued`, `sending`, `unknown`, or `sent`.
  The receipt is present only when the persisted first success timestamp exists
  with state `sent`. It means the existing sender recorded a successful
  transport response. It does not establish recipient delivery or read status.
- The deadline is exposed only with that durable success receipt and only from
  the persisted deadline value. An uncertain or failed attempt has no countdown.
- The winner is the persisted human or timeout decision, including its source
  evidence. No winner is synthesized while the row has none.
- Control state exposes pending/unknown work and the durable action-owner
  acknowledgement. `acknowledgement_matches_current_epoch` is true only for an
  acknowledged state whose receipt epoch matches the current control epoch.
- Late instructions are returned from the existing append-only JSON field.

The source row currently stores one first-success timestamp and current control
state, not a history of every send attempt. The projection does not infer that
missing history. Production dashboard registration remains gated on a verified
trusted guard and the normal application integration path.

Focused disposable SQLite contract:

```bash
PYTHONPATH=. pytest -q tests/test_task_decision_view.py
```
