# Standard consumer profiles and ingress audit

Status: work

## Original request

Сделать постоянные consumer-профили: стандартные `emergency`, `important`,
`log` должны быть такими же профилями, как custom; добавить возможность понять,
какой проект/компьютер вызвал уведомление, включая source/proxy IP, и optional
operator notes.

## Objective

Unify built-in and custom delivery profiles without breaking legacy producer
tokens, add quiet-hours ownership to every profile, and retain secret-safe
ingress provenance plus optional operator notes.

## Business canary

Legacy events resolve to one of the three built-in profiles, custom events keep
their custom profile, a quiet-hours rule is evaluated against that profile, and
the incident audit identifies project/profile/source/proxy metadata without
storing bearer tokens.

## Confirmed scope

- Built-in profile records for `emergency`, `important`, and `log`.
- Legacy severity routing compatibility during migration.
- Per-profile quiet hours.
- Trusted-proxy source audit and optional operator note.

## Explicit exclusions

- No arbitrary producer-controlled delivery target.
- No raw Authorization headers, tokens, or full request bodies in audit.
- Keep the global automatic-call switch only as an emergency operator kill
  switch, not as the profile quiet-hours mechanism.

## Initial estimate (immutable)

- Optimistic: 60 active minutes
- Likely: 120 active minutes
- Pessimistic: 240 active minutes
