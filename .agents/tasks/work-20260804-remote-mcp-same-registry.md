# Notify remote MCP with the exact local tool registry

Role: Worker

## Goal

Add authenticated HTTP MCP at the existing Notify service so remote and local
stdio expose the exact same existing tool registry and handlers.

## Confirmed contract

- Remote endpoint is `/mcp` on the existing Notify HTTP service/site.
- `tools/list` and every `tools/call` name/handler match current local stdio
  `notify_mcp.py` exactly, including job tools.
- Remote calls execute on the central Notify host; no second Notify service,
  duplicate registry, or remote-only semantic policy.
- `Authorization: Bearer <NOTIFY_MCP_TOKEN>` gates the remote transport.
- Existing `/v1/events`, local stdio MCP, token scopes, admin, and delivery
  behavior remain compatible.

## Allowed paths

`mcp/notify_mcp.py`, `notification_center/http_api.py`,
`bin/notify-center`, `deploy/notification-center.env.example`, and the smallest
directly relevant tests under `tests/`.

## Excluded

No real token, service restart, nginx/systemd apply, public ingress change,
deployment, or unrelated refactor.

## Required workflow

Write a focused failing regression first. Refactor toward one reusable registry
or dispatcher so stdio and HTTP cannot drift. Add remote Bearer verification
without printing secret values. Run focused tests and the relevant build/check.
Append detailed Red/Green evidence, changed files, and exact remaining runtime
canary to this task; return only TL;DR to L.

## Acceptance

Unauthorized `/mcp` is rejected; authenticated HTTP `initialize`, `tools/list`,
and a safe `tools/call` use the same contract/registry as stdio; test asserts
the two tool lists are exactly equal.

## Status

Active.

## План

- Зафиксировать разрыв между текущим локальным stdio MCP и HTTP `/mcp`, затем вынести общий реестр/диспетчер.
- Добавить авторизацию `Authorization: Bearer <NOTIFY_MCP_TOKEN>` для remote MCP без утечки значений токена.
- Написать регрессию, которая сравнивает `tools/list` у stdio и HTTP и проверяет rejection для неавторизованного запроса.
- Прогнать узкие тесты по MCP/HTTP и сохранить evidence.

## Границы

- Не трогаю реальный секрет, рестарт сервиса, nginx/systemd и деплой.
- Не расширяю рабочий объём за пределы одного общего registry/dispatcher и HTTP `/mcp`.

## Оценка

- Initial active minutes: 45

## Progress

- Red: `python -m pytest tests/test_http_api.py -k remote_mcp_requires_bearer_and_matches_stdio_registry -q` failed with `404 != 401` on `POST /mcp`, proving the remote MCP route was missing.
- Green: Added a shared MCP JSON-RPC dispatcher in `mcp/notify_mcp.py` (`tool_specs`, `dispatch`) and wired `notification_center/http_api.py` to serve `POST /mcp` with `Authorization: Bearer <NOTIFY_MCP_TOKEN>`.
- Green: Updated `bin/notify-center` to pass `NOTIFY_MCP_TOKEN`, and `deploy/notification-center.env.example` to document it.
- Green: Updated affected HTTP tests to supply the new bearer token and assert that remote `tools/list` exactly matches the stdio manifest and that `tools/call` for `list_jobs` matches the local handler output.
- Verification: `python -m pytest tests/test_http_api.py tests/test_notification_center.py tests/test_notification_center_clients.py -q` → `22 passed in 8.81s`.
- Changed files: `mcp/notify_mcp.py`, `notification_center/http_api.py`, `bin/notify-center`, `deploy/notification-center.env.example`, `tests/test_http_api.py`, `tests/test_notification_center.py`, `tests/test_notification_center_clients.py`, `mcp/__init__.py`.
- Remaining risk: HTTP `/mcp` now depends on `NOTIFY_MCP_TOKEN` being configured in the service environment; I did not restart or deploy the service.

## Reviewer-required parity repair

Reviewer found `CHANGES_REQUIRED`: HTTP `/mcp` dispatches job tools without
calling `ensure_dirs()`, so `run_and_notify` fails on a clean host while stdio
works. Repair the shared initialization boundary so both transports initialize
identically. Add a focused regression for clean remote job-tool initialization,
then run the same focused suite. Do not change tool names, add policy, restart,
deploy, or touch real configuration/token files.

## Execution transport note

The first reassigned Worker produced no task-file evidence or source repair and
its agent handle disappeared. The parity defect remains active; a replacement
Worker owns only this same repair.

## Replacement assignment

Repair the `ensure_dirs()` parity defect now. The HTTP `/mcp` transport must
initialize the same shared job state before any `tools/call`, so a clean remote
`run_and_notify` behaves exactly like stdio. First add/run the focused failing
regression, then make it green and rerun the focused Notify suite. Do not
change tool names/policy, restart, deploy, or touch real configuration/token
files. Append detailed Red/Green evidence and changed paths here; return only
TL;DR.

## Final Reviewer findings

1. `ensure_runtime_ready()` is called both by stdio `main()` and by dispatch,
   so the claimed exactly-once initialization is false.
2. The clean-host regression mocks initialization and calls `list_jobs`; it
   must instead prove real `run_and_notify` succeeds after HTTP dispatch creates
   the job runtime.

Repair only these two findings. Preserve the exact same local/remote tool
registry and bearer contract.

## Worker evidence update

