# S21 Wi-Fi LAN-only with cellular default egress

## Исходный запрос

«вай фай зачем то специально ограничен чтобы телефон его не юзал как доступ в
интернет а он все равно юзает надо пофиксить»

## Цель

Оставить S21 доступ к LAN `192.168.2.0/24` по Wi-Fi, но исключить Wi-Fi как
маршрут в интернет: внешний egress должен идти только через validated LTE.

## Business canary

При включённом Wi-Fi телефон достигает LAN gateway/локальных адресов через
`wlan0`, а проверка публичного IP/DNS/HTTPS идёт через cellular `rmnet4` и
не получает ответ от Wi-Fi gateway.

## Подтверждённый scope

- Диагностировать текущие Android network capabilities, маршруты и DNS.
- Применить минимальную обратимую policy/configuration на S21.
- Проверить LAN и интернетные направления отдельными probes.

## Явные исключения

- Публикация телефона или Android MCP в интернет.
- Изменение маршрутизации других устройств в сети.
- VPN/TUN-подмена без подтверждённой необходимости.

## Оценка

- Initial active-minute estimate: 15 / 35 / 90 минут.
- Revision log: none.

## Начальный план

1. Подтвердить default network, Wi-Fi LAN и LTE capabilities.
2. Выбрать минимальную обратимую Android policy, сохраняющую LAN.
3. Выполнить раздельные LAN/LTE canaries и зафиксировать rollback.

## Progress log

- 2026-08-02: Wi-Fi gateway `192.168.2.1` подтверждён как non-egress
  (`Destination Port Unreachable` для `1.1.1.1`); LTE `rmnet4` validated и
  достигает `1.1.1.1`. Временное отключение Wi-Fi восстановило Termux egress.
- 2026-08-02: Mandatory Overseer audit requested before changing the durable
  Wi-Fi/LTE policy.
- 2026-08-02: Overseer approved the narrow policy. Samsung's enabled "Switch
  to mobile data" policy corresponds to `network_avoid_bad_wifi=0` and
  `mobile_data_always_on=1`; a temporary value of `1` was reverted after live
  system-UI evidence showed it yielded to the bad Wi-Fi network.
- 2026-08-02: Durable root cause was a per-network exception for `Demiurge
  Space Slow`: it explicitly kept the phone connected without Internet
  (`ACCEPT_UNVALIDATED`). Removed only that exception through Samsung
  Intelligent Wi-Fi. Post-change Wi-Fi is enabled but disengaged from that
  no-Internet network; LTE `rmnet4` is the active default network and the
  public probe succeeds. LAN-only retention remains unproven because Android
  disconnected from the bad Wi-Fi network, so the simultaneous LAN-plus-LTE
  acceptance gate remains pending.
