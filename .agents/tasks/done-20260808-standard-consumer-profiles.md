# Standard consumer profiles and ingress audit

Status: done

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

## Completion evidence

- Commit `b6db08a` pushed to `main`.
- Built-in profiles `profile_emergency`, `profile_important`, and `profile_log`
  are materialized in the production SQLite database and legacy events resolve
  to them by mode.
- Custom profiles retain their own consumer identity and quiet-hours policy.
- `event_ingress` audit records project, profile, source IP, trusted proxy IP,
  and forwarded-for chain; bearer tokens and Authorization headers are excluded.
- Optional `operator_note` is accepted by `notify-producer`, stored on the
  incident, and included in Telegram cards.
- Production services are active and deployed source digests match the
  workspace. Existing temporary global call suppression remains scheduled to
  re-enable at 09:00 MSK.
- Full test run: 118/119 passed; the single failure is the pre-existing
  `test_telegram_controls` notice-to-log route drift.
