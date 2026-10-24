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


## 8. REVISION after the independent review — SOURCE-FIX-NEEDED / REVISE (2026-10-24, second pass)

Lead: Root `01a1653b`, review `advisor-noticeplace-recovery-review.md` (id 11548B), verdict
SOURCE-FIX-NEEDED/REVISE, `770d95e` not releasable. The whole-chain guard was **confirmed by the review and
was not rewritten**; only the remaining reply binding was repaired, and the policy update seam was added.

### 8.1 FINDING 1 — the body was the authority; now the binding comes from the notice

The revoked route trusted `incident_id`, `room_id` and `answerer` from the body, and the consumer bearer only
proved the project, so a producer could acknowledge a real notice for itself. The repair follows the model the
product already uses for answers (`human_request_for_reply` resolves the target from the sent message record
and `allowed_actors` decides who may answer, `core.py:1364-1400`): the reply is resolved against the notice
that NoticePlace itself sent.

- `notification_center/core.py:2827-2918` — `bind_matrix_reply(reply_event_id, reply_to_event_id, sender,
  room_id, answer)`. The binding is resolved from the notice: the delivery record of the sent Matrix message
  (`deliveries.result_json.event_id`, the receipt the Matrix sender already returns at
  `http_api.py:706-709`), its incident, its consumer and the explicit route of that consumer. The body cannot
  name an incident, a room or an incident id at all.
- `notification_center/core.py:301-309` — `matrix_reply_receipts` with `reply_event_id` as the primary key:
  one answer event is consumed once. The receipt insert, the acknowledgement and the audit happen in one
  transaction; a repeat hits the primary key and returns the idempotent result without a second
  acknowledgement, without a second event and without a second audit.
- `notification_center/core.py:2218-2253` — the route scope now carries the declared `senders` next to the
  room, the participants and the callee. Only a declared sender may answer; the callee (`target`) is not an
  answerer by itself.
- `notification_center/http_api.py:2428-2458` — `POST /v1/matrix/reply` now requires the **transport
  capability** `NOTIFY_MATRIX_REPLY_TOKEN` (`hmac.compare_digest` against the bearer), rejects a body that
  carries anything but `reply_event_id`, `reply_to_event_id`, `sender`, `room_id`, `answer`, and answers
  `403` when no capability is configured, `401` for another bearer, `400` for every denial. A denial never
  acknowledges anything.

Boundaries now enforced, each with a test:

| boundary | behaviour | test |
|---|---|---|
| producer bearer | `401`, incident stays `open` | `tests/test_http_api.py:102` |
| capability not configured | `403`, nothing acknowledged | `tests/test_http_api.py:139` |
| body names the incident | `400`, the field is refused | `tests/test_http_api.py:120` |
| undeclared sender (owner identity included) | `400`, incident stays `open` | `tests/test_http_api.py:161`, `tests/test_consumer_policy.py:549` |
| foreign room | `400`, incident stays `open` | `tests/test_http_api.py:161` |
| unknown notice | `400`, nothing acknowledged | `tests/test_consumer_policy.py:549` |
| route without declared senders | `400`, nothing acknowledged | `tests/test_consumer_policy.py:565` |
| answer for another consumer route | `400`, incident stays `open` | `tests/test_consumer_policy.py:607` |
| the same answer event again | `200 idempotent`, one audit, one delivery, no second event | `tests/test_consumer_policy.py:577`, `tests/test_http_api.py:161` |

### 8.2 FINDING 2 — a narrow admin owned policy update seam

The live QA consumer `consumer_7ad31d4110d744259bde4ee7580890e4` declares only the room, so it has no sender
binding, and the release had no way to change a policy. The seam is now in the source and is not applied live.

- `notification_center/core.py:2920-2968` — `replace_consumer_policy(consumer_id, policy, actor)`: the consumer
  keeps its id, its intake token, its project and its severity; only the stages are replaced, the policy goes
  through the same validator as creation, a builtin profile is refused, and the change is audited as
  `consumer_policy_updated`.
- `notification_center/admin.py:664-687` — `AdminConfigStore.update_consumer_policy(consumer_id, policy_json,
  actor)`: the audited admin operation, same shape as `create_consumer`.
