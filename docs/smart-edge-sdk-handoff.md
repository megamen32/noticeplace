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

The historical pre-cutover read found both credential keys in
`/etc/health-incident-monitor.env` and no `NOTICEPLACE_TYPED_HEALTH` key there.
The subsequent protected preparation confirmed that the existing producer and
verifier credentials differ, without printing either value. Preparation itself
did not change the live environment. Delivery receipts are recorded below.
Presence of keys alone does not prove effective API scope,
process environment, typed-mode activity or a successful lifecycle.
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
second executor. Pull and submit perform this expiry check. The integrated source also calls
`incident_lifecycle.expire_verification_requests(limit=20)` from the existing
`core.run_history_retention_tick`, reached through `http_api.run_task_decision_tick`
and the existing worker. The maintenance interval is 60 seconds with a 90-second
runtime lease; no second scheduler is introduced. Expiry marks verification
unknown and rearms the request, never fabricates a source receipt or closes an
incident. A bound finished attempt gets at most one continuation.

`agent_job_helper._resume_source_continuation` reads the exact native
`/api/sessions/{harness}/{session_id}/details`, checks identity and absolute cwd,
rechecks owner STOP, then calls that session's `/resume` with
`humanRequested=false` and a stable input ID. The operation identity binds the
incident, finished attempt, harness and session rather than rotating verification
requests. Missing/refused/UNKNOWN resume never falls back to a new session.
The preparation evidence includes 3 deadline and 9 exact-resume isolated tests;
this establishes source behavior, not a live native-session continuation.

The earlier Lead snapshot was server100=a7d1392, server44/88=eb8deb7. Backend
`0ad9afe218ce1a9a2a47501e18f2f017c7fbfae0` is now installed on all three;
`20261108-integrated/release-0ad9afe218ce/receipts.json` records authenticated
readiness and the five module hashes. Consumer SDKs are installed independently, including
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

Those original fixtures used temporary SQLite and in-process transports, with
no live effects. Subsequent publication, delivery and independent QA evidence
are separate below.

## Historical verifier-fix snapshot

Base HEAD: `a7d139293b35789853d01eade01d658188c7ea3c`. The owned source/test
snapshot below records the original bounded fixes before the Lead's subsequent
S09 deadline integration. It is not the current integrated file identity. The
separate HTTP-error unit also passed (92 isolated tests total at that point).
The original manifest/logs remain in primary evidence `20261108-verifier`;
current release identities belong to the Lead's final manifest.

| Owned path | SHA-256 |
| --- | --- |
| `notification_center/incident_lifecycle.py` | `202391ecc856ee2c30a5426851d2e8637aa2425217d188683a17caed4ce4843b` |
| `notification_center/consumer_lifecycle.py` | `a32c04d25b19097681b69fc5e7dd29a46cc22fb345a23bba2a41018c5a8e73f6` |
| `tests/test_consumer_lifecycle.py` | `fa2c6fe1ce45b2b1fa677d77b176825e7c18b94c8e342b6c7da2d941b4539445` |
| `tests/test_source_verifier_reliability.py` | `7640d705fb0c35efbeee5681b696454b02f06a72499121d34472799121d796d1` |

## Registry inventory and S17 active migration queue

Before adding SmartEdge the registry recorded 26 entries: 11 installed_active,
4 source_published_inactive_not_enabled, 2 source_published_install_pending,
6 central_notification_compatibility, 2 qa_not_production_producer, and 1
executor_not_source. These are registry claims, not fresh runtime verification;
server88 discovery was previously partial. Source-only/inactive entries:
health-incident-monitor, omniroute-guard, router-health, codex-daemon-watchdog;
install-pending: video-selfheal-producer and agent-herder. Notification-only,
security notifications, QA and executor-only entries must not be promoted to
health verifiers merely because they are in the registry.

The later bounded live inventory supersedes those stale activity labels. The
accepted M01–M13 priority map is in primary evidence
`20261108-migration-priority/{priority-migration-map.md,delivery-recipes.md,evidence.json}`.
It selects four actual enabled adapter gaps, rather than migrating all26 entries:

- OmniRoute M02: retained-incident correlation fix published in infrastructure
  main `6d836672b8285330f350a5af0fe3714713d9e146`; delivery evidence below.
