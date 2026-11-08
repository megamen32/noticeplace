# Smart Edge consumer / SDK handoff

Scope: `health-monitor`, source `router-haproxy-watchdog`, host `router`, signal
and problem `https`, correlation `router-haproxy:<episode>`, stable dedup
`router-haproxy:lan-smart-edge`. The existing lifecycle family groups by project,
consumer, host and problem; correlation binds the exact episode. The normal timer
remains the sole scheduler. Source verifier reads a fresh SSH HAProxy status and
HTTPS HEAD after three successful samples, checks exact episode and resolved
receipt, and leaves an ambiguous request fenced. Legacy episodes remain legacy.

The current Smart Edge source requires a separate verifier credential:
`NOTICEPLACE_VERIFIER_TOKEN` must be nonempty and different from the producer's
`NOTICEPLACE_HEALTH_TOKEN`. The typed-submission guard enforces this before
ingress and runs the executable verifier's `--check-config`; the verifier itself
requires `NOTICEPLACE_VERIFIER_TOKEN`, `NOTICEPLACE_URL`, and an installed SDK
exporting `SourceVerifier` and `NotificationCenterClient.emit(source_context=...)`.
Source refs:
`/home/roomhacker/ServersAdministartion/ops/router-health-lite/router-haproxy-watchdog:203`
and `ops/router-health-lite/router-haproxy-source-verifier:112`, checked against
infrastructure HEAD `7295c36b091bcd39606b5c9798ffc20c8a7a567f` on 2026-11-08.
Do not substitute the producer or executor credential for this verifier token.

The Lead's prior live read found both credential keys in
`/etc/health-incident-monitor.env` and no `NOTICEPLACE_TYPED_HEALTH` key there.
That is an environment-file observation, not proof of token distinctness,
effective scope, process environment, typed-mode activity or a successful
lifecycle. This worker did not read secret values or change that environment.
The earlier role/quiet-setting observations are historical, not current
acceptance. Verify effective producer/verifier grants, installed SDK and guard,
explicit typed opt-in, native executor route and quiet policy at cutover; then
use independent receipt/readback evidence. Never replay unknown sends.

A loopback HTTP test uses the real NoticePlace API, source/executor scopes,
SQLite fixture, SDK source code and synthetic probes; an isolated wheel import is verified separately. It verifies one queued
executor delivery, no owner-facing initial delivery, work-finished not closing,
then fresh healthy source receipt closing. The executor completion is simulated;
no real executor session, message or call was launched. Production integration,
grant/readback and real independent consumer proof remain separate.

## S09 source contract and mixed-version rollout

The current HTTP endpoint already forwards `limit`, `cursor` and `wait_seconds`.
Server keyset cursors use `(verification_due, incident_id)`, are bound to
project/source/principal and bearer, and expire after 300 seconds. The page size
is 20 in the SDK, independently of `max_checks`. A tick reads at most 100 pages.
`sweep.json` checkpoints the cursor and the handled request IDs within a partial
page under the existing verifier lock; a later timer tick or process restart
continues that sweep. The new server advertises `cursor_resume_supported`:
`cursor=resume:<signed-cursor>` restores the signed read position after cursor
expiry, with current bearer, project, source and grant checks. It is not a
verification nonce, measurement or cached authorization. Normal cursors still
expire. This permits progress even when the existing timer interval exceeds
300 seconds. On an old server without this capability, an expired/rejected
cursor restarts the read-only sweep; progress past the page cap across such a
long interval requires the server update. Neither path retries POST.

The SDK rejects a foreign project/source response but skips a different host
before invoking the probe. `accept_group` still selects the exact episode.
An exact consumer/source verifier grant covers named principals as well as
historical credential and consumer principals. Pull and submit use the same
independent-source policy; another consumer/source and executor are not granted.

New requests expose `verification_requested_at` and `verification_deadline` as
server epoch seconds. The deadline is 300 seconds after the due time. All new
results must arrive before that deadline. When a healthy/degraded result includes
`observed_at`, it must be finite and positive, measured after the request was
issued (5 seconds of clock skew allowed), and not in the future beyond that
skew. An explicit null/invalid timestamp is rejected, never treated as absent.
The supplied timestamp is included in the immutable idempotency payload.
Unknown is never evidence of recovery.

Legacy SDKs omit `observed_at`. Their authenticated source assertion remains
accepted inside the server-issued 300-second window. This is bounded trust in
the source verifier, not proof of absolute measurement freshness: the server
cannot distinguish a cached sample when the old client supplies no timestamp.
Expiry fences legacy and new clients equally. Do not claim stronger legacy
freshness than this contract provides.