- Tests: `tests/test_admin_console.py:535` keeps identity, token fingerprint and the consumer count, and proves
  the old intake token still works after the update (no new grant, no new consumer);
  `tests/test_admin_console.py:587` refuses a builtin profile, an unknown consumer, invalid JSON and an invalid
  policy.

No policy was changed live, no consumer was created, no SQL was written.

### 8.3 RED → fix → GREEN (actual output)

RED, the new tests against the committed vulnerable state `770d95e`
(`git stash push -- notification_center/core.py notification_center/http_api.py notification_center/admin.py`,
the tests kept in the worktree):

```
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_binds_only_to_the_notice_it_answers
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_is_consumed_once_and_never_acks_twice
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_needs_a_declared_sender_in_the_route
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_needs_a_known_notice_and_a_declared_sender
FAILED tests/test_consumer_policy.py::ConsumerPolicyTests::test_matrix_reply_never_binds_across_consumers
FAILED tests/test_http_api.py::HttpApiTests::test_matrix_reply_route_binds_inside_the_route_and_refuses_outside
FAILED tests/test_http_api.py::HttpApiTests::test_matrix_reply_route_refuses_a_producer_bearer
FAILED tests/test_http_api.py::HttpApiTests::test_matrix_reply_route_requires_the_transport_capability
FAILED tests/test_admin_console.py::AdminConsoleTests::test_consumer_policy_update_keeps_identity_and_replaces_stages
FAILED tests/test_admin_console.py::AdminConsoleTests::test_consumer_policy_update_refuses_builtin_profiles_and_invalid_policy
10 failed, 1 passed, 56 deselected in 16.42s
```
with the concrete causes `AttributeError: 'NotificationCenter' object has no attribute 'bind_matrix_reply'`
(the old body driven signature), `AttributeError: 'AdminConfigStore' object has no attribute
'update_consumer_policy'`, and on the HTTP seam the old route answering `200` where the attack cases expect
`401`/`400`/`403`.

Fix: the source changes of §8.1 and §8.2. The fix was restored right after the RED run and verified with
`sha256sum -c` (all three sources `ЦЕЛ`).

GREEN:

```
$ python3 -m pytest tests/test_consumer_policy.py tests/test_incident_call_chain.py tests/test_http_api.py tests/test_admin_console.py -q
100 passed in 148.13s (0:02:28)
```

### 8.4 Provenance proof

The acknowledgement is no longer derivable from a caller supplied claim:

1. The bearer must be the transport capability `NOTIFY_MATRIX_REPLY_TOKEN`; the producer consumer bearer is
   rejected with `401` (`http_api.py:2434-2441`).
2. The body may not name an incident; only the reply fields are accepted, everything else is `400`
   (`http_api.py:2442-2445`).
3. The binding target is the delivery record of the notice NoticePlace sent
   (`deliveries.result_json.event_id = reply_to_event_id`, `core.py:2849-2857`), so the incident, the consumer
   and the route are server facts, never caller claims.
4. The sender must be in the `senders` of that route and the room must be the route room
   (`core.py:2870-2881`); an owner identity is not a declared sender of any scoped route.
5. The answer event is consumed exactly once by the primary key of `matrix_reply_receipts`
   (`core.py:2874-2890`), so a replayed receipt cannot produce a second acknowledgement or a second event.

What is still trusted: the transport capability holder attests that the Matrix event exists and who authored
it, because only the transport holds a Matrix session of the scoped room. That trust boundary is the UserIO
seam of §8.6 and is deliberately not implemented on the NoticePlace side.

### 8.5 Source commit, paths and hashes

