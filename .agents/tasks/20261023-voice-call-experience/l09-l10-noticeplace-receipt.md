# L09 + L10 — Notice Place leaf receipt

Session: mvs_2b489b9ad2364fcaa03f4687420699a1 (MiniMax worker, retry of this leaf; parent UserIO
mvs_4f40fab5c86f4ad5bad2cd9b7b5507a2). Plan C2 rev 625396cebde8, leaves L09 + L10.
Host of all work: server-100 (`roomhacker@95.165.165.65`, ssh -p 22104), repo
`/home/roomhacker/agents-projects/noticeplace` (branch `main`, HEAD `285591e`).
Written 2026-10-24 (server-100 local time). No credentials, tokens, cookies or keys are recorded here.

## 1. NATIVE STATE

- Access verified with the parent's own SSH grant:
  - `git log --oneline -1` → `285591e docs: reconcile voice call task plan evidence`
  - `git status --porcelain | head -5` → `M .agents/tasks/.../plan.graph.json`, `plan.html`, `plan.md`,
    `task.md`, `M bin/notify-center` (all foreign / untouched by me).
- Live release actually serving traffic: `noticeplace-mesh-api.service` →
  `ExecStart=/usr/bin/python3 /opt/noticeplace-releases/20261023T091306Z-b1944cd2b39c-2269751/bin/notify-center`,
  `WorkingDirectory=/opt/noticeplace-releases/20261023T091306Z-b1944cd2b39c-2269751`,
  API on `127.0.0.1:8091` (`NOTIFY_CENTER_PORT`).
- Live database is the dqlite mesh, not the local sqlite mirror: `NOTIFY_CENTER_DB=mesh:/run/noticeplace-mesh/sql.sock`.
  All readbacks below are SELECTs over that socket (`/opt/noticeplace-console/mesh_data.py:76-135`), so the
  numbers are the live ones. The file `/var/lib/notification-center/notify-center.sqlite3` is a stale mirror
  (it does not contain the QA consumers) — it was not used for any conclusion.
- Live console API: `noticeplace-console-api.service` → `/usr/bin/python3 /opt/noticeplace-console/console_api.py`,
  `WorkingDirectory=/opt/noticeplace-console`, `EnvironmentFile=/etc/notification-center-admin.env`,
  `ReadWritePaths=/var/lib/noticeplace-console /etc/notification-center.env /etc/gptadmin/agent-jobs.json
  /var/lib/noticeplace-mesh-kill`.
- Predecessor session of this same leaf (`mvs_49d93ef5982e42a6913afbccdea48a58`, recorded in
  `ownership.md`) had already created the L09 consumer and one failed delivery; its artifacts are kept and
  reused here rather than re-created.

## 2. L09 — Matrix-only recipient: DONE (live, read back)

Live consumer (mesh read):

- `consumer_id`: `consumer_7ad31d4110d744259bde4ee7580890e4`
- `project`: `noticeplace-qa-stop-20261023b`, `name`: "QA: ответ останавливает уведомления",
  `profile_type=custom`, `max_severity=notice`.
- `consumer_policy_stages`: exactly one enabled row —
  `step_id=step-1`, `previous_step_id=NULL` (root stage), `platform=matrix`, `action=message`,
  `enabled=1`, `retry_interval_seconds=60`, `max_repeats=1`,
  `target_json={"room_id": "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"}` (the isolated L02 room).
- Zero rows with `platform`/`action` in {phone, android, telegram, whatsapp, vk} and zero `*.call` actions:
  no `phone.call`, no `android.phone.call`, no `telegram.*` anywhere in this policy.
- Because `profile_type=custom`, the builtin ladder
  `("telegram.main", "matrix.call", "android.phone.call")` (`notification_center/core.py:2505`) is not
  applied to this consumer at all — the ladder branch only fires for `profile_type='builtin'`
  (`notification_center/core.py:2500-2505`). An empty/legacy policy would therefore have been
  insufficient; this policy is explicit and non-empty, as required.
- Consumer token is live (read-only proof, secret value never printed):
  `GET http://127.0.0.1:8091/v1/incidents/inc_1d027be8f1fd419f890e67d3e7fc89a8` with the consumer's own
  intake token → HTTP 200, body `consumer_id=consumer_7ad31d4110d744259bde4ee7580890e4`,
  `project=noticeplace-qa-stop-20261023b`. Token fingerprint (sha256 prefix) is kept only in the protected token file and in the console audit record; the token itself is stored
  0600 outside the repo at `/home/roomhacker/.config/noticeplace/qa-stop-20261023b-consumer.json`
  (value deliberately absent from this report and from stdout).
