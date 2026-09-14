# External site monitor routing to Notify

Status: work

## Original request

Выяснить, кто отправляет уведомления External site monitor и NOTIFICATION CENTER DOWN/RECOVERED, проверить связь с VPN2/nginx/lol.bezrobotny.com, перевести основной путь на общий Notify с прямым fallback, а падение сайта сделать критическим с телефонной эскалацией.

## Objective

Identify the real monitor producer and implement a fail-safe route: Notify first when healthy, direct delivery when Notify is unavailable, with a critical phone escalation for site-down incidents.

## Business canary

A controlled site-down event produces one Notify intake attempt and, when Notify is unavailable, one direct fallback alert; a critical site-down event reaches the configured phone escalation path. Recovery produces a normal recovery notification without a phone call.

## Confirmed scope

- Read-only topology and sender ownership diagnosis across the actual monitor host, VPN2/nginx/lol path, and Notify.
- Preserve direct fallback when Notify is unavailable.
- Route site-down as critical and connect it to the existing at-least-once phone escalation.
- Add focused tests/config evidence before any production apply.

## Explicit exclusions

- No deletion or broad cleanup of monitor rules.
- No changes to unrelated VPN routing, nginx sites, or external monitor thresholds.
- No production restart or live canary until the exact current sender and target are confirmed.

## Initial estimate (immutable)

- Optimistic: 60 active minutes
- Likely: 150 active minutes
- Pessimistic: 300 active minutes

## Initial plan (Russian)

1. Найти реальный unit/cron/container и исходный код отправителя по текстам сообщений и доменам.
2. Проверить текущие Notify/direct/fallback каналы и критическую телефонную конфигурацию.
3. Подготовить узкий fail-safe cutover, тесты и бизнес-canary с отдельным разрешением на production apply.

## Execution log (English)

- 2026-08-07: Read-only ownership confirmed `External site monitor` is `/home/roomhacker/lol-nginx-admin/monitor/ingest.py` on server-100; VPN2 runs `probe.sh` hourly and uploads through a restricted SSH command. The old ingest path sent direct Telegram to chat `540308572`.
- Implemented Notify-first transition routing in `lol-nginx-admin`: site DOWN is a `critical` Notify incident, recovery is a resolve event, and direct Telegram remains fallback when Notify fails. Existing VPN2 upload-failure direct alert remains independent.
- Notify credentials are resolved from the existing protected `/etc/notification-center.env` scope `fleet-health`; no token was copied to source control. A resolve-only routing canary returned success without creating an incident or phone call.
- Focused monitor tests: 5 passed. Live Notify health was `ok` before the canary. Commit/push: `lol-nginx-admin` `2a02a2a feat: route site monitor through Notify with fallback`.
- The exact sender of the historical text `NOTIFICATION CENTER DOWN/RECOVERED` was not found in local systemd/cron/source inventory. Current `fleet-health` already uses Notify and emits different message text; no blind change was made to that path.
