---
phase: quick
plan: 261014-incident-call-chain
type: execute
wave: 1
depends_on: []
autonomous: true
files_modified:
  - notification_center/core.py
  - notification_center/http_api.py
  - bin/notify-center
  - notification_center/incident_call_chain.py
  - tests/test_incident_call_chain.py
must_haves:
  truths:
    - A new eligible incident rings Matrix first, Telegram only after a durable safe no-answer, and cellular only after Telegram's durable safe no-answer.
    - Answer, ACK, resolution, uncertain admission and unknown call outcome stop advancement without automatic replay.
    - Existing severity, call kill switch, quiet hours, cross-episode interval and quiet health suppression remain effective.
  artifacts:
    - path: notification_center/core.py
      provides: Transactional unique chain advancement and predecessor-scoped call reservation
    - path: notification_center/http_api.py
      provides: Single-root scheduling and tri-state call outcome handling on the real worker path
  key_links:
    - from: bin/notify-center
      to: DeliveryWorker
      via: Validated INCIDENT_CALL_ORDER configuration and installed adapter factories
    - from: complete_delivery
      to: _reserve_problem_call
      via: Persisted compact receipt, exact chain predecessor and shared budget last_delivery_id
---

<objective>
Implement the user's explicit incident call order: Matrix → Telegram → cellular, advancing only after the previous call definitively ended unanswered. Use the existing delivery queue and shared mesh transactions. Root integrates and deploys NoticePlace; UserIO worker owns its real call helper/endpoints/tests. This planner owns only this PLAN.md and runs no provider, device, test or deployment action.
</objective>

<context>
@.planning/PROJECT.md
@.planning/REQUIREMENTS.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.agents/tasks/incident-call-budget-outcomes-20261006.md
@.agents/tasks/noticeplace-mesh-ha-20261013.md
@.agents/tasks/20261013-quiet-repair-first.md
@notification_center/core.py
@notification_center/http_api.py
@bin/notify-center

Existing planning describes the separate Secretary slice; preserve its unfinished acceptance and phase position. This quick task implements the newer delegated incident-chain mandate, not a replacement roadmap. Preserve peer-owned quiet-health changes, unrelated WIP, all historical queue IDs and uncertain/sending rows. Known runtime is the three-node mesh with an immutable quiet-progress overlay; root must verify current live identity before deployment.

Locked decisions: D-01 exact Matrix → Telegram → cellular order; D-02 one durable root and one next stage after safe no-answer; D-03 kill switch/severity/quiet hours/shared interval/no-replay retained; D-04 use supported UserIO call seam, no parallel calls or old queue migration.
</context>

<tasks>
<task type="auto">
  <name>1. Wire explicit order and definitive call receipts on production adapters</name>
  <files>notification_center/http_api.py, notification_center/incident_call_chain.py, bin/notify-center; UserIO worker-owned helper/endpoints/tests</files>
  <action>
Implement D-01/D-04. Validate INCIDENT_CALL_ORDER against exact supported unique channels; use Matrix, UserIO Telegram call, existing cellular adapter. Wire the actual bin/notify-center environment factory and worker_loop consumer. Explicit order requires all needed adapters; an unavailable stage must not silently skip to cellular. When enabled, initial eligible Telegram card completion schedules only the stable Matrix root, including critical/emergency Health without defeating quiet-health suppression. Preserve existing severity eligibility and escalation timing settings; do not independently schedule cellular in _after_telegram_delivery. Keep ordinary Telegram card repeats compatible.

Preserve provider tri-state receipt: answered=true is actual answer; answered=false advances only with explicit terminal unanswered status plus safe_to_fallback=true after worker/call teardown; answered=null, uncertain=true, timeout, invalid/lost response or unknown admission is uncertain. UserIO must produce those states from its real helper. MatrixCallSender and WebhookCallSender currently coerce null/missing to false and discard safe_to_fallback; fix those relevant transport projections. Return a compact bounded valid receipt. An accepted/started receipt is insufficient to advance. After crossing reserve_delivery_send, every call branch must persist uncertain on ambiguous exception, not route through generic retry. Actual answer stops the chain even if actor is missing; acknowledge only with a valid actor, and preserve the answer receipt.
  </action>
  <verify>Focused integration through real DeliveryWorker and authenticated helper route with fake provider: no provider action for missing adapter, null/unknown/timeout stops, explicit safe no-answer advances, answer stops. No real calls in source tests.</verify>
  <done>Production environment and bounded worker consumer use one configured order and preserve safe/no-answer/unknown distinctions end to end.</done>
</task>

<task type="auto">
  <name>2. Persist unique chain advancement and constrain same-cycle budget continuation</name>
  <files>notification_center/core.py, tests/test_incident_call_chain.py</files>
  <action>
Implement D-02/D-03 inside existing shared transactions. Freeze order, root identity, index and predecessor delivery ID in call target_json; derive stable delivery keys per root/index. complete_delivery must atomically persist the compact terminal receipt and create only the next exact stage after failed + definitive safe no-answer, while the incident remains active. Duplicate completion, concurrent workers/connections and restart cannot produce a second stage. Read durable result_json, not transient result alone. Restrict chain completion to the exact claimed/sending generation and do not overwrite an already terminal answer with a later conflicting result. No successor after sent/answered, uncertain, retry, cancellation, ACK, mute or resolution.