- Creation path (no bypass): console `POST /consumers` with an SSO session + CSRF token, via the
  audited `AdminConfigStore.create_consumer` (`notification_center/admin.py:627`) and the generic policy
  validator (`notification_center/core.py:1610-1665`).

Changed paths (only inside owned paths):
- `.agents/tasks/20261023-voice-call-experience/console-auth-overlay/console_api.py`
  - `console_api.py:60-78` — new `_admin_state_dir()` helper
  - `console_api.py:91-94` — `AdminConfigStore(..., _admin_state_dir())` instead of the hard-coded
    read-only `/var/lib/notification-center-admin`
  - overlay sha256 == deployed `/opt/noticeplace-console/console_api.py` (overlay is the truth for the
    deployed file)
- `.agents/tasks/20261023-voice-call-experience/l09-l10-noticeplace-receipt.md` (this file)
- `tests/test_consumer_policy.py:326-359` (new test, see §3)
- `tests/test_incident_call_chain.py:340-380` (new test, see §3)

Why the console change was needed (defect found, not guessed): `noticeplace-console-api.service` runs with
`ProtectSystem=strict` and does not list `/var/lib/notification-center-admin` in `ReadWritePaths`, while the
audited admin operation appends `audit.jsonl` into that state directory — `POST /consumers` therefore ended in
`OSError: [Errno 30] Read-only file system` after the consumer was already persisted in the mesh, so the caller
got HTTP 500 and never received the one-time intake token. The console now falls back to its own writable state
directory `/var/lib/noticeplace-console/admin-state` (0600), keeping the operator action audited:
`{"action": "consumer_created", "actor": "sso:operator", "subject": "consumer_7ad31d4110d744259bde4ee7580890e4",
"token_fingerprint": "<sha256 prefix — value kept in the audit record>"}`.
Still owed by the owner of the unit (not my owned path): add
`ReadWritePaths=/var/lib/notification-center-admin` to the console unit so the audit returns to
`/var/lib/notification-center-admin/audit.jsonl`, and apply the same patch to the canonical source
`automation/noticeplace-console/console_api.py` in the ServersAdministartion repo, otherwise the next
`install.sh` reverts the deployed fix.

## 3. L10 — answered receipt and escalation stop: BLOCKED on a UserIO-owned resource

What is proven live and by independent readback (mesh SELECTs, not memory):

- Event/incident from the L09 consumer: `evt_30cf01bc19d14e218776daeb2e9e2e64` → incident
  `inc_1d027be8f1fd419f890e67d3e7fc89a8` (project `noticeplace-qa-stop-20261023b`, event_type `notification`).
- Deliveries of that incident: **exactly one row** — `dlv_176ba2cfc49c41eca84b6bebef72d288`,
  `channel=matrix.message`, `policy_step_id=step-1`, `target_json={"room_id": "!ZatzpywRZSemgAaPju:..."}`,
  `status=cancelled`, `attempt=5`, `last_error="HTTP Error 403: Forbidden"`.
  A SELECT for `telegram.*`, `android.*`, `phone.*`, `*.call` on this incident returns **0 rows**: nothing was
  ever scheduled on a phone or Telegram channel, not even while the Matrix step kept failing and retrying.
  The incident is `state=resolved`, `acknowledged_at=NULL` (closed by the consumer's own resolve action to stop
  the unbounded retry backoff `min(300, 5*2**attempt)`).
- Code-level proof inside owned paths (both tests pass, run against the release code):
  - `tests/test_consumer_policy.py:326-359`
    `test_matrix_only_policy_keeps_a_refused_delivery_inside_matrix` — a refused `matrix.message` delivery
    stays inside Matrix: the incident's delivery set remains `['matrix.message']` and contains no
    phone/android/telegram channel; the stored policy stays `['matrix.message']`.
  - `tests/test_incident_call_chain.py:340-380`
    `test_answered_matrix_call_for_a_matrix_only_consumer_leaves_no_phone_or_telegram` — an answered Matrix
    call acknowledges the incident (`state='acknowledged'`, `acknowledged_at` set), the audit row records the
    answerer `matrix:@…`, and the incident has no `telegram.*` / `android.*` / `phone.*` delivery.
  - Full owned suites: `python3 -m pytest tests/test_consumer_policy.py tests/test_incident_call_chain.py -q`
    → **41 passed** (47.91s); the two leaf tests alone → 2 passed (1.95s).

