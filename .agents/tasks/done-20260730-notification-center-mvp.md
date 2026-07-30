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
Next action: complete; source and deployment contract verified locally, live vpn2 deployment deliberately pending approved hostname and credentials.

## Notes

- User-directed exception: the repository has no ROADMAP.md; use `mcp/notify` as the existing notification-related project.
- Existing README/docs/assets changes are unrelated user work and must be preserved.
- User proposed the correct failure-domain boundary: an external health endpoint probed by a second center on vpn2, which sends direct notifications and does not depend on the primary center.
- Chosen baseline: dependency-light Python (`sqlite3`, stdlib HTTP) rather than an npm service or premature Go rewrite. Matrix calling stays an adapter boundary until real Matrix credentials/runtime are verified.
- Delegation (2026-07-30 UTC+3): `core_endpoint` owns health-token/API proof; `vpn2_watchdog` owns direct vpn2 watchdog hardening; `architecture_tests` owns ACK/escalation lifecycle proof. Lead owns integration, security review, and acceptance.

## Blocker

none

## Result

Implemented the dependency-light notification-center Tier-A MVP beside the
existing Notify MCP interface, which remains unchanged. `notification_center/`
provides bearer-scoped event intake, idempotency conflict protection, active
deduplication, SQLite incident/audit/outbox state, ACK/resolve/snooze, and
lease reclaim after a worker crash. The HTTP API exposes separately authorized,
secret-safe health readiness; its worker heartbeat and SQLite failure map to
safe 503 responses.

`deploy/vpn2/notification-center-watchdog.sh` is an independent POSIX
sh/curl failure domain: it authenticates only to the public health contract,
persists mode-0600 counters without evaluating state, recovers stale locks,
uses threshold/cooldown hysteresis, and sends direct Telegram then Matrix
failover without calling the center's database or event API. Primary and vpn2
unit/env templates and the install/drill runbook are included under `deploy/`.

Evidence: `PYTHONPATH=. python3 -m unittest discover -s tests -v` passed 23
tests; `python3 -m py_compile notification_center/*.py bin/notify-center
bin/notification-center-watchdog`, `sh -n deploy/vpn2/notification-center-watchdog.sh`,
and `git diff --check` passed. A loopback process smoke returned the expected
authenticated `/health` JSON and accepted a bearer-authenticated critical
event. Live vpn2/systemd/Telegram/Matrix evidence is not claimed: it needs an
approved external hostname/TLS route and separate real credentials.

Final Critic gate initially returned `RETHINK`: the Matrix failover method was
implicit POST and stale locks without trustworthy PID metadata could persist.
Both were repaired before close. The watchdog now explicitly sends Matrix
events with `PUT`, and uses a bounded 120-second configurable lock lease that
recovers expired locks even for missing, malformed, or PID-reused metadata,
while fresh locks still prevent overlap. New regressions assert the request
method and all four lock cases. Final evidence: `python3
tests/test_vpn2_watchdog_shell.py -v` passed 10/10; `pytest -q` passed 25;
`sh -n deploy/vpn2/notification-center-watchdog.sh` and `git diff --check`
passed. The tested watchdog artifact SHA-256 is
`1f3983f4a2b5db7096d76dc228ad6f82d260b3b16ee2139a26a0455a2593867b`.

### Authenticated core health slice (`/root/core_endpoint`)

- `GET /health` now requires a dedicated `NOTIFY_CENTER_HEALTH_TOKEN`;
  absent, wrong, and producer Bearer tokens receive the same secret-safe `401`.
- Readiness performs real SQLite queries and checks the delivery-dispatcher
  heartbeat. SQLite failure or stale dispatcher returns safe JSON with `503`;
  no exception text, path, configuration, or credential is returned.
- TDD red proof:
  `python3 -m unittest discover -s tests -p 'test_notification_center.py' -v`
  failed with two `TypeError` errors before `build_handler` accepted the health
  credential.
- Green proof:
  `pytest -q tests/test_http_api.py tests/test_notification_center.py` returned
  `12 passed`; full `pytest -q` returned `20 passed`; focused stdlib discovery
  returned `9/9 OK`; `python3 -m py_compile notification_center/core.py notification_center/http_api.py
  tests/test_http_api.py tests/test_notification_center.py` succeeded.
- Added `pytest.ini` so canonical `pytest` collection imports the local package
  without an ambient `PYTHONPATH`. The vpn2 probe must send the matching Bearer;
  that independent watchdog change remains owned by `/root/vpn2_watchdog`.

## Completion checklist

- [x] Every selected workflow stage is complete or its omission is explained.
- [x] Acceptance is proven with exact commands, immutable artifacts, or paths.
- [x] Blockers are resolved or explicitly retained.
- [x] Result contains the full handoff and does not depend on a delivered agent message.
- [x] `work-20260730-notification-center-mvp.md` moved to `done-20260730-notification-center-mvp.md` and committed.
