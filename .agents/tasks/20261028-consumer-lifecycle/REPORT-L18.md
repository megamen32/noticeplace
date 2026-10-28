# L18 — installed consumer migration

Owner: `/root/consumer_runtime`, parent `01a17ea3-5600-7580-aba9-1badd2187dd5`.
Current status: known active consumers installed after the explicit central API/auth gate.
No QA incident, owner notification, call, purchase or business send was made.

## Final installed readback (28 October, around 19:47 MSK)

Central owner opened the gate after installing NP `43ec435e` on all replicas.
Existing source credentials for Fleet, external-site, ATV, TGC, AutoSell,
video-watching and winramp each passed GET-only authorization (HTTP200).
All seven existing protected opt-ins are enabled, owner/mode/unrelated values
preserved. No inactive timer was enabled.

- Fleet wrapper `d99e4a4323c9081e7a5f375030b8e876fbcbf240aee2c070b83b6a0f93c0c793`
  and unit `5c263e45` installed; foreign base `14e6c9be…` unchanged. Multiple
  normal postflag ticks exit0, seven durable verifier locks, no relevant
  SDK/auth/permission errors in bounded journals.
- External probe `7da55d98…` installed; canonical ingress/stale retained.
  Postflag stale and normal full sweep 19:39:59–19:47:05 exit0. Existing
  targets/drop-ins/timers preserved; no manual sweep or direct Telegram bypass.
- ATV: registered grant/SDK/opt-in active. Fresh exact five hourly SELECTs in
  BEGIN READ ONLY measured DB/drift/silent-progress predicates healthy; no
  alarm or business cron run for QA.
- TGC: canonical timer exit0 and durable lock after opt-in; fresh readiness
  and DB-local predicates true. Standalone adapter `5edf9f6` active; foreign
  watchdog WIP preserved. This is a mixed-source runtime, not a single-commit
  equality claim. Broad engine and active CO-generation remain unknown.
- AutoSell: recipe `7b35c05e`; installed image
  `sha256:da97533d6fdbf3f87d01e33f67895185c2f7d4e561b0baa23246006615f22fc0`
  layered over newest cloud/email base `3429955f…`, full latest compose chain
  retained. Actual app GET200, UID1000 SDK/hash proof, persistent cache fixture
  across one recreation and healthz200 pass; production proxy-capacity lock
  exists. Only image and two reviewed env fields changed, mounts unchanged.
  Exporter untouched. Hourly unit moved to canonical checkout; fresh DB/spend
  snapshots enforce readonly over six transactions. Exact receipt is
  AutoFindClient `.tmp/noticeplace-consumer-overlay-20261028/installed-receipt.json`.
- Final peer-compatible successor at19:50 MSK is cloud-afc-6cfe-v3 image
  `sha256:c60ecf4b8694775b747d64c42f139e66b86890c4f1101f1439f2df22a5f79d61`.
  Actual readback retains our compose override, exact adapter hashes,
  SDKac0f3c01, UID1000 and V2/persistent-state env; healthy. No repeated
  activation or attempt to restore the earlier image was made.
- Video/winramp: canonical promote-live installed signed server_bot commit
  `b87cb8daa9b602455f6d9e15a74057008246daa8` on100 only; PID739990,
  NRestarts0, cwd/new manifest match; producer hash `3b49ebc7…` and actual
  venv UID1000 SDK import pass. Only bot reloaded. Existing cron/timer reads
  new flags; no business job/topup/purchase was manually triggered.

Video first activation caught an installer PATH defect and rolled back before
restart. Executable `/usr/sbin/rabbitmqctl` fallback was published in dev;
targeted RED → three installer tests GREEN. Normal fixed retry applied required
reject-publish policy and completed. No RabbitMQ restart or policy/auth bypass.
Readonly runtime-role SQL showed 598 complete_ok / 19 errors / 1511 in_flight
over20min; server Prometheus returned200. Generic make-status `/stats60` still
fails async DB lifecycle initialization; legacy viewer units show inactive.
No broader fleet PASS is claimed. Exact follow-up is in canonical video
`docs/operations/bugs.md`; no legacy viewer was activated.

Registry records eleven known active installed entries. Earlier partial
server88 discovery keeps global coverage=false. Failed own test images were
removed; smoke containers used --rm. Container/workspace retention previews
completed without own leftover containers or worktrees. Central/Herder/UI/QA runtime belongs to
the respective owners and was not changed by this leaf.

## Historical preactivation notes below

The following sections describe earlier preparation and the closed gate at
that time. Their pending/old-image statements are superseded by the final
installed receipt above; they are retained as work evidence.

## Published source and frozen inputs

- ServerAdmin `5c263e45`: explicit writable Fleet SDK state directory in its unit,
  `/var/lib/fleet-health/source-verifier`. Unit assertion RED → GREEN. Wrapper-only
  installer remains strict about installed foreign base SHA256
  `14e6c9be18f11e413362e2897dd774e3d5a01ba403497ebdc7887363bec5aa4c`.
  Frozen input under ServerAdmin `.tmp/consumer-runtime-l18/fleet/`.
