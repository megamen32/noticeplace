# Supervisor-stalled remediation must become non-reclaimable

Original request: deliver the health remediation business flow. The one
authorized remediation attempt stalled after useful progress stopped; it was
supervisor-stopped, then worker reclaim advanced the same delivery to attempt
4 without a strict terminal receipt.

Objective: ensure a supervisor-stalled health remediation is durably terminal
and cannot re-launch Hermes via worker reclaim. The resulting failed receipt
must keep bounded session/trace evidence and distinguish stall containment
from a successful health resolution.

Business canary: one stopped health remediation yields exactly one terminal
`failed` delivery/audit state, never a new Hermes session; `health.resolved`
is still absent until independent verification proves the original source
healthy.

Confirmed scope: NoticePlace delivery claim/completion/retry and health-agent
job integration, plus focused regression tests. No new Telegram card, no
Hermes launch, no requeue, no production deploy/restart, no secret access.

Initial active estimate: 20 minutes.

## План (Russian)

1. Найти путь reclaim после stopped Hermes session и зафиксировать red test.
2. Сделать stalled delivery терминальным, с trace и без повторного запуска.
3. Прогнать focused tests, fresh review и только затем обсуждать новый canary.

## Execution log (English)

- 2026-08-11: Real attempt 3 created Hermes session
  `hermes-job-6c0b139a-5950-4333-9fc0-d794eb0a276f`, then stalled at a fixed
  useful-progress fingerprint. Supervisor stopped it. The durable delivery
  later showed attempt 4 and duplicate `agent_job_failed` events for the same
  failed Hub job `cb30c2299f0eae989aaafe1838973616`.
- 2026-08-11: Fresh Critic `019fef06-1e61-77f1-88f7-110069a41126` returned
  `RETHINK`: prove a non-reclaimable terminal state, add red→green reclaim/stall
  coverage, review, then request a distinct approval before another attempt.
- 2026-08-11: Red first: the agent adapter observed its delivery still
  `claimed`, and no reservation prevented a 60-second worker lease reclaim.
- 2026-08-11: The worker now reserves the exact delivery generation as
  `sending` before any GPTAdmin/Hermes call; terminal completion carries the
  original lease generation. The regression asserts an expired-lease claim pass
  cannot reclaim the in-flight delivery. Focused suites passed:
  `tests/test_gptadmin_agent.py`, `tests/test_delivery_worker.py`, and
  `tests/test_http_api.py` (plus direct regression pass).
- 2026-08-11: Fresh Reviewer `019fef0c-16ac-7a71-a606-fa64ae68e7f5` returned
  `APPROVE`; no findings. It independently verified the reservation and lease
  generation completion path plus focused GPTAdmin/DeliveryWorker tests.

## Review (2026-08-11)

Evidence checked:

- `notification_center/http_api.py:682-725` reserves the exact lease generation
  as `sending` before any GPTAdmin/Hermes boundary, then completes health
  remediation with the original `claimed_at`/`attempt` so stale claim retries
  cannot relaunch Hermes or overwrite the terminal receipt.
- `tests/test_gptadmin_agent.py:351-376` exercises the regression by asserting
  the adapter observes `sending` and that an expired-lease `claim_due_deliveries`
  pass does not reclaim the in-flight delivery.
- `package.json:42` narrows the published Python payload to top-level
  `notification_center/*.py`; reviewed as packaging-only and not a blocker for
  this delivery path.

Focused verification run in review:

- `python -m pytest -q tests/test_gptadmin_agent.py -k 'configured_agent_job_adapter or completed_health_remediation_closes_only_after_independent_verification'`
- `python -m pytest -q tests/test_delivery_worker.py -k 'sent_and_sending_health_cards_block_new_plan_delivery or health_generic_telegram_policy_does_not_schedule_repeats'`

Verdict: APPROVE

Unverified assumptions:

- The broader package publish flow still includes all runtime-needed assets
  outside `notification_center/*.py`; no issue surfaced in the reviewed path.
