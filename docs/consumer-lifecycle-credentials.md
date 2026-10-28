# Protected credential cutover preparation

L09 supplies `deploy/consumer-lifecycle-credentials.py`; it does not perform a
production cutover automatically. Default invocation is read-only preview:

```
sudo python3 deploy/consumer-lifecycle-credentials.py --plan <reviewed-plan.json>
```

The reviewed plan contains exactly the existing project set. Each project needs
explicit globally distinct source/executor principal names and exact registered
source IDs plus an optional boolean `verification_owned_sources` (an empty list
without that opt-in denotes notification-only producer authority):

```json
{"projects":{"example":{"source_principal":"example-source","executor_principal":"example-executor","verification_sources":["exact-source"],"verification_owned_sources":true}}}
```

Do not infer consumer IDs from project or producer labels: consumer identity
continues to come from the original authoritative producer-token scope. The
original token value remains unchanged for the existing producer environment;
its scope gains its source principal and allowed source IDs. The callback map
gets a separately generated executor credential without verification authority.
Producer routing, max severity, automatic-diagnosis flags and QA settings are
preserved. Existing callback compatibility projects are not silently dropped.
No credential or complete protected file is printed.

Before apply, the Lead must stop new launch admission and stage all API nodes
with the published source. `--prepare-dir <new-protected-candidate-directory>` generates the credential map
once without changing either live file. Before any apply, preflight all three
replicas with `--check-prepared <candidate-directory>` (and `--scope-only` where
there is no callback): their canonical token-map hash must match the prepared base, including
source scope compatibility. `--apply-prepared <same-candidate-directory>
--backup-dir <new-protected-directory>` writes
both files with their existing owner/mode, saving mode0600 backups and a CAS
rollback manifest first. This is two individually atomic file replacements,
not a cross-process or cross-host atomic transaction: orchestration must fence
launches and coordinate restart/readback. Use `--scope-only` on an API replica without a local executor callback. Each
local environment keeps its host-specific port, DB and unrelated settings: only
its credential-map line is patched. A different base map is an explicit source
collision and no file is written. No service is restarted by the helper.
Generate executor credentials once for the coordinated fleet release, and install
the identical protected scoped map on all API replicas according to the owning
mesh contract. Do not run independent credential-generating apply invocations
on separate nodes and assume the resulting maps match.
On write failure it restores only bytes installed by this invocation; later
concurrent edits are preserved and require operator reconciliation. `--rollback --backup-dir <same>`
restores only if the current hashes still match this helper's applied versions;
it refuses to overwrite another owner's later changes. Store the backup under
protected project state, not a tracked directory. Never regenerate independently per replica: every node consumes the identical
protected candidate. An already migrated or divergent map is rejected.

`scripts/sync-health-callback-tokens.py`, invoked by deploy/start, selects only
explicit independent executor scopes once any v2 source/executor scope exists.
An incomplete executor map aborts before changing the callback file; it cannot
silently restore a verifier credential into the executor callback. Before v2
cutover the legacy selection behavior remains available.

For UNKNOWN check-result admission, GET incident lifecycle now exposes the last
check receipt (request ID, idempotency key and result). SourceVerifier clears its
intent only when exact request/key/generation/state match. A missing/different
receipt preserves the fence and never replays POST.

Dynamic source IDs derived from video URLs use the reviewed opt-in
`verification_owned_sources: true` on an explicit verifier principal. The server
permits checks only for episodes whose source principal was bound from the
same authenticated token at intake. Body actor/source labels cannot transfer
ownership; another principal with the same project/source gets no due rows and
cannot check the incident. Exact lists remain for reviewed historical unbound
episodes. Executor credentials never carry either verification grant.

L07/L08 supply actual producer environment paths. L06 enables the adapter's
`NOTICEPLACE_CONSUMER_V2=1` opt-in only after shared helper installation/import
readback and scope cutover. `NOTICEPLACE_VERIFIER_TOKEN` may override; otherwise
the adapter reuses its unchanged existing producer event token after registration.
This helper does not rewrite arbitrary producer environment files. L06 owns live
atomic apply and the muted integration canary.