Exclude marked chain deliveries from both _schedule_failure_neighbours and generic successful consumer-policy successor/repeat scheduling; either path can violate the order. _reserve_problem_call may continue within the shared interval only for the exact next index/channel of the same root/incident when its predecessor equals call_problem_budgets.last_delivery_id and has a persisted definitive safe no-answer. Validate predecessor/root/order/index, not only same incident. Reject a duplicate index, different episode/root, backward stage or uncertain predecessor. Preserve bootstrap from historical attempted calls and normal interval enforcement for all new cycles/unrelated episodes. Keep one cycle's budget timestamp semantics explicit; updating last_delivery_id must not reset authority or admit parallel continuation.

Preserve call kill switch at dispatch and quiet-hour/severity checks for every cellular stage. Cancellation by policy must terminate the chain. Existing sending/uncertain work is not reclaimed or migrated by this change. Bound result serialization by projecting receipt fields before encoding; slicing a JSON string at4000 can invalidate persisted predecessor proof.
  </action>
  <verify>Focused integration: Matrix safe-no-answer → Telegram safe-no-answer → one cellular delivery; no successor before durable predecessor; Matrix answer and Telegram answer stop; unknown receipt stops; duplicate/concurrent/stale completions, restart, ACK race, quiet hours, disabled calls, hourly budget bootstrap and unrelated-episode rejection. Verify existing quiet Health decisions remain unchanged.</verify>
  <done>Only an exact durable predecessor can authorize the next stage; budget, no-replay and policy guards remain enforced across the real store boundary.</done>
</task>

<task type="auto">
  <name>3. Integrate owned changes and prove the installed consumer without replay</name>
  <files>Owned NoticePlace paths and root-owned evidence/task record; UserIO deployment belongs to its worker/root agreement</files>
  <action>
Root reviews combined receipts/interface and focused regressions, records measured finite build/test budgets, commits only owned paths on main and pushes. Preserve peer source/index bytes. Build the actual immutable runtime with latest quiet-progress source and explicit chain environment; inspect installed source identity and effective configuration on100/44/88. Preserve mesh data, credentials, limits and all sending/uncertain IDs; deploy through existing controlled release route. Do not infer runtime readiness from canonical source alone.

Run one new authorized controlled incident through the public production intake and real bounded worker. Recipient-visible evidence must show Matrix first; if unanswered, Telegram next; cellular only after Telegram definitive no-answer. Capture durable stage receipts and provider teardown to prove ordering. A separate answer case must prove no later calls. Do not replay old consumers, retry UNKNOWN/provider admission, change quiet hours or bypass disabled calls. Required physical S21 interaction uses its lease; runtime/user policy governs real call authority. If a call outcome is uncertain, retain it, stop that consumer, and report the concrete acceptance boundary.
  </action>
  <verify>Three categories only: fast unit (configuration/receipt classifier, expected10s/max100s); focused integration (queue/store/worker/helper behavior, expected60s/max600s); slow nightly is not required for this bounded change. Real consumer scenario expected180s/max1800s including setup, recording actual elapsed/unfinished coverage. Root adjusts expected values to current bounded runner measurements before execution, preserves project/host reserves, and uses no broad unbounded suite. Tests/HTTP200 alone are not final acceptance.</verify>
  <done>Published installed chain passes the controlled real consumer and answer-stop proof, or the exact incomplete runtime/recipient boundary is retained without claiming completion.</done>
</task>
</tasks>

<verification>
Source audit: GOAL is the direct incident-order mandate; all D-01..D-04 are covered above. Existing PHONE-01/PHONE-03 belong to the Secretary phase and are preserved; PHONE-02 incident compatibility is verified here. No separate RESEARCH/CONTEXT source artifact was supplied; current source and delegated contract are the evidence, not fabricated requirements.

Critical pre-change findings: http_api._after_telegram_delivery independently schedules Matrix and phone, and Health currently skips Matrix. core.complete_delivery fans failed/retry/uncertain to previous/next methods; generic sent policy stages can also advance. Generic call results are treated as sent regardless of unanswered/unknown. Matrix failure drops its receipt; Matrix/Webhook adapters collapse missing/null answered to false. Generic post-reservation exceptions retry calls. Shared one-hour budget currently rejects legitimate immediate fallback, so continuation must be exact and predecessor-proven. Result JSON slicing can destroy durable proof. All are in the owned execution path, not permission for runtime forks or unrelated changes.
</verification>

<success_criteria>
1. Exactly ordered Matrix → Telegram → cellular, with one stage at a time and definitive durable no-answer gating.
2. Answer/ACK/resolve/unknown stop; no replay, backward neighbour call, duplicate stage or independent phone schedule.
3. Severity, quiet health, quiet hours, operator call switch and cross-episode interval are preserved.
4. Real installed provider/recipient evidence verifies the new behavior; source-only green is described honestly as incomplete acceptance.
</success_criteria>
