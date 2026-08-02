# Notify Center producer SDKs

Use these tiny clients when a project must report an operational event to the
central Notify Center. They know only the producer HTTP API: they do not carry
Telegram, Matrix, Android, or operator credentials.

## What “wait for a response” means

Creating an event returns `202 Accepted`: Notify Center stored it, but a person
has not necessarily seen it. Waiting is optional and polls the incident with
the same project-scoped token until an operator either:

- acknowledges it (`acknowledged`), or
- resolves it (`resolved`).

Delivery status and a telephone or messenger call are deliberately *not*
treated as a human response. Waiting is bounded by a caller-selected timeout;
it never loops forever.

## Python

Install directly from this repository:

```bash
pip install 'git+https://github.com/megamen32/notify.git#subdirectory=python'
```

Remove it when no longer needed:

```bash
pip uninstall notify-center-client
```

```python
from notify_center_client import NotificationCenterClient

client = NotificationCenterClient.from_environment()
created = client.emit(
    project="my-service",
    severity="critical",
    title="Production deploy failed",
    body="CI run 42 returned a non-zero exit code.",
    dedup_key="deploy:production",
    idempotency_key="deploy-42",
)

# Only when the caller needs an operator acknowledgement/resolution:
answer = client.wait_for_response(
    created["incident_id"], timeout_seconds=3600, poll_interval_seconds=10
)
```

`NOTIFY_CENTER_EVENT_URL` must point to the center's `/v1/events` endpoint and
`NOTIFY_CENTER_TOKEN` must be that project's scoped producer token. Store the
token in the deployment secret store or a mode-`0600` environment file, never
in source control.

The same call can wait inline:

```python
answer = client.emit(
    project="my-service", severity="critical", title="Deploy failed",
    dedup_key="deploy:production", wait_for_response=True,
    wait_timeout_seconds=3600,
)
```

## Node.js

Node.js 18 or newer has the required built-in `fetch` support. Install the
existing package once:

```bash
npm install github:megamen32/notify
```

Remove it when no longer needed:

```bash
npm uninstall notify-mcp
```

```js
import { NotificationCenterClient } from "notify-mcp/notification-center";

const client = NotificationCenterClient.fromEnvironment();
const created = await client.emit({
  project: "my-service",
  severity: "critical",
  title: "Production deploy failed",
  body: "CI run 42 returned a non-zero exit code.",
  dedupKey: "deploy:production",
  idempotencyKey: "deploy-42",
});

const answer = await client.waitForResponse(created.incident_id, {
  timeoutMs: 3_600_000,
  pollIntervalMs: 10_000,
});
```

Or wait inline with `waitForResponse: true` in `emit()`.

## Existing systemd service

Keep the producer token out of the unit and place it in a root-owned
mode-`0600` EnvironmentFile instead:

```ini
# /etc/my-service/notify.env
NOTIFY_CENTER_EVENT_URL=https://notify.bezrabotnyi.com/v1/events
NOTIFY_CENTER_TOKEN=producer-token-created-once-in-the-operator-console
```

```ini
# systemctl edit my-service
[Service]
EnvironmentFile=/etc/my-service/notify.env
```

Then use either SDK from the service process, or the curl contract above. Do
not put the token in `ExecStart=`, command history, source control, or logs.

## Token scope and retry rules

Give each project its own token. Notify Center enforces both the project name
and the maximum allowed severity. For example, a service with a maximum of
`important` cannot create a `critical` event.

Use a stable `idempotency_key`/`idempotencyKey` only to retry the exact same
request. Reusing it for changed content is rejected. Use `dedup_key`/`dedupKey`
to merge repeated sightings of the same still-open incident.

Neither client silently retries an ambiguous create request. If a network
failure happens after sending it, retry with the same idempotency key; the
result is then safe and deterministic.

The source, issue tracker and releases are at
[github.com/megamen32/notify](https://github.com/megamen32/notify).
