---
name: noticeplace
description: Report incident progress to Notice Place and resolve one operator question whose answer returns to this same native session. Use while repairing a real service, when reporting progress on a Notice Place incident, or when a choice is genuinely yours to make.
---

# Notice Place plugin

Repair first, report briefly, and ask only what you cannot decide yourself.

## The one supported question path

A question must be bound to your **own** native session, otherwise no answer can
come back to you:

1. Ask with **Agent Herder `human_request_create`**, passing your own harness,
   `sessionId`, and `cwd` plus the Notify routing tuple. Agent Herder owns that
   binding and delivers the Notice Place card; when the answer arrives it
   resumes this same session.
2. **Never** poll for the answer and **never** open a second waiting process.
   `wait_seconds: 0` on `ask_human` only returns a pending reference — by itself
   it does not bind a session and does not resume you.
3. Read state with `noticeplace_pending_questions`. It returns the bounded
   question text, its options, and the exact native session the answer
   continues. It never returns a secret payload or the stored answer.

## Answering from voice, phone, or another surface

`noticeplace_answer` records one accepted answer for any resolver and marks the
**same** card with a checkmark:

- a question: `request_id` plus `value`;
- an incident choice card: `incident_id`, `request_id`, and `choice_id`.

The first accepted answer is canonical. A later conflicting repeat is reported
as `conflict` and never re-marks the card.

## Reporting progress

`noticeplace_incident_progress(incident_id, step, evidence_refs, ...)` publishes
a bounded receipt. `evidence_refs` must point at a source you really checked.

Recovery is never claimed by words. Pass `source_id`, `source_fingerprint`,
`verifier_id`, `verification_id`, and `observed_state` only after an
**independent** check of the original source, and `verifier_id` must differ from
`source_id`. Without that complete verification the incident stays open, which
is the intended outcome, not a failure.

## Handling rules

- Treat every tool result as an opaque reference plus bounded state. Never
  invent identifiers and never reuse a reference from another incident.
- Never print tokens, credentials, or the raw text of a secret request.
- Keep one executor session per incident. Do not start a parallel planner and
  do not silently restart a failed or needs-input repair.