The new SDK records the start of the fresh probe as `observed_at`; existing
`probe(group) -> {observed_state, evidence_refs}` adapters need no signature
change. Adapters that read timestamped measurements must return their actual
`observed_at`; they must never relabel a cached sample as a fresh probe.
Expired requests and rejected stale measurements become `unknown`, advance the
generation and get a new request/window. This commits before the existing
`stale verification request or generation` HTTP 400, so the SDK can reconcile
supersession on its next tick. Accepted receipts, session bindings and existing
continuations are preserved; expiry never resolves an incident or launches a
second executor. Pull and submit perform this expiry check. This change does
not install an independent timer; unattended deadline/continuation scheduling
in an existing server tick remains a Lead-owned S09-2 integration boundary.

The Lead's live snapshot is server100=a7d1392, server44/88=eb8deb7; all three
will receive one artifact. Consumer SDKs are installed independently, including
pip and Docker copies. This server change therefore does not require a fleet
SDK cutover first. It preserves old healthy/degraded payloads inside the TTL;
the updated SDK adds a timestamp also tolerated by the old server. Publish and
update SDKs through each consumer's existing owner/deploy route, with installed
readback. The emitter API and probe signature are unchanged. Existing accepted
receipts without timestamps remain reconcilable; do not resend them with a
timestamp added. Old pending requests retain their original age through the
additive schema migration and are rearmed if expired. A future mandatory
timestamp mode would require an explicit consumer migration and is not enabled
here. No package was published or installed by this scoped source fix. The old
live QA canary is open/unknown with one sent executor; it must not be replayed.

## Scoped RED / GREEN evidence (2026-11-08)

The isolated regressions live in `tests/test_source_verifier_reliability.py`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_source_verifier_reliability.py -v
```

- Before the first fixes: four regressions, three failures and one host-mismatch
  error (named grant, max_checks/page size, resumed sweep and foreign host).
- Before freshness fencing: delayed healthy closed the incident, invalid
  timestamps were accepted, and a seven-day-old request remained due.
- Before numeric bounds: JSON `observed_at=10**400` raised `OverflowError`.
  The guarded result is now `ValidationError`, mapped by HTTP to 400.
- Final focused run: 19 tests passed. It includes legacy timestamp-free payloads,
  expired cursor resume/current grant revocation, partial-page restart, and the
  actual SDK intent transition after expiry HTTP400: `rejected_stale` on disk,
  new SDK instance, incident readback, fresh request/generation, fresh probe,
  new POST identity. No accepted/UNKNOWN operation is replayed.
- Final combined isolated run: 91 tests passed (consumer lifecycle, incident
  lifecycle, source pagination, public SDK, credential grants and the new
  regressions; socket HTTP/MCP cases excluded). Existing direct lifecycle
  fixtures still use their original timestamp-free payloads.

All fixtures use temporary SQLite and in-process transports. No live incident,
executor, owner message, call, deployment, package publish or commit was made.

## Final source snapshot

Base HEAD: `a7d139293b35789853d01eade01d658188c7ea3c`. The owned source/test
snapshot below is uncommitted in the existing implementation worktree. Only
this handoff changed after the final tests; the separate HTTP-error unit also
passed (92 isolated tests total). Unrelated worktree changes are not included.

| Owned path | SHA-256 |
| --- | --- |
| `notification_center/incident_lifecycle.py` | `202391ecc856ee2c30a5426851d2e8637aa2425217d188683a17caed4ce4843b` |
| `notification_center/consumer_lifecycle.py` | `a32c04d25b19097681b69fc5e7dd29a46cc22fb345a23bba2a41018c5a8e73f6` |
| `tests/test_consumer_lifecycle.py` | `fa2c6fe1ce45b2b1fa677d77b176825e7c18b94c8e342b6c7da2d941b4539445` |
| `tests/test_source_verifier_reliability.py` | `7640d705fb0c35efbeee5681b696454b02f06a72499121d34472799121d796d1` |

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

## S08 acceptance boundary

The SDK now accepts `source_context` (`notify.source-context.v1`) alongside
typed identity and event `observed_at`. This is distinct from the measurement
timestamp on check-result. Server persistence, redaction, incomplete context and
executor consumption belong to the Lead's integrated acceptance. This worker
changes neither core nor HTTP nor executor policy. Source unit proofs do not
establish an installed SDK or a real native executor/fresh-source closure.
