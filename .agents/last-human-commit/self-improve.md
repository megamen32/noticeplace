## 2026-08-08 — Notify admin auth recovery (Short)

- What slowed or confused L? The initial symptom looked like auth failure, but a valid synthetic cookie exposed a downstream 502 from the admin backend.
- Which instruction should change? none
- Which skill, MCP, or tool is missing? none; nginx plus service journal gave the claim-relevant cause.
- What operation or error repeated? 3 readonly SQLite failures in the admin journal; guard shared-database services with explicit ProtectSystem ReadWritePaths.
- State: fixed now

## 2026-08-09 — merge completed NoticePlace branches (Full)

- What slowed or confused L? A stale temporary worktree made the local main pointers look like extra active branches.
- Which instruction should change? `/home/roomhacker/agents-projects/AGENTS.md` — default to main and require explicit approval for branch creation.
- Which skill, MCP, or tool is missing? none
- What operation or error repeated? none; all unique commits were reviewed, merged, tested, and the obsolete feature branch was deleted.
- State: fixed now

## 2026-08-09 — NoticePlace unified naming (Full)

- What slowed or confused L? “Everywhere” included public labels and runtime units but also legacy API identifiers that must remain compatible.
- Which instruction should change? none
- Which skill, MCP, or tool is missing? none; a bounded naming inventory and live alias canary were sufficient.
- What operation or error repeated? none; preserved `NOTIFY_CENTER_*`, `notification_center`, and database paths deliberately as compatibility surfaces.
- State: fixed now
