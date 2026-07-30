# System notification center MVP

## Task Layer

Description: Implement the first deployable notification-orchestrator vertical slice beside the existing Notify MCP package: authenticated event intake, durable incident/collapse state, explicit ACK/resolve, policy-driven Telegram escalation, external health endpoint, and an independently deployable vpn2 watchdog design.
Severity: CORE
workflow: architecture(Adviser) -> contract-and-core(Worker) -> health-watchdog(Worker) -> verification(Lead) -> review(Critic) -> commit(Lead)
estimated min-max complete time: (min: 45m, max: 2h)
Acceptance: local POST event returns a stable incident; repeated dedup_key collapses into it; ACK and resolve cancel pending escalation; failed Telegram transport is retried safely; /health reports readiness without secrets; automated tests cover the state transitions; vpn2 watchdog deployment contract makes a direct, independent alert on primary failure.

## On Start

started (UTC+3): 2026-07-30T03:45:01+03:00
Executor: L (/root)
PID: Codex desktop
Harness: codex
session identifier: 019fb077-261f-7521-8f96-6fb99e3e9e89
Next action: parallel implementation: protect the external readiness probe, harden the vpn2-independent direct-alert watchdog, and close the ACK/escalation race; Lead integrates and verifies all slices.

## Notes

- User-directed exception: the repository has no ROADMAP.md; use `mcp/notify` as the existing notification-related project.
- Existing README/docs/assets changes are unrelated user work and must be preserved.
- User proposed the correct failure-domain boundary: an external health endpoint probed by a second center on vpn2, which sends direct notifications and does not depend on the primary center.
- Chosen baseline: dependency-light Python (`sqlite3`, stdlib HTTP) rather than an npm service or premature Go rewrite. Matrix calling stays an adapter boundary until real Matrix credentials/runtime are verified.
- Delegation (2026-07-30 UTC+3): `core_endpoint` owns health-token/API proof; `vpn2_watchdog` owns direct vpn2 watchdog hardening; `architecture_tests` owns ACK/escalation lifecycle proof. Lead owns integration, security review, and acceptance.

## Blocker

none

## Result

Pending.

## Completion checklist

- [ ] Every selected workflow stage is complete or its omission is explained.
- [ ] Acceptance is proven with exact commands, immutable artifacts, or paths.
- [ ] Blockers are resolved or explicitly retained.
- [ ] Result contains the full handoff and does not depend on a delivered agent message.
- [ ] `work-20260730-notification-center-mvp.md` moved to `done-20260730-notification-center-mvp.md` and committed.
