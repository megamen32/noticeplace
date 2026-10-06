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

## Session visibility

As soon as Agent Herder accepts a diagnosis, plan-orchestrator, or remediation
session, the helper posts its real session identity to Notice Place. The card
shows the stage, harness, configured model and **Открыть сессию** link. Creating
a session does not claim that a queued model turn has already started; its
actual state and transcript remain visible in Agent Herder.

Session cards are informational: incident acknowledgement cancels calls and
escalation while preserving these cards, including a link received after ACK.
The link API accepts safe Agent Herder harness identifiers rather than a fixed
four-harness list. Execution still follows the fixed allowlisted job profiles;
publishing a link grants no permission to select another model or run commands.

The phone assistant explains the alert and cannot itself launch a diagnostic
or repair. It must not promise that work has started without session evidence.

## Host profile

Install `bin/notify-agent-job` with the rest of Notify. Its config defaults to
`/etc/gptadmin/agent-jobs.json`, must be owned by the ShellMCP execution user,
and must have exact mode `0600`. Every parent directory must also be traversable
by that user (for example `/etc/gptadmin` as `root:<execution-group>` mode
`0710`); a correct file owner is useless when the parent is mode `0700`:

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

The health workflow adds a second fixed profile, `health-remediation`. It is
not selected by event data: the profile itself pins the remediation target and
the exact execution contract, while the event is allowed to carry only the
already signed plan choice and bounded health telemetry:

```json
{
  "health-remediation": {
    "url": "http://127.0.0.1:18787/api/sessions/new-or-resume",
    "harness": "codex",
    "name": "health_remediation_100",
    "cwd": "/home/roomhacker/ServersAdministartion",
    "mode": "queue",
    "model": "o3",
    "reasoning": "high",
    "topic": "health",
    "instruction": "Apply only the selected health remediation plan and report useful progress."
  }
}
```

For this profile the helper rejects a missing or altered selection and sends
the model to Agent Herder before the first message. The durable NoticePlace
selection creates exactly one `gptadmin.agent:health-remediation` delivery;
the delivery worker must have the matching signed GPTAdmin webhook route
configured before enabling the profile.

The diagnosis callback file may serve several project-scoped producers without
granting one token cross-project authority. Keep it owned by the ShellMCP
execution user with mode `0600` and map each repairable project explicitly:

```json
{
  "url": "http://127.0.0.1:8091",
  "token": "legacy-default-project-token",
  "tokens": {
    "health-monitor": "health-monitor-token",
    "winramp": "winramp-token"
  }
}
```

`health-diagnosis` selects `tokens[incident.project]` and never puts the token
in the agent event. Projects absent from the map cannot attach plans and fail
closed with HTTP 401.

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

## Bounded session-result reads

Agent Herder returns history metadata even with `details?limit=1&history=auto`.
The October 6 live diagnosis measured 129,182 bytes for one final turn
(131,704 bytes for three); its valid JSON receipt was rejected by the former
64 KiB guard. Only session-details GETs now allow at most 1 MiB, read with a
one-byte overflow sentinel. Other responses and error bodies stay at 64 KiB.
Diagnosis and orchestration request only the latest turn; no full-history fetch.

The existing main service measured about 25 MiB with 12 tasks against its
512 MiB/1 GiB RAM, 256 MiB swap, two-CPU and 256-task budget. With two delivery
workers, at most two sequential bounded reads add 2 MiB raw response storage
plus JSON allocations; reserve 32 MiB for these readers within the existing
service ceiling. No CPU, process, swap or service-memory quota is raised.
No response body is written to disk; private metadata-only proof files remain
in ignored `.tmp/`. A read-only diagnosis/orchestrator consumer canary verifies
this budget change after deployment. Larger responses still fail explicitly.

The one-tab UI acceptance probe runs serially after the chain finishes, inside
the existing operator session guard (6/8 GiB soft/hard RAM, 1 GiB swap, eight
CPUs and 4096 tasks shared with the harness). Before this probe the session
measured 5.16 GiB and 292 tasks, with zero instantaneous memory PSI. Permit
only one browser, at most two renderer processes, no GPU workload and a
60-second command timeout; close the dedicated session afterwards. Store its
profile/evidence under `.tmp/session-links-20261006/`, with a 256 MiB disk/cache
budget and no downloads. Stop rather than add browsers when the shared guard
shows pressure; this does not raise or escape the existing session limits.

## Automatic health runtime policy (2026-10-06)

The Agent Herder web interface owns new automatic launches through an
independent persisted `GET/PUT /api/automation/launch-policy` contract:
`version:1`, `allowedHarnesses`, `preferredHarness`, and native `models` by
harness. The current explicit policy permits Codex/ZCode and prefers Codex.
Continuation retains its separate `/api/session-autostart` toggles.

Notice Place reads the latest policy immediately before diagnosis and planning
creation; it uses the selected harness's own model from that policy. A missing
policy (503), disabled empty allowlist, missing model, excluded runtime or a
changed preference during a chain fails closed. It never guesses a substitute
provider or silently falls back to OpenCode. Approved remediation selections
and quota recovery remain bound to their selected Codex/ZCode profile and must
pass the current runtime allowlist again before any new session.

Native queued admission publishes identity before result polling. Every session
card includes a copyable URL in its text and the same URL in its button:
`/#/session/<encoded harness:session-id>`. Agent Herder accepts legacy links and
preserves explicitly linked sessions absent from the quick active list.
