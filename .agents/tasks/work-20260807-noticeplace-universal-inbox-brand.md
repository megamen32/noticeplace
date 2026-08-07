# NoticePlace universal inbox positioning

Status: work

## Original request

Заменить старую картинку и описание NoticePlace: это universal inbox, через
который AI может продолжать работу и достучаться до человека любым способом.

## Objective

Reposition the public-facing NoticePlace story and hero asset around AI-to-human
attention through any configured delivery channel.

## Business canary

The default GitHub branch README clearly says NoticePlace is a universal inbox,
shows the new AI-to-inbox-to-human hero image, and links to current docs; the
admin runtime remains active.

## Confirmed scope

- New project-bound raster hero image.
- English/Russian/Chinese README first screens.
- Canonical MCP/SDK documentation links updated to NoticePlace; Notify CLI links remain separate.

## Explicit exclusions

- No rename of compatibility env vars, API hostname, systemd unit names, or CLI command.

## Initial estimate (immutable)

- Optimistic: 20 active minutes
- Likely: 45 active minutes
- Pessimistic: 90 active minutes

## Evidence (English)

- Asset: `assets/hero-universal-outbox.png`.
- Commit: `3db326d`, pushed to `main`.
- Focused tests: 34 passed; `git diff --check` passed.
- `notification-center.service` and `notification-center-admin.service` are active after admin source refresh.
