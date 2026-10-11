---
name: noticeplace
description: Report incident progress to Notice Place and ask the operator one bound question whose answer returns to this same native session. Use while repairing a real service, when reporting progress on a Notice Place incident, or when a choice is genuinely yours to make.
---

# Notice Place plugin

Repair first, report briefly, and ask only what you cannot decide yourself.

## The one supported question path

A bound question needs two facts that only you have: your own native session
and the opaque request id from Agent Herder `human_request_create`.

1. Call Agent Herder `human_request_create` with **your** harness, `sessionId`,
   and `cwd`. That creates the Herder binding only — it does **not** deliver
   anything to the human on its own.
2. Call `noticeplace_ask_human` with that `request_id`, your `harness`,
   `session_id`, and `cwd`, plus the question text and its options. Notice Place
   persists the question, renders its card, and stores the native target.
   This call returns immediately; it never polls.
3. The answer arrives from Telegram, voice, or phone. Notice Place records it,
   marks the same card, and continues **your** session through Agent Herder's
   own completion seam. You do not poll for it and you do not start a second
   waiting process.

`ask_human` without a native target stays available for plain notifications and
blocking questions; it binds nothing and resumes nothing. Never combine it with
a manual resume.

## Reading and answering questions

- `noticeplace_pending_questions` returns the bounded question text, its
  options, the incident reference, and the exact native session. It never
  returns a stored answer, an attached document, or a secret payload.
- `noticeplace_answer` records one accepted answer for any resolver: a question
  (`request_id` plus `value`) or an incident choice card (`incident_id`,
  `request_id`, `choice_id`). The first accepted answer is canonical; a later
  conflicting repeat is reported as `conflict` and never re-marks the card.

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