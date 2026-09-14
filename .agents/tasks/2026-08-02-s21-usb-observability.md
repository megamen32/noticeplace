# Задача: расследовать пропадание USB ADB у S21

## Исходный запрос

«S21 на самом деле по USB подключен. Можешь расследовать и сделать, чтобы всё нормально работало? … можно сделать, чтобы он писал?»

## Цель

Установить подтверждённую причину отсутствия USB ADB-транспорта S21 на server-100, восстановить его при наличии безопасного локального исправления и добавить ограниченное журналирование изменений USB/ADB-транспорта.

## Бизнес-канарейка

При подключённом S21 `adb devices` показывает USB-транспорт либо диагностический журнал однозначно объясняет, почему он отсутствует; события подключения/отключения сохраняются с временем и причиной.

## Подтверждённый scope

- server-100, его USB/udev/kernel/ADB путь и подключённый S21.
- Notify Center Android adapter only insofar as it consumes ADB transport state.

## Явные исключения

- Перезагрузка server-100, offline fsck, смена SIM/номера, Telegram-авторизация и VPN2 watchdog.

## Оценка (неизменяемая)

- optimistic: 12 active minutes
- likely: 25 active minutes
- pessimistic: 50 active minutes

## Первоначальный план

1. Снять read-only снимок USB, udev, kernel и ADB состояния с временной шкалой.
2. Сопоставить физическое присутствие телефона с авторизацией/режимом Android USB.
3. Выполнить только обратимое исправление, подтверждённое снимком.
4. Добавить минимальный systemd/udev журнал смены USB и ADB-транспорта, проверить реальным событием.

## Execution log

- Confirmed current state: Wi-Fi ADB is healthy but no Samsung `04e8:*` device is enumerated by the host and no USB transport exists in `adb devices -l`.
- Confirmed root cause from the kernel timeline: port `usb1-port2` was disabled with `disabled by hub (EMI?)`, then host power-cycle attempts failed to enumerate the phone.
- With explicit user approval, rebound only Intel xHCI `0000:00:14.0`; NanoKVM re-enumerated but Samsung did not, proving the remaining fault is outside ADB/udev and not a stuck xHCI driver.
- Deployed state-transition logging for the existing USB watchdog. It now writes one `USB_ABSENT` event to the normal journal and returns success for an expected physical absence; the next timer tick produced no duplicate transition.
- The timeline rules out the watchdog as the cause: the kernel disabled `usb1-port2` at 10:49:11, while `android-adb-reconnect.service` only began at 10:49:29 after the disconnect.
- Android-side control was tested over Wi-Fi ADB: `svc usb setFunctions mtp,adb` completed and Android retained `mtp,conn_gadget,adb`, but the host produced no USB event and still lacks Samsung `04e8:*`. The fault is not the selected Android USB function.
- A requested USB-modem round trip was attempted with `svc usb setFunctions rndis,adb` followed by `mtp,adb`. Android retained the original MTP+ADB state and server-100 received zero USB/RNDIS events; MTP+ADB was confirmed after the attempt.
- Recovery confirmed: Samsung `04e8:6860` re-enumerated, restarting the host ADB daemon produced `R5CR702SRFP device usb:1-1`, watchdog transitioned to `USB_ADB_READY`, and the `notification-center` service user verified the physical USB transport.
- Remaining operator action: replug S21 at the physical host / replace or reseat the data cable. USB recovery remains no-go until host `lsusb` shows Samsung `04e8:*`.