- GPTAdmin CI, Kepler-owned: published `4f385b124c73e1b8dc7f13c7c7fd099e53d5f55e`,
  source-executed file hash verified, 63 isolated tests passed, ordinary timer
  completed successfully at15:46UTC. Exact current-request handling and fresh
  aggregate CI checks replace legacy recovery. Read-only list access returned
  HTTP200 with no due requests; no live closure is claimed. Evidence:
  `20261108-gptadmin-consumer/RESULT.md`, `delivery.json`, `public-ci-final.json`.
- AutoSell, Turing-owned: build pin published `5bfd3787e82177f7f51486a78710ce60270b8ae5`;
  exact image overlay preserves the existing followup586fix and Config/HostConfig.
  Installed lifecycle module hash is `a32c04d25b19097681b69fc5e7dd29a46cc22fb345a23bba2a41018c5a8e73f6`;
  26 isolated SDK tests passed. Its natural16:00UTC cycle reached the new SDK,
  then failed at GET during API100 drain. The unchanged endpoint returned200
  at16:02:36UTC, but a successful full natural cycle remains unproven. Evidence:
  `20261108-autosell-delivery/RESULT.md`, `runtime-readback.json`,
  `verification-get-after-drain.json`. No live closure is claimed.
- ATV custom verifier: first-page-only coverage remains the unassigned active
  gap in the accepted map; no source or runtime fix is claimed here.

These delegated receipts describe their recorded snapshots; they do not imply
that this worker reran those consumers or changed registry classifications.

## S08 acceptance boundary

The SDK now accepts `source_context` (`notify.source-context.v1`) alongside
typed identity and event `observed_at`. This is distinct from the measurement
timestamp on check-result. Server persistence, redaction, incomplete context and
executor consumption belong to the Lead's integrated acceptance. This worker
changes neither core nor HTTP nor executor policy. The integrated final-delta log
records96 tests passed; `deadline-group-green.txt` records5, superseding the older
continuation review's cancelled-origin group RED. Kepler's exact-session helper
proof covers66 related tests; preparation retains3 deadline and9 exact-resume
tests. These prove source behavior, not a live automatic deadline continuation.

Turing's `20261108-live-acceptance/{RESULT.md,result.json,cleanup-readback.json}`
provides independent installed lifecycle evidence: old and new isolated incidents
resolved/healthy through fresh SourceVerifier processes and measured HTTP200;
the new episode had one admission, one delivery attempt and one actual repair
attempt in the same native session. Cleanup confirms the fixture stopped and
autopilot disabled. The existing QA delivery required an explicitly authorized
exact-row worker after remaining queued for about five minutes. This therefore
proves the lifecycle, not ordinary queue latency. Deadline/new-session acceptance
remains deferred. There were no human deliveries; the old canary was not replayed.

## S16 delivered scopes, SDK 0.2.1 and typed opt-in

Delivery finished on2026-11-08 at16:10UTC under NoticePlace lease fence16;
its release was confirmed after every API restart completed. Primary evidence:
`/home/roomhacker/agents-projects/noticeplace/.agents/review-artifacts/20261108-smartedge-delivery/receipt.json`.
Backend runtime remains the immutable published0ad9afe release. Scope delivery
reuses the existing separate verifier bearer; no credential was issued:

```json
{"project":"health-monitor","principal_id":"source:health-monitor","incident_role":"verifier","max_severity":"notice","verification_sources":["router-haproxy-watchdog"]}
```

The principal is the existing `source:health-monitor`, not the superseded draft
`source:router-haproxy-watchdog`. No owned-source wildcard, executor or automatic
launch privilege was added. Each node kept its own preexisting scopes and
consumer grants. The callback file hash was unchanged. All three process maps
matched their respective files and prepared logical candidates:

| API | Verified InvocationID | Scope SHA-256 |
| --- | --- | --- |
|100|`13a76f9c0284403886ca04c6b87759ab`|`5771fa82f7af59cff89f4ba7fb5f828062bd4c93c4c83c8e28fe4da672deb1c0`|
|44|`50f9b00934e94ca2bc3399af554216d2`|`9f7b023c316e205f5920b43f5967b1117bedbef37a4c9314f3e30792460d4029`|
|88|`01787ae4952c40699f1e772b1a64b99b`|`9f7b023c316e205f5920b43f5967b1117bedbef37a4c9314f3e30792460d4029`|

Authenticated loopback health responded in5/6/6ms with service, storage and
dispatcher ready. Overall health remained503/degraded, as in the delivery
baseline; this is not a claim of fully healthy fleet status. Env100 was tightened
from0640 to0600 for the protected helper; the old mode is recorded for rollback.