## Read-only fleet preflight: 2026-10-28 14:12:55 UTC

Direct LAN SSH with fresh hostname identity found a source collision. No real
candidate or configuration file was written. Canonical sorted JSON SHA256 of
`NOTIFY_CENTER_TOKENS_JSON`:

| Host identity | Scope/project count | Normalized hash | Explicit automatic true/false |
|---|---:|---|---|
| roomhacker-server-100 | 26/26 | `94c2d6d75d477a87c0e8758ce4ea02f6d833d57bf17a85b4b8928b7207ca3a18` | 9/1 |
| server-44 | 25/25 | `4bdd90215d92c1e977095f3578739cb3c32409a0dec81b5e7275e25265db497f` | 9/1 |
| roomhacker-server-88 | 25/25 | `4bdd90215d92c1e977095f3578739cb3c32409a0dec81b5e7275e25265db497f` | 9/1 |

All scopes are still legacy. The differing scope identity/content was not
printed or inferred in this first probe. The exact difference was subsequently
identified by Lead; the reviewed narrow normalization is recorded below. Do not
overwrite the 44/88 environment with the 100 environment or suppress the helper's
collision fence.

## Reviewed normalization and registered-consumer follow-up

Lead's read-only comparison at 2026-10-28 14:16 UTC identified the exact map
mismatch: all 25 shared credential hashes and every shared scope are identical
on 100/44/88. Only the existing `integration` scope is absent on 44/88. It has
`max_severity: important`, no agent jobs and no explicit automatic diagnosis.
This is not a conflicting health policy or permission to replace host settings.

Preparation may use `--allow-missing-base-project integration`. It computes
exactly two permitted base hashes: the full canonical map, and that same old map
minus its single existing integration credential. No arbitrary hash or skip-CAS
option exists. The exception rejects every other project, active health grants,
agent jobs, automatic diagnosis or a severity above important. A replica with
any additional token/scope difference still fails before writes. The identical
candidate then forward-aligns the reviewed existing integration scope together
with the scoped credential transition, without an extra API restart.

The ATV sender in `/etc/default/autotelegramviews` uses a DB-authenticated intake
token, not one of the 26 legacy configured bearer scopes. Its confirmed identity
is `consumer_b85a89f421694cdd9213c7cdf873e991`, project `autotelegramviews`. Do not
rename `atv-views`, copy the intake token into config, or invent a public policy
key usable as a bearer. [The reviewed registration fragment](consumer-lifecycle-registration-plan.json)
contains only this public consumer identity and policy, with no credential.

Merge that fragment into the final reviewed credential plan. The supplemental
`consumer_grants` list is written as `NOTICEPLACE_CONSUMER_VERIFIER_GRANTS_JSON`
in the existing protected API environment. It grants only explicit exact
consumer/project identities. The API first authenticates the actual token hash
against its consumer DB on every request, preserving revocation, stored project,
consumer ID and maximum severity; only then does an unambiguous verifier grant
supply `consumer:<stored_id>` source ownership. No bearer is created or copied
and the callback map never receives this policy list. An executor entry, claimed
principal, unknown field or foreign-project identity is rejected by preparation.
Existing unrelated supplemental policy is preserved; a differing grant for an
already registered consumer requires explicit reconciliation. No other registered
consumer, QA actor or voice account is automatically enrolled.

Candidates include both `base_grants_hash` and `candidate_grants_hash`. Check/apply
requires the local supplemental policy to match its prepared base as well as an
allowed credential-map base; arbitrary local policy differences remain fenced.
The same single candidate patches both environment lines on every replica while
preserving each host's port, database and unrelated settings.

