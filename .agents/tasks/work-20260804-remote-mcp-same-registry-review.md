# Review Notify remote MCP exact-registry diff

Role: Reviewer

## Goal

Independently review the Notify remote `/mcp` implementation against the
confirmed exact-local-registry contract.

## Confirmed scope

`mcp/notify_mcp.py`, `mcp/__init__.py`, `notification_center/http_api.py`,
`bin/notify-center`, `deploy/notification-center.env.example`, and directly
related tests only.

## Acceptance

- HTTP and stdio share one registry/dispatch rather than duplicate tool lists.
- HTTP Bearer rejects missing/wrong token without leaking it.
- Remote `tools/list` and `tools/call` retain every current local tool/handler.
- Existing event/admin behavior remains compatible.
- Name exact source-level defects or append `APPROVE`.

## Exclusions

Read-only. Do not edit, rerun tests, restart, deploy, read real config/token,
or inspect unrelated files.

## Status

Active.

## Reviewer evidence

- Reviewed selected diff in `mcp/notify_mcp.py`, `mcp/__init__.py`, `notification_center/http_api.py`, `bin/notify-center`, `deploy/notification-center.env.example`, and directly related tests. No tests, deployment, restart, or real configuration/token were read or run.
- Finding (medium): remote HTTP dispatch does not initialize the MCP state directory. `notification_center/http_api.py:352-353` calls `notify_mcp_dispatch` directly, while `mcp/notify_mcp.py:673-675` performs `ensure_dirs()` only from stdio `main()`. Consequently the remote `run_and_notify` handler reaches `mcp/notify_mcp.py:280` (`job_dir.mkdir(..., exist_ok=False)`) with a missing `JOBS_DIR` parent and fails on a fresh deployment, so the HTTP endpoint does not retain every current handler's behavior. Smallest fix: initialize the shared state directory before HTTP dispatch (or make the handler's directory creation recursive/idempotent).
- No other source-level defect was established within the confirmed scope: HTTP uses `dispatch`/`tool_specs` from the same `TOOLS` registry; bearer failures return a generic unauthorized body without token echo; event/admin routes were not structurally altered beyond the added MCP branch.

## Result

CHANGES_REQUIRED. The state-directory initialization defect must be fixed and reviewed again. Unverified: runtime behavior and full test canary were intentionally not run under the task exclusions.