A release defect was found and repaired: unquoted JSON assignments let systemd
consume Unicode backslashes. The first100 restart produced valid JSON but changed
one existing project's name semantically. Canonical helper fix
`4aa7146586538e9577b4377acf7783017c80447c` is published, with18 focused tests GREEN
and an isolated real-systemd fixture. Both scope and consumer-grant values now
preserve JSON escapes.100 received a separate encoding-only CAS and corrected
restart after its first restart had conclusively completed; peers received only
the corrected published helper. No runtime source file was patched.

The first wrapper incorrectly released fence15 during a slow drain. That error
and the new drain-aware lease retention are recorded in
`20261108-smartedge-delivery/ENCODING-AND-DRAIN.md`, with RED/GREEN logs. No restart
was replayed after UNKNOWN and no force signal was sent. The final corrective
process maps above restore the exact intended semantics.

The installed distribution is `notify-center-client==0.2.1`. Wheel SHA-256:
`8268830f5c33640b7d0d1b3d094094e76b283f93a9c0aef53bfb2d312f3bd53e`.
Installed lifecycle module:
`a32c04d25b19097681b69fc5e7dd29a46cc22fb345a23bba2a41018c5a8e73f6`.
The SDK was installed offline by atomic directory exchange; imports, exports,
version and all three module hashes were checked. The old0.2.0 tree remains the
rollback copy, and the historical inventory is unchanged. Swap receipt:
`/usr/local/lib/router-haproxy-watchdog/.python.noticeplace-0.2.1.json`.

The conditional command verified the exact source executables, all three running
scope maps, distinct existing tokens, SDK compatibility and healthy episode state
before writing only `NOTICEPLACE_TYPED_HEALTH=1`. The protected flag backup is
`/etc/health-incident-monitor.env.pre-typed-1794154250095471326`.
The existing timer remained enabled/active. Its ordinary16:12:00–16:12:02UTC
invocation `763bbc1f7068493c8d57eb52577ac314` was observed live with typed flag1 and
distinct credentials, then completed success/0 with watchdog state healthy.
See `20261108-smartedge-delivery/natural-timer.json`. No manual tick, source probe,
outage, test event, notification or call was introduced.

Preparation evidence remains in `20261108-smartedge-preparation`:19 isolated
regressions against the wheel, SDK exchange/idempotency/rollback tests, and
conditional-cutover tests. Its original scope commands used the old helper and
are historical: use the corrected published helper identified above. Encoding
rollback on100 has two CAS layers described in `ENCODING-AND-DRAIN.md`; complete
both file restores before restarting. Never remove SDK/grants needed by a pending
typed episode. The separate infrastructure README correction remains a patch
artifact; no unrelated infra documentation was overwritten.

This establishes installed identity, effective scope, typed process opt-in and
an ordinary healthy watcher cycle. This bounded delivery did not exercise a
natural SmartEdge fresh-source closure; that production episode acceptance
remains separate from Turing's isolated lifecycle proof.

## M02 exact published guard delivery

Infrastructure source fix `6d836672b8285330f350a5af0fe3714713d9e146` preserves
verification after `end_episode` removes the top-level correlation while an
exact retained incident still exists. Identity mismatches still fail closed.
Canonical RED had one failure among5 tests; all5 passed after the minimal fix.

The exact published guard SHA-256
`b362116f0fbe7e912667227c3b0a16e941f64d2e35e150468a07ea826af60642` was installed
atomically on100/44/88 under infrastructure fence26, then read back on all three.
Only the existing executable was exchanged; state, config, units and timers were
preserved. Each node retains its exact previous executable backup. The lease was
released after installation. Evidence:
`20261108-m02-omniroute/{release-source.json,delivery-receipt.json,postflight-100.json,postflight-44.json,postflight-88.json}`.

Effective installed-policy checks allow each configured source and deny foreign
principal/consumer, executor role and self-executor. The preexisting
`verification_owned_sources=true` scope was neither widened nor narrowed.
All three ordinary timers remained enabled/active. Existing invocations that
started before replacement were preserved. New ordinary processes subsequently
started at16:13:51UTC on100,16:15:46 on44 and16:15:24 on88, with new InvocationIDs
and the installed guard in their command lines. `natural-invocations.json` and
`natural-processes.json` record those observations. The processes were still
running at readback; this proves natural admission of the installed guard, not
successful cycle completion or source closure. No forced timer, probe or event
was used to manufacture that evidence.
