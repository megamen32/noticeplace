# NoticePlace executor report — secretary recovery full cycle

Executor: MiniMax worker, native session `mvs_2b489b9ad2364fcaa03f4687420699a1` (same child as the L09/L10
leaf). Lead: Root `01a1653b`. Host: server-100 (`roomhacker@95.165.165.65`, ssh -p 22104).
Repository: `/home/roomhacker/agents-projects/noticeplace`, canonical branch `main`, base commit `0543566`.
Written 2026-10-24 (server-100 local time). Zone: **NoticePlace source only**. No credentials, tokens or keys
are recorded here.

## 1. Owned paths (declared before the write)

Already owned from the earlier L09/L10 leaf:

- `notification_center/core.py`
- `notification_center/incident_call_chain.py`
- `tests/test_consumer_policy.py`
- `tests/test_incident_call_chain.py`
- `.agents/tasks/20261023-voice-call-experience/l09-l10-noticeplace-receipt.md` (historical receipt)

Newly declared for this leaf, both NoticePlace paths, bounded hunks only, no foreign hunks:

- `notification_center/http_api.py` — one reply route (`POST /v1/matrix/reply`), `http_api.py:2428-2444`
- `tests/test_http_api.py` — one route test

Report path (this file, 0600):
`.agents/tasks/20261024-secretary-recovery-fullcycle/executor-noticeplace-recovery.md`

Not owned and not touched: `notification_center/incident_call_chain.py` needed no change in this leaf (the
existing whole-chain guard already covers it), the console overlay, `universal-userio`, `deploy/`, `mcp/`,
`web/`, `bin/notify-center`, `graphify-out`, `.tmp/`, foreign worktrees and every foreign file of the
`20261024-secretary-recovery-fullcycle` task directory.

## 2. Reused, not rebuilt

