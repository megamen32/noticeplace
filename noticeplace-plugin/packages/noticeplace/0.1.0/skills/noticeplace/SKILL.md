---
name: noticeplace
description: Report incident progress to Notice Place and ask the operator one bound question whose answer returns to this same native session. Use while repairing a real service, when reporting progress on a Notice Place incident, or when a choice is genuinely yours to make.
---

# Notice Place plugin

Repair first, report briefly, and ask only what you cannot decide yourself.

## The one supported question path

Ask with **Agent Herder `human_request_create`** first. It only binds your own
harness, `sessionId`, and `cwd` in Herder and delivers **no** card by itself.
The single Notice Place card comes from `noticeplace_ask_human` below.

From that binding, create the Notice Place side exactly once with
`noticeplace_ask_human` using `binding.request_id`,
`binding.native_target.harness`, `binding.native_target.session_id`, and the
required `cwd=binding.native_target.cwd`, plus the message. Herder choices
have `{choice_id, label}`; the Notice Place tool accepts `{value, label}`.
Map every choice explicitly: **`value=choice_id`**, preserving `label`:

```text
noticeplace_ask_human(
  request_id=binding.request_id,
  harness=binding.native_target.harness,
  session_id=binding.native_target.session_id,
  cwd=binding.native_target.cwd,
  message=binding.question.message,
  choices=[{value: choice.choice_id, label: choice.label} for choice in binding.question.choices]
)
```

Do **not** create a second question or a second card:
one creation means one card, one pending question, and one answer.

The answer arrives from Telegram, voice, or phone. Notice Place records it, marks
the same card, and continues **your** session through Agent Herder's own
completion seam. You do not poll for it and you do not start a second waiting
process.

When Agent Herder resumes you it says the question is answered and gives the
`request_id` plus an answer reference. Read the canonical answer with
`noticeplace_question_result(request_id=...)` and apply it. That value comes from
durable storage, never from the message text, so a replayed or conflicting
answer cannot change it. Ordinary question tools reject secret answers;
use the separate AskSecret/SSS flow. Historical secret results stay opaque.

`ask_human` stays available for a plain notification or an unbound blocking
question. It binds nothing and resumes nothing, so never pair it with a manual
resume.

## Reading and answering questions

- `noticeplace_pending_questions` returns the bounded question text, its
  options, the incident reference, and the exact native session. It never
  returns a stored answer, an attached document, or a secret payload.
- `noticeplace_answer` records one accepted answer for any resolver: a question
  (`request_id` plus `value`) or an incident choice card (`incident_id`,
  `request_id`, `choice_id`). The first accepted answer is canonical; a later
  conflicting repeat is reported as `conflict` and never changes the answer.
  `answer_status=no_accept` means the question is closed and no answer was
  accepted. Card and native delivery are separate outcomes. A failed card edit
  does not stop native continuation; the existing delivery worker retries the
  same card and a definitively unavailable native completion. Unknown admission
  is never retried automatically.

## Reporting progress

`noticeplace_incident_progress(incident_id, step, evidence_refs, ...)` publishes
a bounded receipt. `evidence_refs` must point at a source you really checked.

You observe; you never verify yourself. Pass `source_id` and
`source_fingerprint` for the original source you actually read, plus
`observed_state` when you are confident. Do not send `verifier_id` or
`verification_id`: the tool rejects them.

A healthy observation leaves the incident open with `awaiting_verification` and
the exact `source_id` and `source_fingerprint` a separate probe must confirm.
Only an independent producer verification can close it. An incident that stays
open is the intended outcome, not a failure.

## Handling rules

- Treat every tool result as an opaque reference plus bounded state. Never
  invent identifiers and never reuse a reference from another incident.
- Never print tokens, credentials, or the raw text of a secret request.
- Keep one executor session per incident. Do not start a parallel planner and
  do not silently restart a failed or needs-input repair.
