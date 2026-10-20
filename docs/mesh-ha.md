# Notice Place mesh: availability versus autonomous host repair

Verified against the deployed fleet on 2026-10-20. Product owner:
`megamen32/noticeplace`. Infrastructure placement and recovery ingress belong to
`megamen32/ServersAdministartion`, inventory `docs/inventory/sites/notify.md`.

## Verdict

**The three-node Notice Place availability layer is installed and has real
single-service-node exclusion evidence. Autonomous repair of a failed
server-100 by a surviving AI executor is NOT completed or accepted.**

Do not confuse these outcomes. A working HTTP replica, database leader election,
a delivered Telegram notification, a registered SIP phone, and a resumed repair
agent are different facts.

## What is already running

Notice Place is not waiting to be copied or cold-started on server-88. Native
systemd replicas are already running and enabled on server-100, server-44 and
server-88. Each has local API/delivery workers, a Dqlite store member, shared Rust
mesh-core, private mutually authenticated ingress, operator console, phone shim
and speech route. `Restart=on-failure` restarts a failed local process; it does
not repair another machine.

| Layer | Implementation / current evidence |
| --- | --- |
| Durable business state | Three TLS-connected Dqlite voters on private TCP 11491; quorum is two of three. At 11:43-11:44 UTC all three reported the same membership and leader `192.168.2.75:11491` (server-88). |
| Local API | Loopback port 8091. `/v1/instructions` returned HTTP 200 on all three nodes. |
| Private ingress | Per-node mTLS edge on TCP 11492. Authenticated 44-to-88 and 88-to-44 reads succeeded with certificate and hostname verification; these peer routes do not traverse server-100. |
| Public/recovery entry | Normal ingress plus HAOS recovery ingress. A direct TLS-validated request to HAOS `192.168.2.101:8443`, with the Notice Place hostname, returned HTTP 200. Recovery upstream source declares all three mTLS edges. |
| Shared identity/routing | The installed Rust mesh-core supplies product identity/routing. Dqlite, not the mere existence of mesh-core, replicates the business database. |
| Agent executors | API effective configuration has `NOTIFY_CENTER_AGENT_WORKERS=0` on both 44 and 88. Server-100 uses the source default of two agent workers. This is the current repair bottleneck. |

Installed API revisions are not identical: server-100 points to the `a15de07`
release; 44 and 88 still point to `cleanup-4513abcd5c92572cb143ef0abe78548347935222`.
All three answered the checked read-only endpoint, but that does not prove parity
for every newer MCP or AskHuman capability. Reconcile versions before accepting
new failover behavior; do not update replicas during active calls merely to make
the displayed versions agree.

## What has actually been tested

The existing task `.agents/tasks/noticeplace-mesh-ha-20261013.md` and its protected
receipts record controlled exclusion of each node's Notice Place and voice
service stack on 2026-10-13. These were NOT whole-host shutdowns, power failures,
or arbitrary network partitions.

For each excluded node, a new real incident was accepted once, read back from
the durable store, and delivered. On the server-100 exclusion, the receipt
explicitly proves delivery before its API was restored. Independent Telegram
recipient readback was recorded by that earlier task. All three receipt files
still exist and were read during this audit:

- `.tmp/noticeplace-mesh-preflight/http-exclusion-100.json`
- `.tmp/noticeplace-mesh-preflight/http-exclusion-44.json`
- `.tmp/noticeplace-mesh-preflight/http-exclusion-88.json`

Each receipt has `status=provider_delivered`, `durable_readback=true`, a `sent`
delivery, and **no agent-job delivery ID**. These are valuable availability tests,
not evidence that a surviving AI diagnosed or repaired the excluded server.
Do not replay their events, calls, or idempotency keys.

The 2026-10-20 audit was read-only: SQL `cluster` / `SELECT 1`, service/environment
metadata, local and cross-peer HTTP reads, existing receipt inspection and a
read-only SSH hostname probe from 88 back to 100. No node was stopped, no call or
message was sent, no live preference or timer was changed.

## Telephone continuity is a separate path

