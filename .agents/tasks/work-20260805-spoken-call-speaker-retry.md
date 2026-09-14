# Repair and retry spoken S21 call

## Role

Worker

## Goal

Repair the failure before speakerphone/TTS in the direct Notify phone-call
path, then make exactly one user-authorized retry of:
`Hermes ждёт пароль` twice, followed by hangup.

## Known evidence

The live remote MCP call dialled, then failed at Android
`shell uiautomator dump` while locating the speaker control. The adapter's
`finally` sent hangup, so no spoken audio ran and no call stayed active.

## Allowed paths

`notification_center/android_phone.py`, its focused tests, `mcp/notify_mcp.py`
only if required, and this task file. Read-only service/ADB diagnostics allowed.

## Required workflow

1. Diagnose the exact failing ADB command/error under the service identity.
2. Add and run a focused failing regression, then the smallest fix.
3. Run focused tests.
4. Install only the changed runtime file(s), restart Notify, confirm it is
   healthy, and make exactly one retry via live remote MCP.
5. Do not retry if the same pre-speech failure repeats; append exact evidence.

## Exclusions

No unrelated refactor, TTS provider/STT/WhatsApp work, config/token changes,
or a second retry.

## Acceptance

Detailed evidence in this file: concrete pre-fix error, Red/Green tests,
runtime files installed, and one final receipt distinguishing spoken completion
from a merely started call. Return L only TL;DR.

## Work log

- Pre-fix / live diagnostic evidence:
  - `adb shell 'uiautomator dump /sdcard/notify-center-window.xml; printf "RC:%s\n" "$?"'`
  - observed output: `Killed` and `RC:137`
  - `adb shell 'uiautomator dump /sdcard/notify-center-window.xml >/dev/null 2>&1; printf "RC:%s\n" "$?"'`
  - observed output: `RC:137`
  - `adb shell 'uiautomator dump --compressed /sdcard/notify-center-window.xml >/dev/null 2>&1; printf "RC:%s\n" "$?"'`
  - observed output: `RC:137`

- Red/green tests:
  - Added `test_spoken_phone_call_keeps_talking_when_uiautomator_dump_is_killed`
  - `python -m pytest tests/test_android_phone.py -q` → `13 passed`
  - `python -m pytest tests/test_android_phone.py tests/test_notify_mcp.py -q` → `16 passed`

- Code change:
  - `notification_center/android_phone.py`
  - speaker lookup during `phone_call()` is now best-effort; a killed `uiautomator dump` no longer aborts the spoken call path before TTS/hangup

- Runtime install / restart:
  - attempted `install -m 0644 notification_center/android_phone.py /opt/notify/notification_center/android_phone.py`
  - failed: `install: невозможно удалить '/opt/notify/notification_center/android_phone.py': Отказано в доступе`
  - attempted `systemctl restart notification-center.service`
  - failed: `Interactive authentication required`
  - `systemctl is-active notification-center.service` currently reports `active`

- Not completed:
  - no live retry was executed after the fix per the latest user instruction
  - no final spoken-completion receipt was produced
