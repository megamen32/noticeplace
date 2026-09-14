# Health remediation deadline must cover the bounded Hermes job

Original request: run one newly authorized controlled remediation attempt for
the existing selected Health plan after the Agent-Herder observation repair.

Objective: eliminate the proven 90-second NoticePlace helper deadline that
caused the prior remediation to fail at 93,244 ms while its Hermes CLI job was
authorized to run for 20 minutes. The helper must preserve a strict terminal
receipt requirement; a longer wait is not a success condition.

Business canary: a profile may configure a remediation wait deadline matching
the bounded Hermes job. The delivery does not fail at the diagnosis deadline
while useful progress and eventual independent verification are still possible.

Confirmed scope: `notification_center/agent_job_helper.py`, focused helper
tests, and the allowlisted health job profile schema. A real attempt is not
requeued until the fix is tested, reviewed, deployed, and its current profile
is verified.

Explicit exclusions: no Telegram send, no remediation requeue/launch, no
Hermes egress, no secret read/change, no production restart/deploy in this
implementation step.

Initial active estimate: 20 minutes.

## План (Russian)

1. Воспроизвести применение 90 секунд к remediation в focused test.
2. Добавить отдельный ограниченный timeout remediation, по умолчанию 20 минут.
3. Проверить тестами, review и затем отдельно развернуть перед one-shot canary.

## Execution log (English)

- 2026-08-11: Read-only source and service preflight found no timeout override
  in the Notification Center unit. `_run_health_remediation()` uses
  `diagnosis_timeout_seconds` with a default/max of 90/300 seconds, while the
  Agent-Herder user service sets `HERMES_HEALTH_TIMEOUT_MS=1200000`.
- 2026-08-11: This exact mismatch is consistent with the prior failed retry's
  elapsed time of 93,244 ms. The authorized one-shot attempt is held pending a
  red-first correction; launching it unchanged would be avoidable repetition.
- 2026-08-11: Red first: a remediation profile with `diagnosis_timeout_seconds`
  of 5 and `remediation_timeout_seconds` of 1200 failed at the diagnosis
  deadline before its second terminal poll.
- 2026-08-11: Added bounded `remediation_timeout_seconds` (default 1200,
  maximum 1800) while preserving diagnosis's short deadline. Green evidence:
  `pytest -q tests/test_agent_job_helper.py tests/test_health_workflow.py
  tests/test_http_api.py` -> 51 passed.
- 2026-08-11: Fresh Reviewer `019feef9-533e-71c0-9528-442c3dc3b0c9`
  returned `APPROVE` and separately ran 12 helper tests.
- 2026-08-11: Fleet deployed the committed helper backup-first and verified it
  on server-100; no external Telegram send occurred. A single authorized
  requeue created attempt 3 (`hermes-job-6c0b139a-5950-4333-9fc0-d794eb0a276f`).
  It produced 27 messages but then remained on the same useful-progress
  fingerprint for over a minute at `Initializing agent`; supervisor stopped it.
  The durable delivery ended `failed`; worker behavior subsequently showed
  attempt 4 returning the same failed Hub job without a strict receipt. No
  `health.resolved` exists. No further requeue or Hermes launch is authorized
  by this task; investigate idempotency/reclaim behavior before any retry.

## Review (English)

- Verified the helper now uses `remediation_timeout_seconds` instead of
  `diagnosis_timeout_seconds` in
  `notification_center/agent_job_helper.py:524-563`, with the requested 20
  minute default and a 30 minute cap.
- Verified the allowlisted profile normalization accepts the new field in
  `notification_center/agent_job_helper.py:60-61`, and the example profile in
  `docs/health-agent-jobs.example.json:14-25` advertises the same key.
- Verified the focused regression test covers the deadline split in
  `tests/test_agent_job_helper.py:229-274`.
- Confirmed the relevant test slice passes locally:
  `pytest -q tests/test_agent_job_helper.py` -> 12 passed.

APPROVE

Unverified assumptions: production deployment and the later one-shot canary are
still intentionally out of scope for this implementation review.
