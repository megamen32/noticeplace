# Mandatory full-cycle supertest contract

Status: complete

Original request: записать весь цикл как обязательный супертест.

Objective: make the business E2E gate explicit and reusable for presentation, release, and production routing changes.

Acceptance: one correlation id must prove source webhook → GPTAdmin → Agent Herder → selected executor → Notify incident/delivery → S21 dialing, followed by acknowledgement/cleanup. Direct phone calls and historical receipts do not satisfy the gate.

Safety: dry-run by default; live phone mode requires explicit operator confirmation; no quiet-hours or global call-setting mutation.

Artifact: `docs/mandatory-e2e-supertest.md`, linked from all three README languages.
