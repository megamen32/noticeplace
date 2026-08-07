# Allowlisted GPTAdmin agent jobs

NoticePlace can turn a durable incident into one fixed GPTAdmin automation
without giving an event control over a server, command, MCP tool, model, CWD,
URL, credential, or prompt.

```text
Notify delivery
  -> HMAC GPTAdmin webhook route
  -> fixed ShellMCP target
  -> notify-agent-job fixed profile
  -> loopback Agent Herder new_or_resume
  -> signed polling of the durable GPTAdmin job
  -> bounded Notify audit receipt
```

## Authority boundaries

The producer token owns the job allowlist:

```json
{
  "project-token": {
    "project": "infra",
    "max_severity": "critical",
    "agent_jobs": ["repair_100"]
  }
}
```

An event may add only `"agent_job":"repair_100"`. When `agent_job` is present,
Notify rejects payload fields such as `target`, `command`, `url`, `harness`,
`cwd`, `prompt`, `tool`, `mcp`, credentials, secrets, and callback URLs.

The root-owned Notify environment maps the allowed name to one signed route:

```ini
NOTIFY_GPTADMIN_AGENT_JOBS_JSON={"repair_100":{"url":"https://gptadmin.example/webhooks/v1/notify-repair-100","hmac_secret":"<dedicated-route-secret>","timeout_seconds":90,"poll_interval_seconds":1}}
```

Notify uses HMAC v2 over method, exact request path, Unix timestamp,
`Idempotency-Key`, and the SHA-256 body digest. It then signs empty-body GET requests to
`/webhook-jobs/{job_id}` until the Hub reports `completed` or `failed`. A crash
or ambiguous timeout retries the same body and key, so Hub returns the original
job instead of dispatching a second side effect.

## Host profile

Install `bin/notify-agent-job` with the rest of Notify. Its config defaults to
`/etc/gptadmin/agent-jobs.json`, must be owned by the ShellMCP execution user,
and must have exact mode `0600`:

```json
{
  "profiles": {
    "repair_100": {
      "url": "http://127.0.0.1:18787/api/sessions/new-or-resume",
      "harness": "codex",
      "name": "repair_100",
      "cwd": "/home/roomhacker/ServersAdministartion",
      "mode": "queue",
      "instruction": "Investigate disk pressure read-only. Do not delete data or reboot. Return evidence and a proposed recovery."
    }
  }
}
```

The helper accepts only the exact loopback Agent Herder endpoint. The profile,
not the event, owns harness, name, absolute CWD, delivery mode and fixed
instruction. Agent Herder performs the canonical CWD/existence check in its own
runtime boundary. Incident values are length-bounded and explicitly labeled as
untrusted telemetry.

Configure the GPTAdmin route with `"signature_version":"v2"`, HMAC
authentication, the fixed
`shell:<host>` target, `bounded_autonomous` approval, and this command:

```text
GPTADMIN_NOTIFY_EVENT={{json}} exec /opt/noticeplace/bin/notify-agent-job run repair_100
```

GPTAdmin's shell renderer passes `{{json}}` through a second environment value
rather than splicing event text into shell source or exposing it in argv.

## Event example

```json
{
  "schema": "notify.event.v1",
  "project": "infra",
  "recipient": "ops",
  "kind": "incident",
  "severity": "critical",
  "title": "Disk pressure on server-100",
  "body": "root filesystem 95 percent",
  "dedup_key": "disk-full:server-100:/",
  "agent_job": "repair_100"
}
```

Use a stable producer `Idempotency-Key` when retrying the same event. A new
observation that should deliver updated telemetry gets a new producer key while
keeping the same `dedup_key`.
