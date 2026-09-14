# Задача: проверочный обычный звонок через Notify Center

## Исходный запрос

«Ладно, потом с Леграмом разберемся. Позвони мне просто по телефону.»

## Цель

Выполнить один реальный обычный звонок через канал `android.phone.call` Notify Center на уже подтверждённый номер пользователя.

## Бизнес-канарейка

В audit доставки получить `android.phone.call:sent` после положительной проверки `mVoiceRegState=0(IN_SERVICE)`, затем получить подтверждение пользователя о входящем звонке.

## Подтверждённый scope

- Существующий S21 `R5CR702SRFP`.
- Существующая настройка `ANDROID_PHONE_TARGET`.
- Существующая очередь Notify Center и канал `android.phone.call`.

## Явные исключения

- Telegram, браузерный вход и VPN S21.
- Изменение номера, правил эскалации или VPN2 watchdog.

## Оценка (неизменяемая)

- optimistic: 3 active minutes
- likely: 6 active minutes
- pessimistic: 12 active minutes

## Первоначальный план

1. Проверить доступность S21, голосовую регистрацию и активную версию Notify Center.
2. Поставить один тестовый delivery `android.phone.call` в существующую очередь.
3. Проверить audit и немедленно подтвердить тестовый incident, чтобы не допустить Matrix-эскалации.
4. Запросить подтверждение входящего звонка.

## Execution log

- Preflight found Notify Center active, phone target present, and deployed adapter current.
- STOP: `adb -s R5CR702SRFP get-state` fails because the S21 is absent from USB and ADB; Samsung USB device is absent too.
- Mandatory Overseer approved the canary contract but independently confirms this exact state is a no-go.
- 2026-08-02 recheck: `notification-center.service` is still active, but `adb devices` remains empty and the configured S21 is not found; the voice-registration gate cannot be evaluated. No delivery was scheduled.
- 2026-08-02 final repeated preflight: the configured S21 is still absent from ADB, so it remains impossible to make a valid phone-call canary. The task is externally blocked pending USB/ADB reconnection; no notification was delivered.
- 2026-08-02 recovery: S21 was attached through its approved Wi-Fi ADB transport and reports `mVoiceRegState=0(IN_SERVICE)` on MegaFon. The adapter now resolves the configured hardware serial across ADB transports and the service uses the existing loopback ADB server.
- `android.phone.call` was scheduled through the Notify Center queue and completed as `sent`; the incident was resolved and its queued Matrix call was cancelled. User-side ringing confirmation is still requested.
