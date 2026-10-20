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
closed. This source slice does not install an HTTP, MCP, or dashboard route;
the consuming app must provide and verify its own cross-app trust boundary.

```python
view = TaskDecisionView(center, trusted_project_guard)
projection = view.read(authenticated_principal, request_id)
```

## Projection semantics

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
