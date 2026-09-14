# Notify public docs and GPTAdmin ShellMCP call adapter

## Исходный запрос

«ссылку на гитхаб добавь а на гитхабе подробную инструкцию в docs сам ридми
должен быть как и все публичные ридми быть коротким. а ты можешь сделать еще
адаптер один на звонок через gptadmin -> android shellmcp -> call чтобы adb бы
не обязателен вообще?»

## Цель

Сделать root README короткой публичной посадочной страницей, перенести подробные
инструкции в docs и исследовать/после выбора реализовать независимый от ADB
phone-call adapter через GPTAdmin ShellMCP на Android.

## Business canary

Публичный README ведёт к одному реальному install и подробным docs; при
выбранной реализации Notify Center отправляет тестовый Android call request
через GPTAdmin/ShellMCP без ADB transport.

## Подтверждённый scope

- Сократить публичный root README, добавить GitHub/docs links и расширить
  подробный producer guide.
- Исследовать текущий GPTAdmin hub, ShellMCP и Android call contract,
  безопасность credentials и failure behavior.
- Install and configure Android Remote Control MCP as a separate restricted
  S21 control surface: LAN/bearer only, no public tunnel, least required
  permissions/tools.

## Явные исключения до выбора adapter-варианта

- Удаление существующего ADB adapter или его проверенной fallback-политики.
- Передача GPTAdmin / ShellMCP secrets в public README, browser или producer.
- Публикация пакета в registry без отдельного запроса.

## Оценка

- Initial active-minute estimate (docs / adapter, optimistic-likely-pessimistic):
  30 / 60 / 100 минут; 180 / 360 / 600 минут.
- Revision log: none.

## Начальный план

1. Проверить текущий public docs и ShellMCP/Android runtime contract.
2. Выполнить краткий README/docs change и получить human selection для adapter.
3. До adapter implementation показать call stack, file diff и signatures.
4. Реализовать, выполнить real no-ADB canary, review и release.

## Progress log

- 2026-08-02: Task created; public-readme and GPTAdmin/ShellMCP research started.
- 2026-08-02: Short public README, GitHub link, detailed producer systemd guide,
  and one-time console GitHub/docs links were committed and pushed as `c497067`.
  Focused admin-console tests passed and the updated loopback service is active.
- 2026-08-02: GPTAdmin research confirms only generic ShellMCP tools today;
  there is no dedicated Android phone-call contract, so a no-ADB adapter needs
  an explicit design selection before implementation.
- 2026-08-02: User selected the YAGNI fixed-command phone fallback and
  requested Android Remote Control MCP installation; live installation research
  is in progress.
- 2026-08-02: Confirmed the S21 (SM-G998B, Android 15) is presently reachable
  over USB ADB and Android Remote Control MCP is not installed. Installation
  will use the official signed APK only as a bootstrap; the resulting call
  adapter will expose one named `android.phone.call` operation and will never
  accept a caller-provided shell command or phone number.
- 2026-08-02: Installed and launched Android Remote Control MCP 1.10.0 on the
  S21. Installed F-Droid Termux:API 0.53.0 only after comparing its signing
  certificate with the installed Termux 0.118.3; granted only `CALL_PHONE`.
- 2026-08-02: Added a tested `GptAdminPhoneAdapter`: it posts one immutable
  command to a dedicated ShellMCP action endpoint, rejects mixed ADB/GPTAdmin
  configuration, and never serializes incident data or a phone number. Full
  local suite: 69 tests passed. The remaining live gate is device-side
  provisioning of the private Termux script and an outbound `s21-phone`
  ShellMCP target; no real call has been triggered.
- 2026-08-02: The adapter and operator guide were committed and pushed as
  `d83cfa8`. The live service remains on the existing direct-ADB configuration
  until the separate no-ADB target can be enrolled, so this change does not
  silently alter the current escalation path.
