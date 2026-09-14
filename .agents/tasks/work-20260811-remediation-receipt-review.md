# Review task: health remediation terminal receipt

Original request: довести бизнес-сценарий Health incident до выбора плана, запуска Hermes, полезного прогресса, независимой проверки и resolved receipt с elapsed time и trace IDs.

Objective: independently review the local NoticePlace helper change that makes `health-remediation` wait for Agent-Herder/Hermes terminal output and return a bounded receipt.

Business canary: after a selected Health plan, the user-facing delivery must not be reported completed merely when a Hermes session is created; it must carry the selected plan, useful progress, source/verification identities, evidence, and trace references so NoticePlace can accept or reject resolution under its independent-verification gate.

Confirmed scope: `/home/roomhacker/agents-projects/noticeplace/notification_center/agent_job_helper.py` and its focused tests.

Explicit exclusions: no production restart/deploy, no Telegram send, no secret changes, no database edits, no destructive operation, and no changes to unrelated dirty files.

Initial estimate (active minutes): optimistic 20 / likely 40 / pessimistic 90.

Review target: inspect the current diff against HEAD, run focused tests if useful, report concrete findings and PASS/CHANGES_REQUIRED. Do not edit files or reset unrelated work.

## Review revision

The first review required fail-closed parsing because incomplete terminal JSON was accepted. A red regression now asserts that a receipt without verification/source/fingerprint/verifier/evidence/trace proof is rejected. The parser was tightened to require all proof fields and distinct source/verifier identities. Focused helper suite is green: `11 passed`.

Please independently re-review this revised diff and return PASS or concrete remaining findings. Do not edit files.

## Review result

Reviewer v2: PASS. The parser requires verification/source/fingerprint/verifier/evidence/trace proof and rejects equal source/verifier identities. Focused helper plus Agent-Herder-choice coverage: `14 passed`. Incomplete-proof repro timed out instead of returning a completed receipt.

## Review evidence

- Focused tests: `pytest -q tests/test_agent_job_helper.py tests/test_agent_herder_choices.py`
- Result: `13 passed in 0.91s`
- Repro check: `run_profile("health-remediation", ...)` with a terminal assistant JSON that only contained `plan_id`, `step`, and `observed_state` still returned `status=completed` and empty `verification_id`, `source_id`, `source_fingerprint`, `verifier_id`, `evidence_refs`, and `trace_refs` except the synthetic session traces.

## Verdict

CHANGES_REQUIRED: the remediation receipt currently fails open on the independent-verification fields. It can mark the Health incident completed before the receipt carries the source/verification identities and evidence the business canary requires.