Exact reasons the live delivery/answer could not be completed (all measured, none guessed):

1. **The Matrix message sender is not a member of the isolated room.** The only Matrix message sender
   configured in the live env is the service account `@aiadmin:chat.bezrabotnyi.com`
   (`MATRIX_MESSAGE_HOMESERVER`, `MATRIX_MESSAGE_ACCESS_TOKEN`, `MATRIX_MESSAGE_ROOM_ID` in
   `/etc/notification-center.env`; read-only probe `/_matrix/client/v3/account/whoami` → 200,
   `user_id=@aiadmin:chat.bezrabotnyi.com`). `/_matrix/client/v3/joined_rooms` → 20 rooms, the isolated room
   `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` is **not** among them. That is exactly the measured
   `HTTP Error 403: Forbidden` on the live delivery above. The room is invite-only and its two joined members
   are the L02 test bots; UserIO's voice worker hard-gates the room on exactly those two joined members
   (`l02-isolation-evidence.md:149-152`), so joining `@aiadmin` would break the concurrent L03 run and
   invalidate the L02 isolation evidence ("exactly 2 members with membership=join").
2. **The release has no Matrix *message* answer→incident binding.** In `notification_center/` the only Matrix
   channels are `matrix.call` (8 references) and `matrix.message` (1). `acknowledge_if_active` is reached only
   from `notification_center/http_api.py:1271` (answered `matrix.call`), `:1285`, `:1358` (telegram call) and
   `notification_center/core.py:2705` (phone receipt). The `matrix.message` branch
   (`notification_center/http_api.py:1239-1251`) completes the delivery as `sent` and never acknowledges the
   incident. `human_request` answers (`notification_center/http_api.py:1661-1712` →
   `notification_center/core.py:1378-1422`) resolve the question only; they do not transition the incident.
   The only non-voice acknowledgement of an incident is the operator action
   `POST /v1/incidents/{id}/ack` (`notification_center/http_api.py:2543` → `notification_center/core.py:2669-2687`),
   which is an operator decision, not an answer inside Matrix.
3. **The live voice bridge would not reach the isolated room and would touch a personal account.** The bridge
   behind `MATRIX_CALL_URL=http://127.0.0.1:18093/v1/calls/matrix/noticeplace` is UserIO's runtime
   (`python -m universal_userio.runtime`, pid 2341933, `/opt/universal-userio`). Its destination comes from the
   bridge environment: `USERIO_MATRIX_NOTICE_ACCOUNT=matrix:3a38caa1e8ac2eeb77443b275466`,
   `USERIO_MATRIX_NOTICE_ROOM=!IfwZsTlPzouoIDHQez:chat.bezrabotnyi.com`,
   `USERIO_MATRIX_NOTICE_TARGET=@bezrabotnyi:chat.bezrabotnyi.com` — a different room, and the owner's
   personal Matrix id, which is forbidden for me. Repointing those variables would also repoint the production
   owner-alert voice route, so it is not a change I may make.
4. **Safety: a rejected Matrix admission would be treated as a proven miss and would schedule a phone rung.**
   `notification_center/incident_call_chain.py:154-161` (`advance`) schedules `telegram.call` and then
   `android.phone.call` only after a `safe_missed` receipt, and `safe_missed`
   (`notification_center/incident_call_chain.py:58-61`) requires `answered=False`. The bridge's "another Matrix
   voice call is active" rejection (HTTP 400) is mapped by `IncidentCallSender` in
   `notification_center/incident_call_chain.py` to `answered=False, ended=True, safe_to_fallback=True`. So while
   the parent's own call is active in that room, a voice attempt from Notice Place would not merely miss — it
   would advance the chain to `telegram.call` and `android.phone.call`, i.e. plan a phone call for the same
   incident. That is exactly the forbidden outcome, so no event was posted for a `matrix.call` consumer.

Conclusion for L10: the product path is implemented and verified at code level (answered Matrix receipt →
`acknowledge_if_active` → incident `acknowledged` → chain advance stops, no `telegram.call`, no
`android.phone.call`), but the live end-to-end run needs a Matrix-side resource that only UserIO owns: either a
noticeplace-bound Matrix voice binding for the isolated L02 room (test-only, production route untouched), or
`@aiadmin` joined into the isolated room (breaks the two-member gate and the L02 evidence), or an
operator-relayed answer through `POST /v1/incidents/{id}/ack` (no Matrix answer, no voice).

