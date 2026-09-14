# Fast-path audit: S21 acoustic spoken call

## Role

Explorer

## Goal

Find the exact shortest supported way on the connected S21 to (a) force or
verify speakerphone during a cellular call and (b) verify Termux API TTS
broadcast execution. The user permits calls, but do not place one; report the
single decisive live diagnostic or smallest repair.

## Allowed actions

Read-only ADB/agent-device diagnostics, installed-package inspection, and
source inspection. No calls, TTS playback, settings mutation, app install, or
service restart.

## Acceptance

Append exact evidence and a smallest next action. Return only TL;DR.

## Explorer evidence (2026-08-05)

- Live transport is available: `agent-device devices --json` reported `R5CR702SRFP` / `SM G998B` booted; `adb devices -l` reports the same USB device.
- Cellular voice prerequisite is currently satisfied: `adb -s R5CR702SRFP shell dumpsys telephony.registry` contains `mVoiceRegState=0(IN_SERVICE)`, `mCallState=0`, operator `MegaFon`, LTE, and `IsVoiceCallAvailable=true`.
- Decisive TTS blocker: `adb -s R5CR702SRFP shell dumpsys package com.termux.api` reports `versionName=0.53.0`, but User 0 is `stopped=true notLaunched=true enabled=0`. The package is installed and declares the relevant API permissions, but is disabled/not launched, so the `com.termux.api.tts.SPEAK` broadcast cannot be accepted by a running Termux:API component. `cmd appops get com.termux.api` showed only storage/media allowances; no positive TTS execution evidence exists.
- Source-of-truth call path: `notify/notification_center/android_phone.py:263-273` waits, discovers speaker controls by labels `Speaker`/`Динамик`/`Громкая связь`, taps the discovered bounds, then sends `com.termux.api.tts.SPEAK` with `com.termux.api.extra.TEXT`; `:277-278` hangs up by default. The fixed ShellMCP path is documented at `notify/docs/gptadmin-phone-adapter.md:6-15`, but is a separate transport.
- No call, TTS broadcast, settings mutation, install, or restart was performed. Current speakerphone state cannot be verified without an active call; the supported implementation verifies/taps it only after call connection.

## Smallest next action

An operator must enable and launch Termux:API on the S21 (same source/signing family as Termux), then perform one user-approved live canary: place a test call, verify the in-call UI exposes `Speaker`/`Динамик`/`Громкая связь`, and send the existing TTS broadcast while observing the result. Before that canary, the single decisive read-only check is `dumpsys package com.termux.api` showing `enabled=1` and no stopped/notLaunched state.
