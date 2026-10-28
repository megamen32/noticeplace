# L27 persistent protected-profile traversal

Canonical cause: GPTAdmin SecretStore constructor and atomic writes used ensurePrivateDir(configDir)/chmod0700 on shared /etc/gptadmin, resetting the POSIX ACL mask and revoking named-user traversal. Profiles themselves remaineduid1000/0600.

Narrow runtime repair applied nameduid traversal and mask--x only; other/root-only foreign files unchanged. Real POSIX setfacl regression RED reproduced the exact effective--- mask. Reviewed source `01a39b887018ce4be0f51f7184f04b59142440e4` published to origin/main, only secret_store.go/test. Explicit config-parent seam preserves existing traverse mask, forbids groupread/write and other permissions; no hardcodedUID or new grant. Private secret dirs0700/files0600 remain strict. Symlinkconfigparent rejected. Admin proxy default private /var/lib directory correctly leftstrict0700. Targeted SecretStore+ProxyRoute tests GREEN11.280s.

Canonical clean-source Node package built using tools/build_node.py, source_dirty=false; package SHA256 `8026479238218847ff44c16eda0e00e4dec22c5f41487da529c1532342d81c74`.

First --require-drain update deadline50s aborted before release switch; olda6252c50/PID1777542/start10:41/NRestarts0 remainedactive/versionHTTP200; authenticated drain status confirmed draining=false, no unverifiedaccepted install. Two other external-site health jobs were in flight and were not interrupted.

One subsequent canonical --require-drain --wait-secs300 update is running; runtime acceptance is pending. Do not mark L27done before actual newversion/readback and uid1000 own-profile/callback read plus foreign root-only denial.

## Runner drain classification repaired

Second canonical wait300s refused because eight never-dispatched read-only catalog requests were counted as unfinished executions. Exact records had relay/statusqueued/methodtools-list, no started/completed time, params/result/error or associated shell execution. Their localowner matched the actual live process's open task-owner lock. No counter/DB rewriting or business-job cancellation was used.

Reviewed counter fix `443f941bdfcb2857c9e0ca43458479c5cb629737` published after meaningful RED→GREEN; all TestNodeDrain passed0.820s. Only never-started queued tools/list with no result/error/parent is excluded; queued/running tools/call and running/UNKNOWN/started/accepted-result discovery continue to fence updates. Durable receipts remain unchanged.

Owner explicitly authorized cancellation of exactly eight stale read-only schema requests through existing authenticated MCP tasks/cancel. Each record was re-read immediately before its one request and confirmed cancelled via tasks/get. No accepted/UNKNOWN cancellation retried. Receipt `/home/roomhacker/gptadmin/.tmp/l27-node-package/cancel-discovery-receipts.json`, SHA256 `062a9a90d06a4ccfa90618e1302fcf29d15b5ad03c09c325464b6b1943185bab`; local audit/request reason: «Отмена устаревшего запроса каталога перед обновлением». Provider durable status is truthfullycancelled, neverfakecompleted.

Final clean-source package and canonical update acceptance still pending; source-only status is not runtime completion.

## Final installed acceptance — 2026-10-28 17:35 UTC

Final source `443f941bdfcb2857c9e0ca43458479c5cb629737` authoritative origin/main verified; clean canonical package source_dirty=false, archive SHA256 `8c58b51d60ed76193ddb2f784dfeedb0bf272c112e6e4e102b71fd2cb733de74`. Contains ACL01a39b88 and drainclassification443f941b.

After eight truthful supported cancellations and normal drain, canonical update --require-drain --wait-secs300 succeeded without force/DBrewrite or compatibility bypass. Actual /version reports443f941b; current release `/opt/gptadmin-node/versions/443f941bdfcb2857c9e0ca43458479c5cb629737-1`; live process.exe matches manifest binary SHA256 `35e23b96e129657cf2a00a20a2248d582b28c1b65b396a27679bf10f6fa96274`. MainPID2233575, active since20:35:47MSK, NRestarts0; drain cancelled and all counterszero. Previousrelease kept.

Postrestart actualuid1000 reads both existing healthprofiles and exactcallback, modes0600 preserved. /etc/gptadmin namedroomhacker--x/mask--x stays effective; mtproxy/group/other--- remain denied. Foreignroot-only gptadmin.env remains unreadable byuid1000. No secret changed or printed.

RECEIPT-L27.json captures installedproof. No extra incident/native/notification created. Retention previews exit0, no leafcreated garbage matches.
