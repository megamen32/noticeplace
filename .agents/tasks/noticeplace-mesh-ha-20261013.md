# Notice Place: survive loss of one mesh service node

Owner: Codex current chat `01a1328a-6739-7df3-9514-ecac740951a8`.
Requested: HTTP/curl requests, incident intake and processing, and telephone calls must continue when one of the service nodes (100,44,88) fails.
Status: **in progress; HTTP/incident delivery verified; standby voice assistant remains incomplete**.

## Verified current state (2026-10-13, latest)

- Both preserved ZCode and reviewed mesh source inputs were integrated and pushed to main. Three immutable API/worker releases run source18ab3996ab0c643cf433bf19a02c0b6407506431 on100/44/88. Original source inputs remain in history; unrelated WIP was preserved.
- The real three-voter TLS Dqlite mesh contains the migrated business database:15 tables,662988 rows,196069 events. The frozen original SQLite is retained. All8 original sending and1472 uncertain reservations were preserved by ID through each service exclusion. Store budgets follow measured usage: MemoryHigh5G, MemoryMax6G, swap0, CPU2, TasksMax128. Actual migration/import and shared-connection transaction regressions passed.
- Public nginx and independent HAOS TLS ingress route to all three private mTLS edges. New real incidents were admitted and read back while each node's owned service stack was excluded separately. Provider receipts plus independent Telegram recipient readback confirmed messages6782 (without100),6783 (without44),6785 (without88). All three test incidents were resolved. These were service exclusions, not whole-host power-off tests. Private receipts: `.tmp/noticeplace-mesh-preflight/http-exclusion-{100,44,88}.json`; do not repost existing test events.
- Three PBX/voice/TTS runtimes, shared phone-line fencing and quorum-backed S21 lease/SMS receipts are installed. Independent phone reverse tunnels to44/88 were verified. Earlier actual100-to44 and44-to88 SIP registration switches were observed. A later reconnect-storm APK fix from infrastructure source952d0602 was installed with retained signing identity; complete failover acceptance of that final APK is outstanding.
- Real neural TTS44/88 and authenticated speech recognition44 succeeded. An actual forced backup route transcribed real audio through44 in3.008s and was restored. This proves that speech route, not a completed assistant conversation.
- The user's existing incoming call on44 at12:50 Moscow (631a1ce5-821b-4673-8022-8f2acd271a86) had working audio and recognized speech, but FastAgent startup failed. Do not replay this conversation or its actions. No outbound call or test SMS was originated by this task.
- Wrong standby `AGENTCALL_FASTAGENT_*` settings and a shell shebang were identified; owned deployment corrections and the existing protected fallback credential were applied on44. The installer correction remains infrastructure source WIP. A real SDK startup diagnostic then failed specifically at MCP AgentHerder initialization: ConnectError, exit1, no ready frame. Its configured localhost18787 service is absent on44. This is a confirmed remaining deployment dependency, not successful voice acceptance.
- The earlier S21 lease expired; this task currently holds none. Claim a new lease before further interactive phone access. Preserve the peer-owned frozen bridge. Protected NoticePlace MCP configuration and mesh DB access in SDK child processes still require reconciliation. Secretary/native investigation placement retains100 dependencies; accepted/UNKNOWN sessions must not be recreated on standbys.

## Remaining work

1. Reconcile the real standby MCP/authentication/configuration/DB dependencies with the voice and Herder owner, correct both44/88 deployment inputs, review and publish owned source, then prove a fresh real SDK startup and harmless operation without repeating the user's conversation.
2. Under a new S21 lease, verify the final APK through actual SIP exclusions; never interrupt an active call or automatically redial. Complete a fresh authorized, recipient-confirmed audible assistant path on a surviving node.
3. Resolve100-only Secretary/native investigation ownership without replaying accepted or uncertain jobs. Keep full HA acceptance open until these real consumer paths pass.
4. The user requested an architectural quorum on containers/Kubernetes with independent operation without Kubernetes. Three read-only reviewers agreed on portable OCI packaging first, common configuration/state contract, and optional Kubernetes after a measured pilot. This assessment does not authorize replacing the live stack or installing a cluster. HAOS/SSH/physical-device recovery must remain independent; automatic parallel native/Kubernetes failback risks duplicate external effects.

## Historical initial observations (superseded by latest checkpoint)

