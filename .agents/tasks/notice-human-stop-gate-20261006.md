# Notice: запрет продолжения остановленных сессий

Статус: source implementation; API root NOT READY, deploy/canary forbidden until ready.

Own Notice producer/helper/telemetry/tests only. Herder WIP belongs to root and is read-only.

Accepted contract: GET /api/coordination/context?harness=...&sessionId=...&cwd=...&consume=0 -> top-level humanStopHeld:boolean. Missing/invalid/error fails closed. Before related planner/remediation/retry/quota/start-plan fallback, check all verified receipt native IDs and send sourceSessions:[{harness,sessionId}], max32, plus immediate sourceHarness/sourceSessionId. Root unions sources and checks one fresh durable snapshot at every creation/send admission. No guessed ancestry/history/status, no humanRequested field, no consuming inbox, no hidden OpenCode path. >32 fails closed with manual handling; never silently truncate.

Reuse existing receipt events for diagnosis/planner/remediation; names/native IDs remain intact. New unrelated initial launch can have empty sources. Human-stop release belongs only to trusted human prompt/explicit Herder resume; Notice never releases it.

Budget unchanged: one serial focused Python run, ≤2CPU affinity/timeout180s, existing host session guard, temp/artifacts only .tmp/. No native launch or browser/deploy until API-ready handoff. Existing Notice call-limit runtime89aa85d and source387853c remain accepted; root Herder/Codex/Mac services untouched.

Source verification:128 first related checks passed; 11 direct admission regressions passed; final related suites220/220 passed (85.575s), including manual-card preservation from ee4f599. After final ordinary/manual receipt fix,31/31 source/manual/interactions checks passed (24.090s). Ordinary/manual retry adds source_sessions outside the original-health-event block, so native ancestry remains even when _health_original_event is None. Manual source-stop/invalid chain yields one informational terminal cancellation without a queued retry.

Source GET budget:≤5s per request,15s total,64KiB response; max32 source identities. Source records come from accepted diagnosis/planner/remediation session events and legacy accepted plan-batch native IDs, no title/status/trace guessing. All-source union preserved across initial related retry, planner handoff, quota retry and accepted-session start-plan replacement; original native harness/ID retained when chosen target differs. HumanRequested input is ignored; never present in request bodies. Held/missing/invalid authority rejects before POST; >32 raises manual-handling error rather than truncating.

Shared source:manual-card ee4f599 captured core/http_api receipt/terminal-hook hunks while source_gate was untracked. Owner confirmed no runtime changes; retain its card marker/url/in-place migration. This scoped publication supplies the complete module/helper/Hub dependencies. Herder API is still NOT READY; source checks do not claim live native stop acceptance. No deploy/canary or settings writes.
