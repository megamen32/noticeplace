# NoticePlace

## What This Is

NoticePlace delivers durable service incidents and agent results through configured notification and call adapters. This brownfield slice adds ordinary Secretary phone messages to the existing AgentCall adapter for Nikita.

## Core Value

Deliver the authorized message once through the existing phone consumer path and preserve its receipt.

## Requirements

### Validated

- ✓ Durable events, consumer delivery policies and phone receipts exist — existing canonical source.
- ✓ Incident and operator-test call modes exist — existing AgentCall adapter and tests.

### Active

- [ ] Secretary phone messages introduce Nikita's assistant and use ordinary conversation context.
- [ ] Incident call behavior remains compatible.
- [ ] Root activates published source and proves a new audible Secretary call.

### Out of Scope

- Arbitrary destination dialing — initial typed API supports only the configured owner.
- New dispatcher/storage — existing NoticePlace queue owns delivery.
- Phone/runtime changes by this worker — root owns activation and physical device.

## Context

User requests normal assistant conversation for external callers and X-manager Secretary call capability in parallel worktrees. Admin owns typed durable intake; this isolated NoticePlace slice owns only existing phone adapter wording/context. HTTP/core WIP in canonical checkout belongs to another owner.

## Constraints

- **Compatibility**: Existing incident wording, context and two spoken repetitions remain unchanged.
- **Delivery**: Preserve canonical provider timeout/no-redial behavior.
- **Scope**: Secretary context uses existing incident.operator_note ≤500 characters.
- **Acceptance**: Fake-provider source integration supports review; audible live canary belongs to root.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| secretary.call explicit event type | Ordinary conversation avoids incident instructions | Pending |
| One spoken repetition | User-authorized ordinary call should not repeat incident opening | Pending |

## Evolution

Update active requirements after verified delivery; preserve existing validated capabilities and task scope.

---
*Last updated: 2026-10-12 after brownfield initialization*
