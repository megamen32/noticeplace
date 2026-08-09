# Mandatory full-cycle supertest

This is the required business canary before a presentation, release, or
production routing change. A green unit suite, service status, dashboard, or
historical delivery row is not a substitute for this test.

## Contract

One unique correlation id must be traceable through the complete path:

```text
source webhook
  -> GPTAdmin authenticated webhook route
  -> NoticePlace agent-job wrapper
  -> Agent Herder new-or-resume
  -> selected executor (Codex, OpenCode, or Hermes)
  -> Notify event/incident receipt
  -> policy scheduling and delivery worker
  -> S21 phone adapter
  -> Android cellular call state
```

The executor must be the component that emits the Notify event. A direct
adapter call is a phone-transport test only and does not satisfy this gate.

## Required evidence

The run is green only when all of these are present for the same correlation
id, with secrets and phone numbers redacted:

1. GPTAdmin webhook accepted the signed request and completed its shell action.
2. Agent Herder returned an accepted/resumed session for the selected harness.
3. The executor produced a Notify event or incident receipt.
4. Notify created the incident and a phone delivery with the expected policy.
5. The delivery worker completed the phone adapter call successfully.
6. The S21 reports the cellular call as `DIALING` or `ACTIVE`.
7. The incident is acknowledged/resolved and no duplicate call remains queued.

Every stage must fail the test explicitly. `200`, `active`, a dashboard page,
or an old `sent` row without this correlation id is insufficient.

## Safety

- The default mode is dry-run and must not call a phone.
- Live mode requires an explicit operator confirmation at execution time, for
  example `--live-call CONFIRM-NOTICEPLACE-SUPERTEST`.
- Use a unique idempotency and dedupe key per run.
- Do not change quiet hours or global call settings as part of the test.
- If quiet hours suppress the policy call, the result is `NOT_CONFIRMED`, not
  green; a separately authorized one-off live call must be marked as such.
- Preserve a compact receipt bundle with timestamps, correlation id, stage
  statuses, and redacted errors.

## Current baseline

The previously run GPTAdmin canary proved the webhook-to-Codex handoff, and a
separate live adapter canary proved S21 dialing. Neither alone satisfies this
supertest; the combined executor-to-Notify step remains mandatory.
