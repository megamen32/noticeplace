# Per-consumer delivery policy builder

Status: active
Classification: Normal (selected by user)

## Original request

Add detailed per-consumer notification configuration: create a consumer, receive a URL, configure platform targets, acknowledgement, retries, and cross-platform escalation.

## Objective

An operator can create a scoped producer and independently configure its durable delivery policy without sharing server delivery credentials with producers.

## Business canary

Create one test consumer in the operator UI, copy the one-time intake URL/token, emit an event, and prove its configured delivery sequence and ACK cancellation.

## Confirmed scope — selected Normal vertical slice

- Operator UI/API creates one consumer and reveals exactly one scoped intake URL/token.
- Consumer policy has two ordered stages only: explicit Telegram `chat_id`/optional topic immediately, then the pre-existing fixed phone adapter after a configured delay.
- Consumer policy and destination IDs are operator-only; producer event payloads cannot select channels, targets, phone numbers, commands, delays, or retries.
- ACK/resolve cancels a queued phone stage before its deadline. SQLite outbox keeps existing at-least-once delivery semantics.
- Matrix and WhatsApp are reserved target kinds in the persisted policy schema only; neither adapter nor UI configuration ships in this slice.

## Explicit exclusions

- No uncontrolled phone number or arbitrary command from producer payloads.
- No migration or mutation of existing global severity routes until an approved migration plan.
- No change to existing incidents without an explicit consumer policy; they retain the legacy global severity-to-Telegram route.

## Initial estimate (immutable)

- Optimistic: 300 active minutes
- Likely: 600 active minutes
- Pessimistic: 960 active minutes

## Initial plan (Russian)

1. Определить модель consumer, endpoint, target и escalation stage поверх текущего durable outbox.
2. Спроектировать UI создания, одноразовый credential handoff и policy editor.
3. Реализовать выбранный vertical slice: consumer token/URL, Telegram target, ACK и одна phone escalation; Matrix/WhatsApp остаются future schema kinds.

## Planned acceptance tests

1. Creating a consumer returns its token/intake URL exactly once; subsequent dashboard reads expose only a fingerprint and no route secret.
2. A consumer event produces `telegram.consumer:<id>` immediately and `android.phone.call` at the configured deadline; a fake existing `AndroidPhoneAdapter` records no call before that deadline.
3. ACK and resolve before the deadline cancel the queued phone delivery, verified with the fake adapter.
4. Event payload fields for target, platform, phone number, command, delay, retry, or stage are rejected; only operator policy chooses them.
5. A consumer policy affects only that consumer; a non-consumer event still creates the legacy `telegram.main` delivery using global severity routing.
6. Policy validation accepts `matrix` and `whatsapp` only as disabled future schema kinds; the Normal UI exposes Telegram and Phone only.

## Execution log (English)

- 2026-08-03: Confirmed the current admin supports only project tokens and global severity-to-Telegram routes; it lacks consumer-specific policy persistence and an escalation editor.
- 2026-08-04: User selected Normal. Contract narrowed after Overseer RETHINK: tests must prove one-time credential reveal, consumer stages override only that consumer, legacy global routes remain unchanged, and ACK before deadline cancels the fake phone delivery.
- 2026-08-05 Worker: implemented the durable core slice in `notification_center/core.py` and added `tests/test_consumer_policy.py`.
  - Added persisted consumers, ordered policy stages, hashed intake tokens with safe fingerprints, consumer-scoped incident deduplication, and migration-safe `consumer_id`/immutable delivery `target_json` columns.
  - `create_consumer()` reveals `intake_token` only in its creation result; `get_consumer()` exposes only the fingerprint and persisted policy. The valid enabled sequence is Telegram (`chat_id`, optional `topic_id`) followed by fixed `android.phone.call` after a positive operator-selected delay. Matrix and WhatsApp persist only as disabled reserved kinds.
  - Consumer event tokens materialize `telegram.consumer:<consumer_id>` and `android.phone.call` durable rows. ACK continues to cancel queued/claimed rows via the existing state machine. Producer payload authority fields (`target`, platform, phone number, command, delay, retry, stage, and related control fields) are rejected for all events; legacy tokens retain `telegram.main`.
  - Focused red evidence: `python3 -m unittest discover -s tests -p 'test_consumer_policy.py' -v` initially failed with `AttributeError: 'NotificationCenter' object has no attribute 'create_consumer'` (2 errors).
  - Green evidence: `python3 -m unittest discover -s tests -p 'test_consumer_policy.py' -v` => 2 passed; `python3 -m unittest discover -s tests -p 'test_notification_center.py' -v` => 11 passed; `python3 -m unittest discover -s tests -p 'test_delivery_worker.py' -v` => 10 passed; `python3 -m py_compile notification_center/core.py` => passed; `git diff --check -- notification_center/core.py tests/test_consumer_policy.py` => passed.
  - No commit created. Did not edit admin/UI or delivery adapters; adapter routing for the new `telegram.consumer:*` channel remains integration work outside this Worker slice.
