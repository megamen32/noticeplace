# L17 central release and one isolated lifecycle proof

## Preparation — 2026-10-28 15:45 UTC

No live configuration, service, event or AI mutation has occurred in L17 yet.
API100 and operator console were active; fresh direct-LAN hostname checks confirmed
`server-44` at 192.168.2.5 and `roomhacker-server-88` at 192.168.2.75.
Installed NoticePlace still pointed at the earlier e34e7749ef2c immutable release.

The final reviewed consumer plan includes the exact QA consumer/source grant,
published with SDK fix as `63ce9c93f79f09385da3c8bbff78c04222e9e63c`.
L19's definitive-stale regression failed before the fix; all eight SDK tests
then passed in 5.9 seconds. Independent integration reviewer accepted the delta.

One protected credential candidate was prepared and transferred without printing
secrets. SHA256 on 100/44/88:
`7e3d7a002f977e85b7067ecd354457645d9ce8418ee56b667b0ee96f872e2944`.
Candidate has 53 scopes: 26 unchanged producer bearer values plus 27 independent
executor credentials, and three reviewed supplemental registered-consumer grants.
`--check-prepared` passed on all three hosts. Existing environment/callback files
remain unchanged; helper reported services_restarted=false.

Protected references (not secret values):

- 100: `.tmp/release-core-20261028/credentials/candidate.json`, root parent0700/file0600.
- 44/88: `/var/lib/noticeplace/lifecycle-release-20261028/candidate.json`, root parent0700/file0600.

Remote source installation uses the same frozen published archive as 100, with
mandatory SHA256 verification before extraction/install and full revision manifest.
This external integrity gate was explicitly accepted by the root; no new installer
path, checkout or worktree is needed. Existing configuration is preserved by the
canonical install_if_missing behavior. Herder is installed by its sole owner;
this worker must not rebuild or restart it.

Live join is still blocked by L20/L21 integrated completion/gate fixes. Prepared
fixture files in `.tmp/lifecycle-canary-20261028` are inert; neither fixture server
nor source checker has been started, and no incident has been created.
Read-only canonical consumer lookup independently confirmed that the existing
QA intake token still authenticates the exact approved consumer/project; its
existing emergency ceiling was observed and preserved. Loopback39129 was free.
Console files were staged from published ServerAdmin `7d3d481888013dede71a0c5e3f18bac604af08fa`;
only its six owned changed paths will be installed, preserving landing and units.

## Required installed proof

One new incident in project `noticeplace-qa-stop-20261023b`, exact source
`lifecycle-canary-20261028-01a17ea3`, host `server-100`, problem `cpu`, muted in
the same creation transaction. Initial native attempt diagnoses the fixture;
explicit finish must pause the attempt while leaving the incident open. A fresh
independent degraded HTTP observation must produce exactly one continuation into
the same native session; the next attempt repairs the fixture and explicitly
finishes. A fresh independent healthy observation must resolve the incident.
Record actual operation IDs, native session, harness/model, autopilot state and
source receipts. No owner notification/call, accepted/UNKNOWN replay, fabricated
agent completion or agent-source healthy verdict is permitted.

## First central activation failed; rollback restored service

- Published narrow workflow-prompt correction `36197227116f914965e94422e27679f8c7d70aae`: follow target AGENTS/owner-selected workflow; no hardcoded GSD bootstrap. Targeted prompt assertion RED then GREEN.
- Final frozen archive SHA256 `7a6fd73b77640658e4e25ba4810ae0798f577f8a84d7ad3265812b8afeb92deb`; verified before extraction on44/88. Candidate stayed unchanged.
- Herder prerequisite independently confirmed installed `05a7c03da74a60aa540221a94b327fa4388c555a` contains9cd3475d, active PID3906189, HTTP200, manifest/index SHA256 `e319b85041845584965e81114860084b8d3b72d3543eb2d1f5dd97e9b14e7dbf` matched actual file.
- Stopped only the three mesh-api units, installed frozen source without starting, applied the same candidate with CAS backups on all nodes, synchronized executor-only callback27 and started APIs.
- Actual startup failed: `incident_lifecycle.initialize`, line79 historical health-event payload SELECT exceeded the mesh broker bounded SQL result. HTTP8091 did not become available. Source tests were insufficient for this production-history migration.
- Immediately stopped APIs and used canonical release rollback plus helper CAS credential rollback on all three hosts. No direct SQL mutation, migration skip or broker-limit change. Restored100 e34e7749 release, callback26 legacy projects, PID4084139/NRestarts0/HTTP200. Replicas44/88 restored their exact previous a7ef74d0 releases and active units.
- No new incident, AI launch, fixture process or consumer-runtime gate occurred. Console has not been installed. The initial schema DDL may exist; failed backfill transaction was rolled back. Root owns the bounded paging correction before the next single corrected activation; the same protected candidate remains available.

## Corrected central release installed; one QA admission blocked

Installed all three APIs from `43ec435ef45636fb842fcc44d321e38fec442aa6`, frozen archive SHA256 `734c87dd7893c3ccda7df4596f8b5fc6998bb28d358ec8564582202517b332a7`. 100 cold readiness 30.053 seconds; 44 and 88 sequential readiness approximately two seconds. All active, NRestarts0. The same protected candidate produced identical scope hash on all nodes, retained source bearers and nine automatic flags, and synchronized 27 executor-only callback entries. Source verification GET returned200, executor verifier GET401. No auth bypass.

