# Rename Notify Center to NoticePlace

Status: work

## Original request

Переименовать центр уведомлений в NoticePlace: GitHub repo, локальную папку,
документацию, deployment и сервисы; CLI Notify оставить отдельным проектом.

## Objective

Publish the notification center under the NoticePlace product name without
breaking existing API clients, producer tokens, service units, or the Notify CLI.

## Business canary

`noticeplace` GitHub repo and local workspace folder are canonical; the
protected admin and API service restart successfully from the new deployment
path; existing health and producer API contracts remain reachable.

## Confirmed scope

- Rename GitHub repository and local Center folder.
- Update product-facing branding, repository URLs, package metadata, and docs.
- Move the deployed Center checkout to the NoticePlace path and restart the API/admin services.
- Keep `notification-center.service`, `NOTIFY_CENTER_*`, `notify.bezrabotnyi.com`, and CLI `Notify` as compatibility identifiers unless independently migrated.

## Explicit exclusions

- No DNS/certificate migration for the existing API hostname.
- No breaking rename of environment variables, API paths, SQLite files, or systemd unit names.

## Initial estimate (immutable)

- Optimistic: 45 active minutes
- Likely: 120 active minutes
- Pessimistic: 240 active minutes

## Initial plan (Russian)

1. Переименовать GitHub/local repo и брендовые ссылки.
2. Проверить deployment copy и перевести его на NoticePlace path.
3. Перезапустить API/admin, проверить health, redirect и producer canary.

## Implementation progress (English)

- Renamed the public GitHub repository from `megamen32/notify` to `megamen32/noticeplace` and updated the local remote.
- Renamed the workspace folder from `/home/roomhacker/agents-projects/notify` to `/home/roomhacker/agents-projects/noticeplace`.
- Rebranded product-facing documentation, UI text, package repository URLs, deployment templates, and SDK links; retained `notification-center.service`, `NOTIFY_CENTER_*`, `notify.bezrabotnyi.com`, and CLI `Notify` for compatibility.
- Moved the production checkout from `/opt/notify` to `/opt/noticeplace`, updated API/admin systemd ExecStart and WorkingDirectory, corrected checkout permissions, reloaded systemd, and restarted both services.
- Verification: focused tests 34 passed; `GET https://notify.bezrabotnyi.com/` returned `303` with `Location: /admin/`; authenticated health returned HTTP 200; both systemd services are active.

## Estimate revision

- Revised likely active time: 180 minutes. Trigger: production path migration exposed and required correction of root-only checkout permissions before restart could pass; evidence: journal showed Permission denied, then active services and HTTP canaries passed.