## 4. Exact blocker and the minimal access that unblocks it

The blocker is a Matrix-side resource, not a Notice Place defect: the only Matrix identity the release can
send as (`@aiadmin:chat.bezrabotnyi.com`, from `MATRIX_MESSAGE_ACCESS_TOKEN`) is not a joined member of the
isolated room `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` (measured `whoami` 200, `joined_rooms` 20 without it,
live delivery 403), and the isolated room is invite-only with exactly the two L02 bots joined, which UserIO's
voice worker requires — so the notification cannot physically arrive there while the two-member gate holds;
the only in-release way to bind a Matrix answer to an incident is the `matrix.call` answered receipt, and the
live noticeplace voice binding points at a different room (`!IfwZsTlPzouoIDHQez:…`) and at the owner's
personal account (`@bezrabotnyi:…`), which I must not call. The minimal access that unblocks L10 is one of:
(a) UserIO provides a **noticeplace-bound Matrix voice binding for the isolated test room** (a test-only
account/room pair on the UserIO side, leaving the production `USERIO_MATRIX_NOTICE_*` route intact) plus an
explicit go for the voice attempt after the parent's own run has finished — then L10 is one
`POST /v1/events` against a `("matrix","call")` consumer plus the answered readback; or (b) UserIO joins
`@aiadmin` into the isolated room and accepts that the two-member gate and the L02 evidence change; or
(c) UserIO relays the L02 bot's answer to Notice Place as an operator action
`POST /v1/incidents/{id}/ack` — this proves the escalation-stop readback but is not an answer inside Matrix.
A fourth option is a product change, outside my owned paths: a Matrix reply→incident ingress in
`notification_center/http_api.py` (the `matrix.message` branch at `:1239-1251` would have to acknowledge the
incident on a confirmed reply).

## 5. What was NOT done

- **No phone call of any kind.** No PSTN, GSM, SIP, Asterisk, GSM2SIP or android-phone was called, configured
  or scheduled. `phone.call`, `android.phone.call` and `telegram.call` were never written into any policy;
  the only channels created are `matrix.message` and (for the code-level test only) `matrix.call` inside a
  unit test. The bridge's personal target `@bezrabotnyi:chat.bezrabotnyi.com` was read from the bridge
  environment for diagnosis only — it was never called.
- **No personal Matrix account used.** The only Matrix identity touched was the service account
  `@aiadmin:chat.bezrabotnyi.com`, used read-only (`whoami`, `joined_rooms`).
- **No voice call started** in `!ZatzpywRZSemgAaPju` — the parent's concurrent run was left alone, and no event
  was posted that could have produced a call or a chain advance.
- No new ACL/role/user created in UserIO; no new Notice Place user either (only a consumer and its project).
- No credential rotated, no cron/daemon/timer created, no `omniroute@20128.service` touched, no Codex session
  contacted. The only service restart in this leaf's history was `noticeplace-console-api.service`, needed to
  deploy the console fix (predecessor session).
- No change outside my owned paths. Written: the overlay `console_api.py`, the two owned test files, this
  report. Not touched: `*.bak-mavis-*`, `notification_center/graphify-out`, `.tmp/`, foreign worktrees, the
  canonical `universal-userio` checkout (read-only reads only), `deploy/`, `mcp/`, `web/`, `bin/notify-center`,
  `plan.*`, `task.md`.
- No raw writes to the live database: the mesh was read with SELECTs only; the only write path used was the
  console API (SSO + CSRF + audited admin operation).
- UNKNOWN/accepted not repeated: no re-run of the failed delivery, no attempt to re-derive the L02 evidence.

## 6. Known hygiene item (owner decision, not actionable by me)

Orphan consumer `consumer_4f63a2d2297c4f5dbddd19db3ff40dd2` (project `noticeplace-qa-stop-20261023`, one
`matrix.message` stage to the isolated room) cannot be deleted or disabled: the release exposes no consumer
delete/update path (`notification_center/admin.py` has only `create_consumer`; no `DELETE`/`UPDATE` for
`consumers` in `notification_center/`), and raw SQL writes to the live mesh are out of bounds for me. It is
inert (own project, lost intake token, no events, no deliveries). Minimal unlock: an owner-side deletion path
or an explicit decision to keep it.