A follow-up read-only sender-class check confirmed
`/home/roomhacker/.local/state/autoseller-noticeplace.env`'s
`NOTIFY_CENTER_TOKEN` matches the existing configured `autosell` scope, so its
adapter reuses that scoped token. `/etc/default/autotelegramviews` uses the actual
variable name `NOTIFY_CENTER_TOKEN`; its token is absent from the legacy map,
consistent with Lead's independently verified DB consumer identity above. No raw
credential or credential fingerprint was emitted. This check does not classify
or enroll every other registered consumer.

## Trusted launch identity and explicit work completion

The GPTAdmin envelope preserves only server-stored lifecycle group consumer and
problem identity plus the stored consumer reference. Repair instructions prefer
`incident.lifecycle.group.consumer_id`; an actual registered `consumer_*` ID is
the fallback. Builtin notification routes (`profile_important`/`profile_emergency`)
fall back to `project+source_id`, so increasing the severity of one CPU signal does
not split it into different repair owners. Producer-claimed consumer labels are
not used. The authoritative group problem type is preserved; only its absence
uses the canonical signal mapping (for example load to CPU).

Both direct and signed GPTAdmin dispatch derive an executor `attempt_id` from the
server's delivery idempotency key and job, hashing it into bounded metadata. A
retry of the same delivery retains the attempt; a later delivery gets a different
attempt. Repair instructions explicitly require `noticeplace_work_finished` or
protected `/health/work-finished` with that attempt, actual native session/harness
and evidence references. A text answer or idle observation is not completion.
No source-health verdict, source verification credential or resolve authority is
added to the executor instructions. The legacy remediation JSON path is unchanged.

## Concrete release input and remaining TGC migration

`docs/consumer-lifecycle-reviewed-plan.json` supplies all 26 existing legacy
project grants, stable source/executor principals, explicit historical source
IDs, the single reviewed ATV supplemental registration and the registered-only
`autotelegramviews` executor callback project. That extra credential has only
project, notice ceiling, executor role and a distinct principal; it cannot emit a
critical source event or verify recovery. Its actual session callback and
work-finished HTTP paths are covered by an isolated local fixture.

One reviewed winramp source ID includes a URL and is deliberately absent from
Git. The public plan contains its exact SHA256 reference. Preparation must use
`--resolve-reviewed-source-refs`; without it the helper fails closed. Resolution
reads canonical state through a read-only connection, matches only the reviewed
project/source hashes, and hydrates the raw value in memory for the protected
candidate. It never prints the value or writes a separate secret file. The
bounded query refuses more than 10,000 distinct candidate source records. No
prefix, wildcard or unreviewed source is enrolled.

The concrete plan was successfully checked using the real protected environment
and read-only canonical state in **preview mode only**: 26 existing project
scopes plus one executor-only callback project, with no candidate directory,
configuration change, service restart or producer event.

The same read-only inventory identified an additional active registered
consumer requiring a separate bounded migration:

- Project `tg-commentator`, consumer `consumer_1df8b4668ab24b89b88859f1929d2880`.
- Confirmed wire identity: source `tgc-engine-watchdog`, host `server-100`, signal
  `engine`; 27 active episode snapshots observed.
- Producer: `/home/roomhacker/PycharmProjects/TelegramAuto/TG_Commentator/scripts/tgc_engine_watchdog.py`,
  `send_notice`/`resolve_notice`, using
  `/home/roomhacker/PycharmProjects/TelegramAuto/TG_Commentator/notify_center_client.py`.
- Missing transition: work-finished → source poll → fresh `check_tgc_execution`
  and `check_tgc_read_path`/engine decision → typed check-result. The existing
  `resolve_health` emits a legacy untyped resolve request, not a v2 source check.
- Next bounded work: migrate that real producer to the shared verifier client,
  confirm its protected sender binding, then explicitly register this exact DB
  consumer and test the matching engine episode. No grant for it is added by
  the current ATV-only plan. Its existing intake token is not verification
  authority by itself. Do not replay its old accepted or uncertain operations.