- Canonical NoticePlace source is `/home/roomhacker/agents-projects/noticeplace` on main. Remote main includes newer accepted repair-first and phone changes.
- Ten pre-existing staged ZCode paths overlap the incident runtime and tests. Preserve worktree and index bytes. Their historical ownership is explicit in the existing NoticePlace repair-first chat. Do not reset/stash/overwrite them to update main.
- `notification-center.service` runs on server-100, API `127.0.0.1:8091`, local SQLite `/var/lib/notification-center/notify-center.sqlite3` (562544640 bytes at observation). No NoticePlace service exists on44/88.
- Mesh Core is installed on100/44/88, but supplies topology/routing only. It does not replicate NoticePlace business state or elect a delivery owner.
- The phone path uses server-100 Asterisk, AgentCall AudioSocket bridge and local control socket. Merely adding HTTP replicas would leave calls dependent on100.
- At the last check the S21 lease was held by `exmanager-max-auth-20261013` for MAX authentication recovery. No interactive phone access, ADB, configuration change or call was attempted.
- PostgreSQL instances on100/44/88 are independent, with no existing synchronous replication observed. They are not an accepted shared HA store for this task.
- Public recovery ingress lives on HAOS. Its current NoticePlace routing still depends on100; it must be included in final failover verification.

Evidence: `.tmp/noticeplace-mesh-preflight/preflight.json` (source/index fingerprints and live observations, no credentials).

## Historical authorization and initial implementation bounds

The user explicitly authorized one Git worktree at `/home/roomhacker/agents-projects/noticeplace/.worktrees/noticeplace-mesh-ha-20261013`, starting from current published main, followed by reviewed integration into main. It was created. The user later explicitly authorized reviewing and publishing both ZCode and mesh source inputs. Original ZCode input is preserved in Git history and an ignored patch; reviewed repair-first integration passed171 relevant local-logic checks in82.622s.

The actual new Dqlite binary/lib and three real TLS-connected local processes passed HTTP intake/readback, transaction rollback, concurrent exclusive claims, actual leader crash and no replay of a sending reservation in2.54s. This is store integration only: no deployed mesh, notification recipient or phone proof yet.

After authorization, deliver a quorum-backed durable state adapter preserving actual SQLite transaction semantics, shared send reservations and UNKNOWN/no-redial behavior; install bounded NoticePlace replicas on100/44/88 using the existing mesh-core route; remove the phone path's sole dependence on100 after the S21 lease is released; wire independent public ingress. Reconcile all existing DB readers, including admin and Secretary call intake. Preserve source WIP and old accepted deliveries.

Acceptance: controlled exclusion of each service node separately, new real HTTP incident intake and durable readback through a remaining node, verified notification/incident processing, and one new recipient-confirmed audible phone call through the surviving path. HTTP200, process state and unit tests alone do not complete this task. Keep ordinary release checks within180s and give each runtime finite resource limits.

## Historical early delivery checkpoint (superseded)

Both the preserved ZCode input and reviewed mesh source were integrated into published main. The real three-voter TLS store is installed on100/44/88 and quorum was read back; no business database has been imported yet and the old API remains active.

The actual voice and Secretary readers passed16 SQLite regressions in7.530s and real Dqlite readback. Source helpers are published in the infrastructure repository. The real root-owned lease helper, SMS receipt monotonicity, frozen-source import/resume, actual leader crash and UNKNOWN preservation passed together in3.288s. Five narrow bookkeeping/admission unit checks passed in0.533s; these do not prove a phone call. The signed APK candidate built successfully in74s, with the installed signing identity retained; no APK was installed yet.

Own S21 lease is held temporarily for bootstrap inspection, ID retained privately; it must be renewed at20 active minutes and released in final cleanup. No call was originated. Native PBX had zero active calls at inspection. SMS reports currently use the AutoFind-owned file spool on100; the new receipt shim retains reports in the quorum and projects them to that existing consumer after100 recovers.

Stage-only replica installer retains credentials privately, starts a separate same-user shared Rust core and writes bounded API/private mTLS edge units. It does not start delivery workers. Standbys explicitly defer native investigation jobs owned by100; unknown native sessions must not be recreated on another host. Final acceptance must state this remaining dependency honestly.

Next: stage the reviewed immutable API release, standalone PBX/voice dependencies on44/88, freeze and import the business DB, switch the real readers and API, install the SIP failover APK under the lease, route public/recovery ingress, and verify actual consumer paths. No full NoticePlace HA or heard phone acceptance is claimed yet.
