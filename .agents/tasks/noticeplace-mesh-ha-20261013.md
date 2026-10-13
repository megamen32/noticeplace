# Notice Place: survive loss of one mesh service node

Owner: Codex current chat `01a1328a-6739-7df3-9514-ecac740951a8`.
Requested: HTTP/curl requests, incident intake and processing, and telephone calls must continue when one of the service nodes (100,44,88) fails.
Status: **in progress; user authorized isolated worktree and review/publication of both source inputs**.

## Verified current state

- Canonical NoticePlace source is `/home/roomhacker/agents-projects/noticeplace` on main. Remote main includes newer accepted repair-first and phone changes.
- Ten pre-existing staged ZCode paths overlap the incident runtime and tests. Preserve worktree and index bytes. Their historical ownership is explicit in the existing NoticePlace repair-first chat. Do not reset/stash/overwrite them to update main.
- `notification-center.service` runs on server-100, API `127.0.0.1:8091`, local SQLite `/var/lib/notification-center/notify-center.sqlite3` (562544640 bytes at observation). No NoticePlace service exists on44/88.
- Mesh Core is installed on100/44/88, but supplies topology/routing only. It does not replicate NoticePlace business state or elect a delivery owner.
- The phone path uses server-100 Asterisk, AgentCall AudioSocket bridge and local control socket. Merely adding HTTP replicas would leave calls dependent on100.
- At the last check the S21 lease was held by `exmanager-max-auth-20261013` for MAX authentication recovery. No interactive phone access, ADB, configuration change or call was attempted.
- PostgreSQL instances on100/44/88 are independent, with no existing synchronous replication observed. They are not an accepted shared HA store for this task.
- Public recovery ingress lives on HAOS. Its current NoticePlace routing still depends on100; it must be included in final failover verification.

Evidence: `.tmp/noticeplace-mesh-preflight/preflight.json` (source/index fingerprints and live observations, no credentials).

## Exact next action and bounds

The user explicitly authorized one Git worktree at `/home/roomhacker/agents-projects/noticeplace/.worktrees/noticeplace-mesh-ha-20261013`, starting from current published main, followed by reviewed integration into main. It was created. The user later explicitly authorized reviewing and publishing both ZCode and mesh source inputs. Original ZCode input is preserved in Git history and an ignored patch; reviewed repair-first integration passed171 relevant local-logic checks in82.622s.

The actual new Dqlite binary/lib and three real TLS-connected local processes passed HTTP intake/readback, transaction rollback, concurrent exclusive claims, actual leader crash and no replay of a sending reservation in2.54s. This is store integration only: no deployed mesh, notification recipient or phone proof yet.

After authorization, deliver a quorum-backed durable state adapter preserving actual SQLite transaction semantics, shared send reservations and UNKNOWN/no-redial behavior; install bounded NoticePlace replicas on100/44/88 using the existing mesh-core route; remove the phone path's sole dependence on100 after the S21 lease is released; wire independent public ingress. Reconcile all existing DB readers, including admin and Secretary call intake. Preserve source WIP and old accepted deliveries.

Acceptance: controlled exclusion of each service node separately, new real HTTP incident intake and durable readback through a remaining node, verified notification/incident processing, and one new recipient-confirmed audible phone call through the surviving path. HTTP200, process state and unit tests alone do not complete this task. Keep ordinary release checks within180s and give each runtime finite resource limits.

No product source, deployed service, public routing, database contents or phone configuration was changed in this preflight. This task is not complete.
