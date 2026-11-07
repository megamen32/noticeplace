# Smart Edge consumer / SDK handoff

Scope: `health-monitor`, source `router-haproxy-watchdog`, host `router`, signal
and problem `https`, correlation `router-haproxy:<episode>`, stable dedup
`router-haproxy:lan-smart-edge`. The existing lifecycle family groups by project,
consumer, host and problem; correlation binds the exact episode. The normal timer
remains the sole scheduler. Source verifier reads a fresh SSH HAProxy status and
HTTPS HEAD after three successful samples, checks exact episode and resolved
receipt, and leaves an ambiguous request fenced. Legacy episodes remain legacy.

Production flags are deliberately off pending the Lead's Notice Place release.
The existing producer credential has verifier role, a principal,
`verification_owned_sources=true`, `agent_jobs` including `health-diagnosis`,
and `automatic_health_diagnosis=true`. There is no separate verifier token;
the adapter can reuse the same source principal credential, never executor
credentials. Source token scope has no `suppress_owner_delivery`, while current
runtime `telegram_all_notifications_enabled=0`; this combination was tested
only against an isolated loopback instance, not by producing a live incident.
Recheck live settings, scopes, exact native executor route and quiet policy at
cutover. SDK package and source verifier import must be installed before
`NOTICEPLACE_TYPED_HEALTH=1`; `--check-config` prevents typed submission if
SDK import or source credential is absent. Never replay unknown sends.

A loopback HTTP test uses the real NoticePlace API, source/executor scopes,
SQLite fixture, SDK source code and synthetic probes; an isolated wheel import is verified separately. It verifies one queued
executor delivery, no owner-facing initial delivery, work-finished not closing,
then fresh healthy source receipt closing. The executor completion is simulated;
no real executor session, message or call was launched. Production integration,
grant/readback and real independent consumer proof remain separate.

## S09 seam for Lead

Current `GET /v1/verification-requests` accepts `project` and `source_id`; the
HTTP handler does not pass `limit`, cursor or wait, and the store returns the
first 20 due checks ordered by `verification_due` with no stable tie-breaker.
SDK `max_checks` and admission deadline defer work without dropping it but cannot
page past 20 unrelated due episodes. Add an authenticated stable keyset cursor
on `(verification_due, incident_id)` bound to project/source/principal,
validated limit (1–100), and bounded optional wait with a deadline. Return
`requests`, `next_cursor`, and explicit cursor expiry; keep old calls compatible.
The cursor must never acknowledge or change a check: an unaccepted request stays
due, and accepted/UNKNOWN `check-result` must reconcile by the existing exact
receipt. Do not use remote callback URLs or retry an UNKNOWN POST. For expired
or unavailable sources, a server-owned deadline must mark verification unknown,
retain the incident open and schedule at most the existing bound session, never
invent a healthy observation or a second executor. Core/http/incident_lifecycle
are Lead/Luna-owned and are not modified here.

## Registry inventory

Before adding SmartEdge the registry recorded 26 entries: 11 installed_active,
4 source_published_inactive_not_enabled, 2 source_published_install_pending,
6 central_notification_compatibility, 2 qa_not_production_producer, and 1
executor_not_source. These are registry claims, not fresh runtime verification;
server88 discovery was previously partial. Source-only/inactive entries:
health-incident-monitor, omniroute-guard, router-health, codex-daemon-watchdog;
install-pending: video-selfheal-producer and agent-herder. Notification-only,
security notifications, QA and executor-only entries must not be promoted to
health verifiers merely because they are in the registry. Each active adapter
needs its owner, scope and live version verified before any migration.

## S08 context seam for Lead

The typed SDK currently sends the existing supported identity fields and
`observed_at`; the existing event contract does not expose an agreed bounded
source context object for impact, symptoms, last-good, diagnostics, permitted
action, verifier reference and freshness. Agree a versioned allowlisted shape,
apply server-side secret redaction and size limits, persist it, and pass an
explicit `incomplete` state to the executor when required fields are absent.
Do not treat producer-supplied diagnostics or callback locations as authority
for commands, and do not claim the fields reach the executor merely because
the v1 event JSON stores them. This requires the Lead-owned core/HTTP/executor
seam; no competing context schema was inserted into the client.