Console five owned inputs installed from ServerAdmin `7a6eb9bdc76e678508edba020941fee13b7dddb1`; actual installed selftest passed at2026-10-28T16:51:59Z. Foreign landing, units and other inputs preserved.

Exactly one muted QA incident created: `inc_c1092c3c91514485a0617072e87a7727`, event `evt_a70d2668602e42c68b14c890b70a60d6`. Initial notification cancelled inside creation/mute transaction. Manual AI delivery `dlv_7c7b8f4f454a43e19da5e809a6d659a1`; Hub job `55a4eccf5492b0fa4d830c31d0d9aba3` definitively source-blocked before project-launch because remote runtime probe lacked auth. Computed operation `e0fff05d48167299345eaaffac137ad6` was not admitted; native session absent. Sole Herder owner fixed runtime transport; supported authenticated operation-negative proof and combined L26/L28 source rollout remain prerequisite to a new ordinary manual attempt of this same incident. Source checker is separately running but gated; no source POST yet. Fixture remains HTTP503 with zero actual attempts.

Narrow /etc/gptadmin traverse ACL restored for uid1000, leaving foreign root-only files untouched; both existing profiles and callback readable at0600. Persistent canonical ACL creator correction remains owned after native admission.

## Final installed business proof — 2026-10-28 17:18 UTC

Final central source `6a9f78fece17f3f2c710f57ff4d45396cc6999a1` installed rolling100→44→88. Frozen archive SHA256 `6ba0f80afcf2eb9bb9d6e2671f8d9d066a7148c49e4a34ca6455bcdb12a7aba5` verified before each remote extraction. Three actual installed helper/adapter/core hashes match exact published inputs; all active/NRestarts0. Source role GET200/executor GET401, same protected scope hash, nine flags unchanged.

Historical first prelaunch block was reconciled only after root/sole Herder owner verified exact operation absence in authenticated process-bound durable attempt log and current runtime identity. Stored domain proof preserves oldsent; a new normal manual delivery `dlv_f1359c1eddeb470d8c090618f8ad0c79` was queued once. No initial event or accepted/UNKNOWN replay.

- Incident: `inc_c1092c3c91514485a0617072e87a7727`, one new muted card.
- Initial operation: `e0fff05d48167299345eaaffac137ad6`, accepted17:07:47Z, created=true.
- Native: `ses_e7c849ab9ffeA5lL9rAli7jl1R`, OpenCode; actual model `omniroute/glm-5.3-flash-minimax-m3.1`, readable Russian incident title.
- First actual attempt `np-attempt:dab7a9fcc00ae251f120862582080c0795289f2e0c74585369e042c2c4b4585d`: HTTP503 reproduced, one control action recorded, useful diagnostic in native transcript. APtrue initially17:07:49.874Z; executor explicitly finished through protected same-project HTTP callback, APoff17:14:00.810Z, incident stillOPEN/work_finished/due.
- Independent fresh HTTP503 at17:14:47.767630Z, attempts1, request `check_9cdb6f77aad04f8d93ad120390b6f816`, created exactly one continuation delivery `dlv_e4cb57cc0ff24b299c0f087ddbc21203`.
- Continuation operation `b96c74a15313a9179b493bd4f8e86cdb`, accepted17:14:55Z, created=false, SAME native. AP rearmed17:14:57.651Z. New actual attempt `np-attempt:566b41212327f620b3ce8100b02e20326cbfa3ea9400bcaf3527b9f876b1912a` repaired source to200, then explicitly finished. APoff17:18:08.275Z while incident remainedOPEN awaiting source.
- Independent fresh HTTP200 at17:18:30.277610Z, attempts2, body SHA256 `4f4f9ee297ed82e21b8b1208518cb17efa3de3a6a226676e2d3c76558ae2fe6f`. Source request `check_e596bfd165a34a71a8b8e779101a7c34` confirmedhealthy and independently resolved the incident.

Sanitized final `RECEIPT-L17.json` SHA256 `28b67bcdef21e74a568537d8903a465eb5a3e21e4c2ae9d9b676449a66acfa5c`. It records exactly two admitted operations and one created native, finalresolved/healthy/no-due/APoff, final fixture state, installed hashes and delivery statuses. All three QA Hub jobs terminal; initial Matrix cancelled, no owner contacts.

Cleanup: removed only own temporary Herder route via If-Match CAS; stopped own fixture/source PIDs612198/613049; sourcegate absent. Preserved incident/native/source history. Root independently read HTTP200+exact2attempts and native ZeroKnowledge read the same session; no extra incident was created for validation.

MCP limitation: actual shared OpenCode Notice MCP uses protected opencode.env project=hermes, so QA-project progress/work-finished correctly rejects its projectscope. Existing exact QA executor callback succeeded for both realfinishes. No production grants widened; no Telegram question was required in this deterministic isolated fixture.

## Final permission follow-through

L27 persistent ACL cause and installer drain classification fixed, published and installed GPTAdmin443f941b. Actualrestart preserves uid-onlytraversal, profiles/callback0600 and foreignroot-onlydenial. See REPORT-L27.md/RECEIPT-L27.json. Own fixture/source and temporaryroute removed; incident/native history retained. Both retention previews on100 exit0, no leaf-created candidate garbage.
