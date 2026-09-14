# Use agent-device for S21 control

Status: complete

## Original request

"используй лол [callstack/agent-device](https://github.com/callstack/agent-device) и в глобальные Agents.md пропиши"

## Objective

Make `callstack/agent-device` the standard inspect-act-verify interface for the USB-attached S21, and record the exact safe invocation rule in Codex's global `AGENTS.md`.

## Business canary

From the machine that owns the USB ADB transport, `agent-device doctor` discovers the S21 and a token-efficient Android snapshot is captured through an `agent-device` session.

## Confirmed scope

- Use the upstream `agent-device` CLI rather than ad-hoc `adb input` for subsequent S21 UI automation.
- Install or update only the runner that owns the S21 USB connection.
- Add a concise global Codex instruction naming the runner, inspect-act-verify flow, and evidence requirement.

## Explicit exclusions

- No public remote-control endpoint or network exposure.
- No changes to S21 data, accounts, Wi-Fi policy, or notification/call delivery as part of this request.
- No removal of existing Android Remote Control MCP or GPTAdmin components.

## Initial estimate

- Optimistic: 12 active minutes.
- Likely: 25 active minutes.
- Pessimistic: 55 active minutes.

## Initial plan

1. Проверить, где выполняется ADB-транспорт S21 и совместимость `agent-device`.
2. Установить/обновить CLI только на этом runner и прогнать `doctor`.
3. Открыть Android-сессию, снять accessibility snapshot и закрыть её.
4. Добавить короткое правило в глобальный Codex `AGENTS.md`; проверить, что оно не конфликтует с проектными инструкциями.

## Progress

- 2026-08-02: Started upstream and local-runtime discovery. Local Node is compatible; the global Codex instruction file needs confirmation before creation.
- 2026-08-02: Verified `agent-device 0.20.0` sees the USB S21 `R5CR702SRFP` and captured a read-only Android accessibility snapshot through its existing `notify` session. Backed up the global AGENTS file and added the S21 inspect-act-verify rule. The prior S21 Wi-Fi work remains separate and was not changed after this task began.

## Acceptance evidence

- `agent-device devices --json` reports `SM G998B` / `R5CR702SRFP` as a booted Android device.
- `agent-device appstate --json` and `agent-device snapshot -i --json`, run from the existing `notify` session, succeeded using the Android helper.
- Global instruction backup: `/home/roomhacker/.codex/AGENTS.md.bak-20260802-070633`.
