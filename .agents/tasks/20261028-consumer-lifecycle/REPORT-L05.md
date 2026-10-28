# L05 — independent integrated review

Review scope: published NoticePlace `34214f36bbeb75afc4f5360202e728ecfc95e014`, Herder `4a8815cf1593f2e211ff8d24d1d4f8f2b5f5b832`, followed by Herder `cd2bb9f13f6ec5ded079ef59b71d5e350bec2cf7`; console SQL and active consumer adapter seams. No live incidents, deliveries, credential changes or deployment were performed by this reviewer. Temporary in-memory/domain fixtures were removed on exit.

## P1 — a definitively rejected stale source check permanently blocks verification

Owner: `notification_center/consumer_lifecycle.py`, `SourceVerifier.tick`.

Reproduction uses the real NotificationCenter and SDK: pull a due verification request; before its POST, a concurrent ordinary source `health.degraded` event rotates nonce/generation; the POST is rejected as stale. The persisted client intent remains `submitting`. The next two ticks fail exact-receipt reconciliation permanently: one POST, one due request, incident still open. This differs from an accepted or UNKNOWN request and must not cause replay of the rejected POST.

Repair L19 was reviewed: only the exact HTTP 400 stale-generation response persists `rejected_stale`; a later authoritative GET must establish the same source/project/host and a superseding nonce/generation before a fresh request can be probed. Other 400s, 5xx and timeouts stay fenced. The added integration regression covers the actual source-event/check ordering; guards retain lost-accepted healthy/degraded receipt handling. Source delta is ready for scoped publication; installed verification remains outstanding.

## P1 — finishing the latest coalesced episode strands older episodes

Owners: `notification_center/incident_lifecycle.py` and its session-attempt binding boundary with `notification_center/agent_job_helper.py`.

Reproduction: create two health episodes with distinct dedup/correlation IDs but equal consumer/project/host/disk group. The helper's current shared native-session attempt points at the latest incident. Finish that latest attempt, then answer every due source request with a fresh healthy observation. Actual result: equal group keys; only one due request; latest incident resolved; earlier incident remains open with `verification_due=None`. The shared AP pauses on the latest explicit finish, so another attempt does not repair that missing verification eligibility.

Required correction: finishing the actually shared attempt must make all relevant open coalesced episodes eligible for independent source checks. Do not blindly resolve a group. Preserve original signal/source authorization and exclude other hosts/types. Degraded responses across those episodes must produce only one durable continuation for that shared attempt/session. Root assigned this correction to `group_completion`; delta review pending.

## Other examined boundaries

- Authenticated DB consumer lookup retains current DB project/severity/revocation authority before supplemental verifier grants; verifier and executor credentials remain separate.
- Production Herder observer uses the NoticePlace attempt receipt, validates session/harness/attempt, and pauses AP with an atomic attempt comparison rather than HumanStop. Current attempt incident overrides the immutable original incident for source checks.
- Herder `cd2bb9f` prevents automatic OpenCode recovery from resuming an explicitly paused NoticePlace attempt; explicit rearming remains possible. Independent targeted router/recovery run: 18 tests passed. One selected NP suite exceeded the review's 20-second subprocess bound without returning a failure; it is not counted as a pass.
- Console grouping precedes pagination, informational TTL uses producer event timestamps, and source verification status remains visible instead of inventing healthy resolution.
- Examined Fleet wrapper, AutoSell capacity and TelegramAuto verification paths require fresh measurements and preserve unknown for unsupported/unmeasured conditions. Fleet adapter surrounds the installed base rather than requiring replacement of the foreign edited probe.

## Direction / method check

The extended duration came primarily from discovering additional real consumers and resolving their authorization/deployment boundaries. The nearest valuable next step is still one controlled installed lifecycle canary, after the two concrete P1 source corrections and coherent installation. Another general audit, framework or council would delay that result. Passing ingredient tests is not business acceptance.

## Focused L20 / L21 delta

The SDK correction was published as `63ce9c93f79f09385da3c8bbff78c04222e9e63c`. L20 `0c5143a5d481d7294519f08bca276c4e999442cd` adds per-episode verification eligibility only for actual same-native-session peers, preserving each source principal, and a shared completion ledger for one continuation. L21 through Herder `9cd3475` supplies production tri-state continuation authority: reuse the exact native session for a valid open canonical episode, block old-attempt replay, and retain normal creation for a genuinely new unbound episode. These changes address the original coalesced-episode review finding.

A remaining bounded P1 was reproduced against L20: A and B share the native attempt; A reports degraded and queues the canonical continuation; B also reports degraded; A then becomes healthy before any dispatch. Resolving A cancels its continuation delivery with `status=cancelled, attempt=0`. A later fresh degraded check for B returns that same cancelled ticket, while GET B exposes no authority because A is resolved. Actual result: B remains open without an executable continuation. This is provably never dispatched and differs from accepted/UNKNOWN. Safe bounded reanchoring must recover this case while leaving accepted/UNKNOWN untouched. Sent to the L20 owner for correction.

The bounded follow-up was independently reviewed and the exact counterexample rerun successfully: a resolved canonical origin with a cancelled ticket, zero attempts, no claim timestamp and no result receipt permits a fresh sibling authority. The new ticket is queued with zero attempts, points at the still-degraded sibling, and preserves the native session. Any dispatch/claim/receipt/missing history remains fenced. This is a new request after proof of non-dispatch, not replay of an accepted/UNKNOWN operation.

Independent final Herder continuation/router run: **25 tests passed** (12 production-port continuation tests plus 13 router tests). No further concrete source integration blocker was found in this bounded delta.

Verdict: **SUPPORTED for controlled installation after publication of the reviewed follow-up**. Installed end-to-end acceptance remains outstanding. Proceed to the existing single controlled lifecycle canary; no additional audit or planning system is needed.

## Bounded release-blocker review L22

Published `43da15bd80db08a60a0a427d8f1e59bdd3e85f9f` pages the three startup migration scans by primary-key cursor with a byte preflight. Historical event identity is selected explicitly by `(created_at,rowid)`; generation/sample aggregation remains unchanged. The result frame is bounded without increasing mesh limits or skipping old incidents.

One concrete legacy-receipt regression was reproduced: the paged query filters `l.last_check_receipt_json IS NULL` and joins `l.request_id`, but processing its first page changes both fields. Multiple receipts for the same old nonce can span pages (the original lifecycle version allowed repeated unknown checks). Later pages then omit their remaining rows. Three same-request legacy receipts with 1.6 KB evidence and a 4096-byte migration page migrate `legacy:0`, while the previous ordered scan retained `legacy:2`. This can lose the final accepted continuation/exact receipt. The fix should select the latest receipt for each request before paging, or otherwise keep stable membership. Sent to the L22 owner; installation approval pending this bounded correction.

The minimal follow-up now selects `MAX(rowid)` per request using the existing request-ID index before paging. The reviewer reran the exact independent fixture: `legacy:2` is retained as expected. The owner's three focused tests also cover final unknown/degraded receipts, exact continuation/evidence, all three bounded scans and additive upgrade. Other paging paths are unchanged. **SUPPORTED for controlled 100-first installation after publication of this reviewed two-path follow-up**; this remains source readiness, not installed acceptance. Do not repeat the 188,599-event production read scan or the full lifecycle suite without a new concrete concern.