- 2026-08-05 Worker: completed the remaining Normal admin/dispatcher integration without changing `notification_center/core.py` or `tests/test_consumer_policy.py`.
  - `notification_center/admin.py` now opens the configured durable Center database only for operator consumer creation/listing, validates the UI-form Telegram destination and fixed phone delay, persists the core-owned policy, and audits only consumer ID/fingerprint (never its intake token).
  - `notification_center/admin_http.py` adds the protected CSRF-gated `/admin/consumers` form, displays each safe policy as Telegram chat/topic followed by fixed-phone delay, and returns a separate no-store page containing the one-time public intake URL plus token. Dashboard snapshots retain only the fingerprint.
  - `notification_center/http_api.py` sends `telegram.consumer:<id>` through the existing Telegram adapter using immutable `delivery.target_json`; it deliberately does not invoke legacy global Telegram follow-up scheduling. `telegram.main` behaviour is unchanged. Existing due `android.phone.call` records still execute through the unchanged adapter path.
  - Focused red evidence: after adding coverage, `python3 -m unittest discover -s tests -p 'test_admin_console.py' -v` failed `test_consumer_form_reveals_intake_url_and_token_once` with `200 != 404` for `/admin/consumers`.
  - Green evidence: `python3 -m unittest discover -s tests -p 'test_admin_console.py' -v` => 3 passed; `python3 -m unittest discover -s tests -p 'test_delivery_worker.py' -v` => 11 passed; `python3 -m unittest discover -s tests -p 'test_consumer_policy.py' -v` => 2 passed; `python3 -m py_compile notification_center/admin.py notification_center/admin_http.py notification_center/http_api.py` => passed; `git diff --check -- notification_center/admin.py notification_center/admin_http.py notification_center/http_api.py tests/test_admin_console.py tests/test_delivery_worker.py` => passed.
  - Changed paths: `notification_center/admin.py`, `notification_center/admin_http.py`, `notification_center/http_api.py`, `tests/test_admin_console.py`, `tests/test_delivery_worker.py`. No commit created. Production deployment/service sandbox policy and the real S21 business canary were not changed or exercised in this bounded shared-worktree slice.
- 2026-08-07: User clarified the final domain model: policies are ordered delivery actions, not platforms. Required action kinds are `telegram.message`, `telegram.call`, `matrix.message`, `matrix.call`, `whatsapp.message`, `whatsapp.call`, and `phone.call`; a sequence may mix writing and calling actions.
- 2026-08-07: User refined the model: the operator freely builds ordered escalation steps in the site. Each step is `platform + action + target + retry interval + max repeats`; the next step is entered only after the previous step reaches its repeat limit. Example: Telegram message to chat A every 3 hours for 10 repeats, then Matrix call, then phone call. The engine must not impose a platform order; the UI/policy defines it.
- 2026-08-07: User finalized the link direction: a step must not contain knowledge of future steps. A successor step references its predecessor with `previous_step_id`; the engine discovers the successor only after the current step exhausts its own retry budget. No fixed Telegram/Matrix/Phone ordering is part of the domain model.

## Worker evidence (2026-08-07)

- Implemented Matrix as an active optional consumer stage: valid enabled sequences are Telegram → Phone (legacy) or Telegram → Matrix → Phone. Matrix accepts only an operator delay; URL, token, and room remain server-owned. Durable `matrix.call` rows carry an empty target.
- Extended `notification_center/admin.py` and `notification_center/admin_http.py` with optional `matrix_delay_seconds` and safe policy display. Blank delay preserves existing two-stage policies.
- Added regressions in `tests/test_consumer_policy.py`, `tests/test_delivery_worker.py`, and `tests/test_admin_console.py` for Matrix scheduling, no pre-deadline delivery, ACK cancellation, and admin configuration.
- Red evidence: new tests initially failed on the old `telegram then phone` validation and the admin form ignored `matrix_delay_seconds`.
- Green evidence: focused consumer policy 3 passed; delivery worker 12 passed; admin console 3 passed; existing notification-center 11 passed; scoped py_compile and git diff --check passed.
- No commit/push, production deployment, Matrix bridge, or real business canary performed. `notification_center/http_api.py` was unchanged because its existing server-owned `MatrixCallSender` path already dispatches `matrix.call` and owns URL/token configuration.