- Red regression added in `tests/test_http_api.py`: `test_remote_mcp_initializes_shared_runtime_before_job_tools` asserts the shared dispatcher calls `ensure_runtime_ready()` on `tools/call` before job-tool execution.
- Source repair: moved the runtime initialization boundary into `mcp/notify_mcp.py::dispatch()` for `initialize` and `tools/call`, and removed the HTTP-side duplicate call from `notification_center/http_api.py` so both transports use the same dispatcher path.
- Changed paths: `mcp/notify_mcp.py`, `notification_center/http_api.py`, `tests/test_http_api.py`.
- Verification: `python -m pytest tests/test_http_api.py -k 'remote_mcp_requires_bearer_and_matches_stdio_registry or remote_mcp_initializes_shared_runtime_before_job_tools' -q` → `2 passed`.
- Verification: `python -m pytest tests/test_http_api.py tests/test_notification_center.py tests/test_notification_center_clients.py -q` → `23 passed in 9.51s`.
- Remaining runtime canary: HTTP `/mcp` still depends on `NOTIFY_MCP_TOKEN` being configured in the service environment; I did not restart, deploy, or touch real config/token files.

## Worker follow-up on final reviewer findings

- Removed the duplicate stdio-side runtime bootstrap from `mcp/notify_mcp.py::main()`, so the shared initialization boundary now lives in the dispatcher instead of being invoked both by `main()` and `dispatch()`.
- Reworked the regression in `tests/test_http_api.py` so it proves a real HTTP `tools/call` for `run_and_notify` succeeds on a clean temporary state directory, using a temporary fake `notify` binary and verifying the created job state plus stable fields against the stdio handler output.
- Final verification:
  - `python -m pytest /home/roomhacker/agents-projects/notify/tests/test_http_api.py -k 'remote_mcp_requires_bearer_and_matches_stdio_registry or remote_mcp_initializes_shared_runtime_before_job_tools' -q` → `2 passed, 8 deselected in 1.18s`
  - `python -m pytest /home/roomhacker/agents-projects/notify/tests/test_http_api.py /home/roomhacker/agents-projects/notify/tests/test_notification_center.py /home/roomhacker/agents-projects/notify/tests/test_notification_center_clients.py -q` → `23 passed in 9.78s`
- Changed paths in this follow-up: `mcp/notify_mcp.py`, `tests/test_http_api.py`
- Remaining runtime canary: still no restart/deploy and no touch of real service config or token files; `/mcp` continues to require `NOTIFY_MCP_TOKEN` in the live service environment.

## Worker follow-up evidence

- Red rerun: `python -m pytest tests/test_http_api.py -k 'remote_mcp_requires_bearer_and_matches_stdio_registry or remote_mcp_initializes_shared_runtime_before_job_tools' -q` initially failed because the regression compared the remote `run_and_notify` result against the unpatched stdio handler after the mock runtime context had already ended.
- Test repair: moved the stdio `run_and_notify` call inside the same isolated patch block in `tests/test_http_api.py` and compared stable contract fields (`ok`, `cwd`, `notify_attached`, `hard_timeout`, `wait_seconds`) instead of the non-idempotent full structured result.
- Changed paths in this pass: `tests/test_http_api.py`.
- Verification after repair: `python -m pytest tests/test_http_api.py -k 'remote_mcp_requires_bearer_and_matches_stdio_registry or remote_mcp_initializes_shared_runtime_before_job_tools' -q` → `2 passed, 8 deselected in 1.19s`.
- Verification of the focused Notify suite: `python -m pytest tests/test_http_api.py tests/test_notification_center.py tests/test_notification_center_clients.py -q` → `23 passed in 9.44s`.
- Remaining runtime canary: unchanged; remote `/mcp` still needs `NOTIFY_MCP_TOKEN` in the service environment, and I did not restart, deploy, or modify real config/token files.

## Final review follow-up

Independent review found one remaining defect: `dispatch()` calls
`ensure_runtime_ready()` for both `initialize` and `tools/call`. Make the
initialization itself process-once while preserving direct remote `tools/call`
on a clean host. First add/run a focused Red regression proving an initialize
then tool-call sequence has one effective initialization; retain the real
clean-host HTTP `run_and_notify` regression. No deploy, restart, config, or
real event.

## Reviewer clarification

The idempotent guard made bootstrap effective once, but the reviewer correctly
requires the dispatcher to invoke it only once in an `initialize` then
`tools/call` stdio sequence. `initialize` does not need job state. Repair the
condition so only `tools/call` initializes the runtime; preserve direct remote
tool calls and existing regressions. Add focused Red/Green evidence.

## Worker final repair evidence

- Red regression added in `tests/test_http_api.py`: `test_remote_mcp_initialize_then_tool_call_only_effectively_initializes_once` patches `mcp.notify_mcp.ensure_dirs` and asserts an authenticated `initialize` followed by authenticated `tools/call` only performs one effective runtime bootstrap.
- Source repair: added a process-local guard in `mcp/notify_mcp.py::ensure_runtime_ready()` using `_RUNTIME_READY` plus `_RUNTIME_READY_LOCK`, so `initialize` and `tools/call` can both enter the shared dispatcher path while the filesystem bootstrap runs once per process.
- Test repair: reset `_RUNTIME_READY` inside the new regression so the count assertion is stable regardless of prior `/mcp` traffic in the same pytest process.
- Changed paths: `mcp/notify_mcp.py`, `tests/test_http_api.py`.
- Verification:
  - `python -m pytest tests/test_http_api.py -k 'initialize_then_tool_call_only_effectively_initializes_once' -q` → `1 passed, 10 deselected in 0.66s`
  - `python -m pytest tests/test_http_api.py tests/test_notification_center.py tests/test_notification_center_clients.py -q` → `24 passed in 10.06s`
- Remaining runtime canary: unchanged; live `/mcp` still requires `Authorization: Bearer <NOTIFY_MCP_TOKEN>` in the service environment, and I did not restart, deploy, or touch real config/token files.
