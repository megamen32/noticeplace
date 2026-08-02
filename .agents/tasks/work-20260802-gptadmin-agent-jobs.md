# Задача: allowlisted GPTAdmin agent jobs из Notify

## Исходный запрос

Продолжить полный путь Notify → подписанный GPTAdmin webhook → allowlisted
ShellMCP host → Agent Herder `new_or_resume`, сначала server-100, затем Mac.

## Цель

Добавить durable Notify delivery для token-scoped agent job, вернуть terminal
GPTAdmin result в bounded audit receipt и доказать реальный двух-host E2E.

## Business canary

Описан в родительской задаче
`/home/roomhacker/gptadmin/.agents/tasks/work-20260802-notify-webhook-agent-orchestration.md`:
один event/key создаёт один Hub job и одну native session delivery, переживает
Notify/Hub restart и возвращает terminal receipt; затем тот же контракт на Mac.

## Confirmed scope

- Notify token allowlist, HMAC POST/GET, durable delivery retry and audit receipt.
- Mode-0600 helper profile with exact loopback Agent Herder endpoint.
- Tests, docs, server-100 deploy/canary, then Mac deploy/canary.

## Explicit exclusions

- No payload-controlled target, command, URL, harness, CWD, prompt, MCP/tool,
  credential, secret or callback URL.
- No new webhook ingress, OAuth/HAOS changes or unrelated task cleanup.

## Классификация и оценка

- Classification: Full (selected Normal architecture).
- Initial optimistic active-minute estimate: 120 minutes.
- Initial likely active-minute estimate: 240 minutes.
- Initial pessimistic active-minute estimate: 420 minutes.

## План (RU)

1. RED/GREEN unit and HTTP contracts.
2. Server-100 route/helper/profile/runtime canary including retries/restarts.
3. Mac prerequisite audit and same canary.
4. Review, docs, commit/push and final completion audit.

## Progress (EN, append-only)

- 2026-08-02: RED proved the outbound adapter and helper did not exist.
- 2026-08-02: Implemented token-scoped scheduling, HMAC POST plus terminal GET
  polling with stable delivery-key idempotency, bounded receipt audit, strict
  dangerous-field rejection, and a mode-0600 exact-loopback helper profile.
  Focused tests are 7/7; pre-doc full suite was 75/75.
- 2026-08-02: Reviewer RED regressions require HMAC v2 method/path/key binding,
  terminal-failure persistence without retry, environment-only event transfer,
  and 64 KiB response bounds. Implementation updated; verification pending.
- 2026-08-02: Follow-up review found an ACK/late-failure race and raw remote
  failure text reaching delivery audit. RED reproduced both; closed incidents
  now retain cancellation and terminal remote failures use a fixed safe reason.