A fresh cellular inspection at 11:34 UTC found the handset registered to
Asterisk on **server-88**, peer `gateway-s21`, user-agent `gsm2sip v1.2.0 p3s`,
status `OK (13 ms)`. That PBX has the outbound GSM-forward/SIP route and inbound
AgentCall/AudioSocket route. Its CDR contains an answered call with 50 billable
seconds; that receipt was not uniquely correlated to the user's reported call
or a particular Notice Place question.

GSM2SIP is the handset cellular gateway/client and Asterisk is the SIP/audio
switch; they can be used together. A missing SIP peer on server-100 does not
mean the handset is down: it can select 44 or 88. UserIO owns communications;
AgentCall is internal voice implementation. Telegram, Matrix and Telemost do
not inherit cellular Asterisk/GSM2SIP requirements. See the
[canonical communications contract](https://github.com/megamen32/universal-userio/blob/main/docs/COMMUNICATIONS-ARCHITECTURE.md).

## Why automatic repair of server-100 remains incomplete

1. **No enabled standby agent-worker pool.** Both standby API processes deliberately
   set `NOTIFY_CENTER_AGENT_WORKERS=0`. The staging source explains that accepted
   native investigations owned by 100 must not be recreated elsewhere. This
   prevents duplicates but leaves new automatic repair execution unavailable on
   those workers when 100 is lost. Do not simply turn the setting on without
   implementing and verifying exact job ownership, leases and reconciliation.
2. **Local callback mismatch.** Effective callback URLs on all three replicas
   point to local port 18787. Standby Herder listens on 18791, not 18787. The
   standby callback route therefore needs explicit reconciliation. The configured
   diagnosis/remediation webhooks point to local GPTAdmin port 9001, but their
   autonomous standby invocation was not executed by this audit.
3. **Independent failure detection is not end-to-end accepted.** The inspected
   `health-incident-fleet.timer` on 100 is disabled/inactive, and no equivalent
   named timer was found on 44/88. This does not prove that no other detector
   exists: HAOS recovery control and existing host watchdogs are separate paths.
   Prove which surviving producer emits the host-down incident, to which surviving
   Notice Place entry, and under what existing repair authority.
4. **Full-machine recovery has no matching acceptance receipt.** The earlier
   exclusions left OS/network/other services running. A read-only SSH command
   from 88 to healthy 100 succeeded today, but cannot prove repair while 100's
   SSH/kernel/power is unavailable. That case needs the existing independent
   recovery/out-of-band route and a separately bounded safe recovery policy.

## Smallest completion scope

Reuse the installed replicas, store, Herder/GPTAdmin and recovery control; do not
add another mesh, controller, caller, cron or Kubernetes cluster. Resolve:

- one independently running failure detector and one correlated incident;
- one eligible executor on a surviving node with confirmed model/tool access;
- a quorum-backed single repair owner, correct callback/target host identity,
  and no replay of accepted or uncertain actions;
- existing authorized remote repair actions, then an independent real health
  and user-scenario readback before canonical resolution and handback.

Existing 100-owned sessions remain unavailable/pending while their host is lost;
they are not silently cloned as new sessions. A new incident repair job is a
separate explicitly bound operation. Network partition must not create two
repair owners or duplicate messages/calls. Loss of two voters must fail closed
for operations requiring quorum, not silently switch to an old local SQLite DB.

Acceptance must deliberately distinguish a process crash, controlled service
exclusion and whole-host unavailability. Run an agreed finite drill with rollback
and independent observer: detection on a surviving host -> durable intake ->
one native repair job -> actual repair -> verified recovery -> one outcome.
Record elapsed times and source/runtime identities. This audit did not authorize
or run that disruptive drill, and documentation publication is not completion
of the outstanding runtime work.

## Operations and sensitive data

Current business DB pointer is `mesh:/run/noticeplace-mesh/sql.sock`. The old
`/var/lib/notification-center/notify-center.sqlite3` is retained historical
migration material, not the active database or a safe automatic failback target.
Preserve current requests, unknown-delivery reservations and original receipts.

Protected configuration remains in `/etc/noticeplace-mesh-api.env`,
`/etc/noticeplace-mesh/` and existing operator/provider stores. Document references,
never credentials, phone numbers, private message bodies or transcripts. Read
current node identity, replica role and installed source before applying the
native release runbook. Do not infer current availability from an old service
name, historical PID, copied runbook or successful test on a different host.
