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
printed or inferred. L06 must compare that exact project/scope difference
without exposing token values, reconcile the authorized scope map, then run
the prepared-candidate compatibility check. Do not overwrite the 44/88 map
with the 100 environment or suppress the helper's collision fence.