- The whole-chain guard from the earlier leaf is present in canonical `main` and was **confirmed by source and
  hash, not rebuilt**:
  - `notification_center/core.py:2160` `consumer_policy_is_matrix_only()`,
    `:2186` `incident_is_matrix_only()`, `:2195` `channel_denied_by_matrix_only_policy()`,
    the funnel guard at `:2209` and the audit `delivery_denied` at `:2212`;
  - `notification_center/incident_call_chain.py:154-170` `advance()` refuses a non-Matrix successor and audits
    `chain_advance_denied`;
  - commit `d1cf189355219bdc115ad55eb95f9bfbc29f99b8` ("L10 repair: an explicit Matrix only policy constrains
    the whole incident"), verified as an ancestor of `main` with `git merge-base --is-ancestor d1cf189 HEAD`;
  - the historical receipt `.agents/tasks/20261023-voice-call-experience/l09-l10-noticeplace-receipt.md` (0600)
    with the L09/L10 evidence, the L10 live safety-stop and the prepared event.
- The id `34744B` could not be found in this repository (no commit, file or document matches it); the reused
  base is therefore the verified `d1cf189` plus the receipt named above.
- Source contract and test data: the existing QA consumer `consumer_7ad31d4110d744259bde4ee7580890e4` and the
  two-bot room `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` with
  `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com` and `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`.
  Nothing of the historical consumer `consumer_2dea1f679372443eaa2c757088e5fc70` and none of its event,
  incident or delivery was replayed, and no new consumer or authorization was created.

## 3. What this leaf changed (source only)

Two invariants, both enforced in the source and both proved by a regression.

### 3.1 No exit from the explicit test Matrix route (call, retry and advance chain)

An explicit Matrix stage that names a room is the operator route of that recipient. Every Matrix delivery of
that consumer must stay in that room, and only the declared identities may be addressed there.

- `notification_center/core.py:2209-2243` — `consumer_matrix_scope()`: the explicit route of a consumer
  (`room_id`, `participants`, `target`, `account_ref`) read from its root Matrix stage.
- `notification_center/core.py:2245-2252` — `incident_matrix_scope()`: the route that owns an incident.
- `notification_center/core.py:2254-2273` — `matrix_route_scope_denied()`: a Matrix delivery is denied when its
  room is not the declared room, or when its callee is not one of the declared participants. An unscoped
  delivery — the one the incident call chain would create — is denied as well.
- `notification_center/core.py:2275-2280` — `_delivery_denial_reason()`: one place that names the reason.
- `notification_center/core.py:2282-2291` — the scheduling funnel denies the delivery and audits
  `delivery_denied` with `reason=matrix_route_scope`. Because the funnel is the single creation point of a
  delivery, the call, the repeat and the advance chain are all covered by the same check.

### 3.2 Reply → incident binding, only inside the route

- `notification_center/core.py:2812-2856` — `bind_matrix_reply()`: the answer must arrive in the room that the
  explicit route declares and from an identity that route declares (`participants` or `target`). A foreign room
  or an undeclared identity raises `ValidationError` and binds nothing; a late answer on a closed incident is
  refused without resurrecting it. A bound answer acknowledges the incident
  (`acknowledge_if_active(incident_id, "matrix:<answerer>")`) and is audited as `matrix_reply_bound` with the
  actor `matrix:<answerer>` and only the sha256 of the answer text.
- `notification_center/http_api.py:2428-2444` — `POST /v1/matrix/reply`: the transport route. It authorizes the
  incident with the bearer token (`authorize_incident`) and then applies the route checks; a foreign room or an
  undeclared answerer answers `400`, an unauthorized token answers `401`. The token proves access to the
  incident; the room and the participant list are what prove the answer.

Live blast radius (read-only, dqlite mesh): six live generic Matrix stages exist, and every one of them
already carries its own `room_id` in the stage target, so no live delivery changes. There is no legacy
(`platform IS NULL`) Matrix stage, so the legacy shape keeps the product defaults.

## 4. RED → fix → GREEN (actual output)

RED, measured with the new tests against the pre-fix source (`git stash push -- notification_center/core.py
notification_center/http_api.py`, tests kept in the worktree):

```
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_delivery_may_not_leave_the_explicit_matrix_route
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_binds_only_inside_the_explicit_route
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_from_a_foreign_room_or_identity_never_binds
FAILED tests/test_incident_call_chain.py::ChainTests::test_scoped_matrix_route_denies_the_unscoped_voice_chain_episode
FAILED tests/test_http_api.py::HttpApiTests::test_matrix_reply_route_binds_inside_the_route_and_refuses_outside
5 failed, 65 deselected in 7.87s
```
with the concrete causes `AttributeError: 'NotificationCenter' object has no attribute 'bind_matrix_reply'`
and, on the HTTP seam, `AssertionError: 200 != 404` (the route did not exist).

Fix: the source changes of §3, applied to `notification_center/core.py` and `notification_center/http_api.py`.

GREEN, after the fix (the fix was restored and hash-verified with `sha256sum -c` right after the RED run):

```
$ python3 -m pytest tests/test_consumer_policy.py tests/test_incident_call_chain.py tests/test_http_api.py -q
70 passed in 137.27s (0:02:17)
```

Regression beyond this leaf, measured on the same tree:

- `tests/test_delivery_worker.py` alone → **69 passed**. Six failures of this file were seen in an earlier
  combined run under load; they do not reproduce in isolation and they are not caused by this change.
- `tests/test_incident_outcomes.py` alone → **3 failed, 11 passed**. The three failures
  (`test_failed_job_after_ack_has_terminal_outcome_and_copyable_session`,
  `test_plans_ready_has_real_choices_and_does_not_claim_fixed`,
  `test_session_cards_are_not_duplicate_alarm_cards_for_plan_migration`) are exactly the ones the pre-change
  baseline already had (`test_incident_outcomes.py:231` asserts a health reconciliation counter that is 0 in
  this tree); they are pre-existing and unrelated to this leaf.

New tests:

- `tests/test_consumer_policy.py:403-624`
  - `test_matrix_delivery_may_not_leave_the_explicit_matrix_route` — an unscoped `matrix.message` delivery and
    the unscoped voice chain episode are both refused, the incident keeps only its scoped `matrix.message`
    delivery, and two `delivery_denied` audits carry `reason=matrix_route_scope`.
  - `test_matrix_reply_binds_only_inside_the_explicit_route` — a reply from
    `@lhc_voice_tester_b_20261021` in the scoped room binds the incident: `state=acknowledged`,
    `acknowledged_at` set, one `matrix_reply_bound` audit with the actor
    `matrix:@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`; a late answer does not bind again.
  - `test_matrix_reply_from_a_foreign_room_or_identity_never_binds` — a reply from the room
    `!SGgCRDRJRnTYTQvKAG:…` and a reply from `@bezrabotnyi:chat.bezrabotnyi.com` are both refused, the
    incident stays `open`, and no `matrix_reply_bound` audit exists. The same test also refuses a reply for an
    incident of a consumer that has no explicit Matrix route at all.
  - `test_matrix_repeat_stays_inside_the_explicit_route` — the repeated delivery of a `max_repeats=2` policy
    keeps the declared room and is not denied.
- `tests/test_incident_call_chain.py:464-503` `test_scoped_matrix_route_denies_the_unscoped_voice_chain_episode`
  — a scoped Matrix call keeps one scoped delivery, the unscoped chain episode is denied, a rejected receipt
  schedules no `telegram.call`, no `android.phone.call` and no `phone.call`.
- `tests/test_http_api.py:56-134` `test_matrix_reply_route_binds_inside_the_route_and_refuses_outside` —
  `POST /v1/matrix/reply` binds inside the route (`200`), and answers `400` for a foreign room and for an
  undeclared answerer.

## 5. Source hashes (post-fix, canonical main worktree)

```
651bbd5a240198d0a23f453b8d916ab67623e0ed27eade5df3f4862700f4e0e7  notification_center/core.py
75764dcb0d8128f8382e55498af788d03d47b324063cb80b759f278212939e07  notification_center/http_api.py
a5b6c7f99a7c58474991167c96a55b89d410dbed9a40fcc6debea38d6c0c305c  notification_center/incident_call_chain.py
0ffeb26e75c7c46dc6a30c50f22e39661bca0aef75929841f25265ad0cbf39a0  tests/test_consumer_policy.py
f13c3f6ee5473693902d8923c77f119a9c5ef2ae52e7cf7818ef7e1b4cd497b5  tests/test_incident_call_chain.py
f2c48d26483bdd2c50adf5d54f0958a71895e12929c48d1a4972c4aa8fbfd3d5  tests/test_http_api.py
```

Diff size of this leaf: `core.py` +123/-2, `http_api.py` +17, `tests/test_consumer_policy.py` +215,
`tests/test_http_api.py` +79, `tests/test_incident_call_chain.py` +40.

Line anchors of the new tests: `tests/test_consumer_policy.py:403` (route scope), `:453` (reply binds),
`:510` (reply refused), `:578` (repeat stays inside the route);
`tests/test_incident_call_chain.py:464` (scoped chain episode denied);
`tests/test_http_api.py:56` (reply route).

## 6. Status

- **Source READY**: the reply → incident binding and the no-exit-from-the-explicit-route invariant are
  implemented, regression-proved (RED → GREEN) and scoped to NoticePlace source paths.
- **Live acceptance NOT RUN**: no event was posted, no delivery was created, no Matrix call was placed, no
  contact was created, no SQL was written, and no production rollout was performed. The live release
  `/opt/noticeplace-releases/20261023T091306Z-b1944cd2b39c-2269751` still runs the previous code and was not
  restarted.
- **Resource join PENDING**: the live acceptance needs the UserIO side of the isolated room. The existing live
  QA consumer `consumer_7ad31d4110d744259bde4ee7580890e4` declares the room only, so a live reply binding
  additionally needs the two participants in that policy; the release exposes no consumer update path
  (`notification_center/admin.py` has only `create_consumer`), so that policy change is an owner-side item
  together with the room resource join.
- The room was not taken and blocks nobody: the actual 88 session heard by UserIO continues independently, and
  this leaf touched no voice runtime, no S21 lease, no UserIO or ExManager code.

## 7. Not done

- No new event, send, call, contact, ACL, role or user. No SQL write to the live mesh. No cron, daemon or
  timer. No service restart and no production rollout. No owner phone call, no owner or personal Matrix
  account touched, no owner identity in the isolated room. No foreign path modified: the plan, task and report
  files of the other executors, the console overlay work of another session, `bin/notify-center`,
  `graphify-out`, `.tmp/`, the `*.bak-mavis-*` files and the foreign worktrees are untouched. The historical
  consumer `consumer_2dea1f679372443eaa2c757088e5fc70` and its event, incident and delivery were not replayed,
  and no unknown or accepted result was repeated.
