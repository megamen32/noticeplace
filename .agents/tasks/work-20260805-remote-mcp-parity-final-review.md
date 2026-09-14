# Final review: Notify remote MCP parity

## Role

Reviewer

## Goal

Independently review the current uncommitted Notify remote MCP parity diff after
the repair. Decide whether it is safe to commit as source-only work.

## Contract

`POST /mcp` with a valid Bearer token must expose and execute exactly the same
MCP tool registry and dispatch semantics as local stdio, including
`run_and_notify`. Authorization is the only remote difference. The common
dispatcher owns runtime initialization exactly once; a clean temporary state
directory must allow the job tool to run.

## Allowed paths

`mcp/notify_mcp.py`, `mcp/__init__.py`, `notification_center/http_api.py`,
`bin/notify-center`, `deploy/notification-center.env.example`, and directly
relevant tests. Read-only git inspection is allowed.

## Forbidden

No edits, commit, restart, deploy, secret/config changes, or real Notify event.

## Required evidence

Check the actual diff and tests, especially:

1. `ensure_runtime_ready()` cannot execute twice in stdio request flow.
2. The regression executes real HTTP `run_and_notify` from a clean temporary
   state directory, rather than mocking initialization or only listing tools.
3. Bearer rejection and full registry parity remain intact.

Append detailed verdict and evidence here. Return L only `APPROVE` or
`CHANGES_REQUIRED` with a one-line reason.

## Review result

Verdict: CHANGES_REQUIRED

Evidence:

- `mcp/notify_mcp.py:643-644` calls `ensure_runtime_ready()` for both `initialize` and `tools/call`.
- That means a normal stdio lifecycle (`initialize` -> `tools/list` -> `tools/call`) invokes runtime preparation twice, which violates the contract item that runtime initialization must happen exactly once.
- The new HTTP `/mcp` path in `notification_center/http_api.py:347-357` correctly dispatches through the shared MCP handler and enforces bearer auth.
- `tests/test_http_api.py:186-238` exercises bearer rejection, registry parity, and a real HTTP `run_and_notify` call from a patched temporary state directory; this part passed.
- Focused validation run:
  - `python -m pytest -q tests/test_http_api.py` → 10 passed
  - `python -m pytest -q tests/test_notification_center.py tests/test_notification_center_clients.py` → 13 passed

Smallest in-scope fix:

- Move runtime preparation to a one-time initialization path for stdio, or guard `ensure_runtime_ready()` so it only runs once per process before the first job-tool dispatch.

Additional independent review:

- `mcp/notify_mcp.py:653-665` still calls `ensure_runtime_ready()` for both `initialize` and `tools/call`, so the stdio dispatcher enters the bootstrap path twice in the normal lifecycle.
- I verified that directly by instrumenting `dispatch()` with a call counter across `initialize -> tools/list -> tools/call`; the counter reached `2`, even though the second invocation was a no-op because `_RUNTIME_READY` was already set.
- `python -m pytest -q tests/test_http_api.py` passed (`11 passed in 6.43s`), so the HTTP parity and bearer gating tests are green, but they do not eliminate the double-invocation issue in the stdio flow.

Final verdict: CHANGES_REQUIRED

Reason: the selected diff still invokes the runtime bootstrap hook twice in the standard stdio path, which is outside the requested exactly-once contract.

Review result: APPROVE

Evidence:

- `mcp/notify_mcp.py:648-677` now routes all JSON-RPC through a single `dispatch()` path, and `ensure_runtime_ready()` is called only for `tools/call` at `mcp/notify_mcp.py:653-654`, so the stdio lifecycle no longer bootstraps runtime on `initialize` or `tools/list`.
- `notification_center/http_api.py:347-357` gates `POST /mcp` behind `NOTIFY_MCP_TOKEN` and forwards the body to the shared MCP dispatcher, which keeps the HTTP path on the same registry/dispatch semantics as stdio.
- `tests/test_http_api.py:186-261` now covers bearer rejection, exact stdio registry parity, a real HTTP `run_and_notify` call from a patched temporary state directory, and the once-only bootstrap check.
- `python -m pytest -q tests/test_http_api.py tests/test_notification_center.py tests/test_notification_center_clients.py` → `24 passed in 10.03s`.

Conclusion: the current uncommitted diff satisfies the requested remote MCP parity contract and is safe to commit as source-only work.
