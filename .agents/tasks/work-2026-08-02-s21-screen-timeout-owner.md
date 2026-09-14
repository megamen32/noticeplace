# S21 five-minute screen-timeout ownership

Status: complete

## Original request

"увелчиь чтобы 5 минут экран не блокировался и найди кто это ломает ? на сотом или на 44?"

## Objective

Keep the S21 interactive for five minutes and identify the automation or host
that changes the Android screen timeout back to 15 seconds.

## Business canary

`screen_off_timeout` remains `300000` after the suspected host automation
windows, and evidence attributes any prior write to server-100, server-44, or
neither.

## Confirmed scope

- Restore only the S21 screen timeout to five minutes.
- Read-only audit of server-100 and server-44 ADB/system automation.

## Explicit exclusions

- No disabling unrelated phone, monitoring, or remote-control services.
- No reboot or host-wide cleanup.

## Initial estimate

- Optimistic: 8 active minutes.
- Likely: 18 active minutes.
- Pessimistic: 45 active minutes.

## Initial plan

1. Установить timeout 300000 и зафиксировать baseline.
2. Найти все записи `screen_off_timeout`/ADB settings на server-100 и server-44.
3. Сопоставить логи и timer/unit execution с моментом сброса; исправить только подтверждённого владельца.

## Progress

- 2026-08-02: Restored the S21 system `screen_off_timeout` from `15000` to `300000`. Read-only attribution audit started on server-100 and server-44.
- 2026-08-02: Attribution confirmed. Server-44 has no attached S21 and its ws-scrcpy service contains no timeout write. On server-100, `android-s21-gptadmin-bridge.timer` runs every 300 seconds and its maintainer script wrote `screen_off_timeout 15000` through USB ADB. The live maintainer was corrected to `300000`; immediate post-run readback is `300000`. Full next-timer-cycle verification is pending.
- 2026-08-02: The following scheduled execution ran at 08:32:03 MSK and completed successfully. A 08:32:24 MSK live S21 readback remained `300000`, so the five-minute setting survived the full offending timer cycle.
