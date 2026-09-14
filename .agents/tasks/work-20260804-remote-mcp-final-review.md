# Final review Notify remote MCP parity repair

Role: Reviewer

## Goal

Independently review the final remote `/mcp` parity repair.

## Confirmed scope

`mcp/notify_mcp.py`, `notification_center/http_api.py`, and direct remote-MCP
tests only. The accepted contract is one exact local/remote registry and shared
runtime initialization for all tools, including job tools.

## Acceptance

Confirm dispatch initializes shared runtime exactly once per process path,
stdio and HTTP retain identical tools/handlers, and the clean-host job-tool
regression proves the prior defect fixed. Append `APPROVE` or
`CHANGES_REQUIRED`; return only TL;DR.

## Exclusions

Read-only. Do not edit, rerun tests, restart, deploy, read real token/config,
or reduce the remote tool surface.

## Status

Active.
