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
