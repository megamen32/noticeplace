# Roadmap: NoticePlace Secretary call slice

## Phases

- [ ] **Phase 1: Ordinary Secretary phone adapter** - Reuse existing delivery and preserve incident compatibility.

## Phase Details

### Phase 1: Ordinary Secretary phone adapter
**Goal**: Nikita's Secretary delivers ordinary assistant messages through the existing call adapter.
**Depends on**: Existing NoticePlace durable queue and root-owned typed intake.
**Requirements**: PHONE-01, PHONE-02, PHONE-03
**Success Criteria**:
1. secretary.call opening introduces assistant and speaks message once.
2. Ordinary context excludes incident and Herder instructions.
3. Existing incident/lead/operator adapter tests pass.
4. Root verifies a new audible consumer call after activation.
**Plans**: 1 plan

Plans:
- [ ] 01-01: Add ordinary call mode and focused regressions, publish for root activation.

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Ordinary Secretary phone adapter | 0/1 | In progress | - |