- AutoFindClient `5c202ed5`: reviewed image overlay recipe under
  `deploy/docker/noticeplace-overlay/`. Exact consumer input
  `2a23c126c0c9a6a15cf02491fd68bc8f5adcfb3e`; SDK input
  `26e402dd5030d30cdddf4e8eb7fb8e2b46afeef0`. Only `proxy_manager.py`,
  `noticeplace_client.py` and the stdlib namespace SDK enter the overlay.
  Hook selected 17 Docker runtime contract tests, all passed.
- A peer deployed email-receipt fixes during preparation. CAS rejected the old
  base before any app activation. New reviewed base is
  `sha256:dbcadd26b598bc860b4dcaee240532179b3ffb3a29a5f7f89ed51caef73b7634`.
  The two producer files are identical between old and new bases
  (`60936796…`, `55c08468…`); the overlay preserves peer email fixes.
  The full actual compose chain, including `receipts.override.json`, must be
  retained. Before activation repeat image-ID CAS; never restore old `ebdd71…`.
- Video canonical signed `server_bot` candidate built from frozen Git archive
  `ae1f80859d23ca4639f328f7512874aeaa1e0351` using the existing release builder.
  Manifest: `video_watching/.tmp/noticeplace-server-release-20261028/bundles/`
  `srv-1.84.0__cli-1.84.1__ae1f80859d23/release-manifest.json`.
  Server bundle SHA256 `da8da8adc7a97eaea7377fefc21995c491b1e31602b3024480068fb7e51c6b62`;
  unchanged client bundle reused with SHA256
  `b7473ce031653c498fed137585cdd4f44358a08fc4ec7ed777f208d4d6e474b1`.
  Bot producer hash `3b49ebc7fa31f3e857cba603dd2a0e837d6b94d797d4da1f067ae818d49d87cf`
  exactly matches published source; no `.env` or `runtime-db.env` in bundle.

## Runtime boundary decisions

- `autosell-proxy-source` runs `AutoSellBot.proxy_source_server`, a read-only
  approved-cache exporter. It imports neither NoticePlace nor proxy_manager;
  it needs no health verifier and must not be recreated for this task.
- Actual app user is UID1000, module path matches the overlay COPY destination.
  HOME `/home/autofind` is writable but ephemeral. The accepted app override
  sets `NOTICEPLACE_VERIFIER_STATE_DIR` inside its existing persistent cache
  bind; no extra secret, mount or volume is needed.
- Minimal SDK is a PEP420 namespace package, without the server `__init__`
  (that imports `.core`). Real uid1000/network-none compose smoke caught
  staging permission errors before production. Docker COPY must preserve
  directory traversal as well as file readability. Failed test containers
  removed themselves, and failed candidate image tags were removed.
- Host producers use the shared SDK at `/opt/noticeplace`. TGC is a root-owned
  canonical watchdog runner; its foreign source WIP remains untouched. ATV
  cron helper reads its existing protected environment directly. No inactive
  infrastructure timer will be enabled.

## Gate and exact next step

Final prepared AutoSell image:
`autofind-client:noticeplace-5c202ed5`, immutable ID
`sha256:bc339627f50bc7ca0ea18d2dd6a56240611aa859c8559a3f0e4e776f8d5008d0`.
Real isolated compose smoke ran as UID1000 with no network or runtime mounts:
SourceVerifier import, real producer module location and all three source hashes
passed. The complete current compose chain plus the proposed override was
resolved without activating it. Changed top-level fields are exactly image and
environment; changed env names are exactly `NOTICEPLACE_CONSUMER_V2` and
`NOTICEPLACE_VERIFIER_STATE_DIR`. Existing effective env and mounted
source/destination pairs match the current running app. Existing volumes,
ports, command and all image entrypoint/user/working-dir/health settings remain
unchanged. No test containers remain.

App activation helper is staged at AutoFindClient
`.tmp/noticeplace-consumer-overlay-20261028/activate-app.py`; it rechecks the
immutable base and exact compose chain, validates the actual container's
existing source credential through GET-only HTTP, repeats CAS before the
single `up --no-deps --no-build --pull never app`, and checks a protected
persistence fixture through the existing cache bind after recreation. The
helper has not been executed.

L17 reported new central startup failure in the historical lifecycle backfill,
and rolled back all three central releases/credential files. Its gate remains
closed. This is not installed consumer acceptance.

After explicit central API/auth ready: perform GET-only verifier scope checks
for the exact existing producer credentials; validate SDK import as real UID;
install frozen Fleet adapter+unit and external probe; switch only the AutoSell
watch unit to canonical source; enable the seven scoped environment flags;
activate the reviewed image overlay and server-only video candidate through
their actual owner routes. Record hashes, persistent receipt readback, next
normal timer execution and fresh read-only source checks. No test incidents,
owner messages, phone calls, purchases or business send jobs belong to L18.

Prepared helpers: ServerAdmin `.tmp/consumer-runtime-l18/auth-preflight.py`
(GET-only, prints project/status/count) and `activate-flag.py` (single flag,
protected backup, permission/owner preservation and concurrent-change fence).
Neither helper has been used to activate a producer.