## Generic chain implementation evidence (2026-08-07)

- Replaced the selected fixed Matrix ordering with a generic linked-step model in `notification_center/core.py`: each step stores `platform`, `action`, operator-owned `target`, `retry_interval_seconds`, `max_repeats`, and optional `previous_step_id`; only the root step is scheduled initially, repeats stay on the current step, and the successor is discovered by querying its predecessor link.
- Preserved legacy Telegram/Matrix/Phone policy compatibility while adding migration-safe columns for step identity and delivery repeat state. The admin form accepts a policy JSON chain and retains the old fields as a fallback.
- Focused green evidence: consumer policy 6 passed; admin console 3 passed; delivery worker 12 passed; existing notification-center 11 passed; scoped `py_compile` and `git diff --check` passed.
- This commit is source/test/admin integration only. No production restart or real Matrix/phone business canary was performed for the generic chain.

## Reviewer evidence (2026-08-05)

Verdict: CHANGES_REQUIRED.

Reviewed only the selected shared diff in `notification_center/{core,admin,admin_http,http_api}.py` and focused consumer/admin/delivery tests. `git diff --check` is clean. Re-ran:

- `python3 -m unittest discover -s tests -p 'test_consumer_policy.py' -v` — 2 passed.
- `python3 -m unittest discover -s tests -p 'test_admin_console.py' -v` — 3 passed.
- `python3 -m unittest discover -s tests -p 'test_delivery_worker.py' -v` — 11 passed.

Findings, ordered by severity:

1. P1 — `notification_center/http_api.py:64-74`: the real `TelegramSender.send()` ignores immutable consumer `payload["target"]` and builds the Bot API request only from the global default/severity route (`telegram_destination(...)`). `DeliveryWorker` correctly dispatches `telegram.consumer:<id>` at `http_api.py:191-198`, and core persists the consumer target at `core.py:455-470`, but production delivery will still go to the legacy route rather than the operator-selected consumer chat/topic. The focused test uses a fake Telegram object and only checks that the payload contains `target` (`tests/test_delivery_worker.py:186-205`), so it cannot catch this. Smallest in-scope fix: have `TelegramSender` select the immutable target for `telegram.consumer:*` deliveries (with validation/normalization appropriate to the existing Bot API fields), retain `telegram_destination()` solely for `telegram.main`, and add a sender-level request-capture regression test.

2. P2 — `notification_center/core.py:251-279`: validation enforces the enabled kinds but accepts arbitrary disabled `telegram` or `phone` stages. Thus a policy such as enabled Telegram+Phone plus a disabled extra Phone is persisted, despite the selected contract allowing exactly two delivery stages and only disabled Matrix/WhatsApp as reserved schema entries. Smallest in-scope fix: accept exactly one enabled Telegram followed by one enabled Phone, plus optional disabled Matrix/WhatsApp entries only; add rejection coverage for disabled extra Telegram/Phone stages.

Confirmed invariants apart from the blocked Telegram destination delivery: `create_consumer()` returns the intake token only from creation (`core.py:282-311`), while `get_consumer()` and the dashboard expose fingerprint/policy only (`core.py:313-331`, `admin.py:86-100`); creation is protected by the SSO/CSRF admin handler (`admin_http.py:65-116`) and operator form; producer delivery-control fields are rejected (`core.py:196-218`); ACK/resolve cancel queued or claimed phone work (`core.py:545-557`); and ordinary tokens retain `telegram.main` scheduling (`core.py:387-397`) with legacy post-Telegram handling gated to that channel (`http_api.py:192-198`). Consumer policy rejects enabled Matrix/WhatsApp and materializes neither adapter (`core.py:257-263`, `455-469`); no WhatsApp path is introduced by this diff. The real S21 business canary remains unrun, as recorded by the Worker.