Source commit: **`a43ef988380ce330f43152bed57eb28e5ac835bd`** ("NoticePlace: the reply binding is resolved
from the notice, never from the body"), author `Mavis <Mavis@local>`, canonical `main`, `git commit --only`,
exactly six paths:

1. `notification_center/core.py` (`+181/-47`; `:301-309`, `:2218-2253`, `:2827-2918`, `:2920-2968`)
2. `notification_center/http_api.py` (`+26/-13`; `:2428-2458`)
3. `notification_center/admin.py` (`+22`; `:664-687`)
4. `tests/test_consumer_policy.py` (`tests/test_consumer_policy.py:493`, `:531`, `:549`, `:565`, `:577`, `:607`)
5. `tests/test_http_api.py` (`tests/test_http_api.py:56`, `:102`, `:120`, `:139`, `:161`)
6. `tests/test_admin_console.py` (`tests/test_admin_console.py:535`, `:587`)

Hashes of the published state:

```
0580ea6726e18834690f9639e820968b5b1faa0f0c1abbb2db04230334cb0f9d  notification_center/core.py
bce59806006e42e443512df91da5cf5f365589ee51ba583574d4fd934e8b6593  notification_center/http_api.py
62ef243c65a7896a57d44d303d429d8faa718063e62a87513d34272d19e474e6  notification_center/admin.py
19f2617cb5999e51445c9826022978bb98b09c011501d11711d77fbd5934f6b1  tests/test_consumer_policy.py
12a9beceea3ba13990ddd83770dd438df5c187d11f50b524e7e7ef201a358aab  tests/test_http_api.py
d4ce9bf021eb2426c283e74221e8595161cd8b65b37469fd511fb2df4f0a92fe  tests/test_admin_console.py
```

The revoked body driven contract was removed from the tree: the tests of the previous revision that encoded it
were deleted, not weakened.

### 8.6 Typed minimal contract for the UserIO dispatcher seam (executable subtask)

NoticePlace side is ready and needs no more source. The transport side must emit one receipt per answer. This
is the whole contract:

```
POST http://127.0.0.1:8091/v1/matrix/reply
Authorization: Bearer <NOTIFY_MATRIX_REPLY_TOKEN>       # transport capability, never a consumer bearer
Content-Type: application/json

{
  "reply_event_id":    "$<event id of the answer in the room>",
  "reply_to_event_id": "$<event id of the NoticePlace notice that it answers>",
  "sender":            "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com",
  "room_id":           "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com",
  "answer":            "<optional text>"
}

200 {"bound": bool, "idempotent": bool, "incident_id": str, "delivery_id": str,
     "state": str, "actor": "matrix:@...", "reply_event_id": str}
400 {"error": str}   # unknown notice, foreign room, undeclared sender, no declared sender, bad body
401 {"error": str}   # the bearer is not the transport capability
403 {"error": str}   # NOTIFY_MATRIX_REPLY_TOKEN is not configured on the NoticePlace side

Transport obligations:
  - `sender` is the authenticated author of the Matrix event, not a client supplied string;
  - `reply_to_event_id` is the event id of a message NoticePlace sent in that room (it is in the receipt
    `deliveries.result_json.event_id`, and the notice text carries `Source: <correlation_id>`);
  - one receipt per answer event; a retry of the same event id is idempotent;
  - the receipt is emitted only for an event that the transport read from the scoped room.

Failure types the transport must distinguish and report without emitting a receipt:
  reply_event_not_in_room | sender_not_in_room | notice_event_not_found | room_scope_mismatch
  | capability_missing | transport_unavailable
```

NoticePlace prerequisites for the live join, both owned by Root:

1. `NOTIFY_MATRIX_REPLY_TOKEN` must be set in the live service environment (`/etc/notification-center.env` for
   `noticeplace-mesh-api.service`). While it is unset the route denies everything, so the live surface is safe
   by default.
2. The policy of `consumer_7ad31d4110d744259bde4ee7580890e4` must gain its sender and target bindings through
   the new admin seam (`AdminConfigStore.update_consumer_policy`). Until then a reply of that route is denied
   by design, because the route declares no sender.

The console route that calls the admin seam is not part of this leaf: the canonical console source belongs to
the console owner (L14). The caller contract is one audited call, `update_consumer_policy(consumer_id,
policy_json, actor)`, with the policy stage target
`{"room_id": "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com", "senders": ["@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com",
"@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"], "target": "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"}`.

### 8.7 Status

- **Source READY**: the reply binding is resolved from the notice, the capability is the transport one, every
  cross consumer, cross project, cross incident and cross route case denies, one answer event is consumed once,
  and the narrow policy update seam exists. RED -> fix -> GREEN is proved (10 failed -> 100 passed).
- **Live acceptance NOT RUN**: no event was posted, no delivery was created, no Matrix call or message was
  sent, no contact, role or consumer was created, no policy was changed live, no SQL was written, no service was
  restarted and no production rollout was performed. The live release still runs the previous code.
- **Resource join PENDING**: the UserIO dispatcher of §8.6 plus the two NoticePlace prerequisites of §8.6.
- The room was not taken, the 88 session heard by UserIO was not touched, and no S21 lease, voice runtime or
  foreign path was modified.
