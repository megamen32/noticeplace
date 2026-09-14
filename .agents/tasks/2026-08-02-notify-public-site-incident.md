# Notify Center: недоступность публичного сайта

## Исходный запрос

«а сайт notify.bezrabotnyi.com не работает»

## Цель

Восстановить доступность ожидаемой публичной HTTPS-поверхности Notify Center
минимальным обратимым изменением и подтвердить это внешним запросом.

## Business canary

Точный публичный маршрут возвращает ожидаемый HTTP-ответ по HTTPS без токена в
URL или ответе; при необходимости защищённые API остаются защищёнными.

## Подтверждённый scope

- Read-only topology-first проверка внешнего DNS/TLS/HTTP и затронутого host.
- После подтверждения причины — минимальный обратимый repair и повторная
  публичная проверка.
- Заменить подтверждённый `404` на `/` безопасной статической landing-страницей
  без токенов, статуса защищённого health endpoint или browser-side mutations.

## Явные исключения

- Ребут server-100, смена сертификата, изменение producer scopes или открытие
  защищённых API без подтверждённой необходимости.
- Показ токенов, credentials, полных environment-файлов или сырых журналов.

## Оценка

- Initial active-minute estimate (оптимистичная / вероятная / пессимистичная):
  15 / 35 / 90 минут.
- Revision log: none.

## Начальный план

1. Проверить внешний DNS/TLS/HTTP и короткий server-health probe.
2. Изолировать точный владелец ingress/runtime маршрута.
3. Применить только подтверждённый обратимый ремонт.
4. Повторить публичный business canary и зафиксировать результат.

## Progress log

- 2026-08-02: Incident created; read-only evidence collection started.
- 2026-08-02: DNS, valid TLS, nginx proxying and dedicated authenticated
  health contract are confirmed healthy. The user-visible fault is a missing
  root route: public `/` returns 404 while protected `/health` correctly
  returns 401 without credentials. A minimal public landing page is approved
  by the original UI request and is in progress.
- 2026-08-02: Added tested token-free root landing, committed/pushed `c0caa36`,
  and deployed only `http_api.py` plus `landing.py` to server-100. Public HTTPS
  root is now 200 text/html with the expected content; local handler and the
  authenticated `notify.health.v1` canary remain healthy. Rollback source is
  retained at `/opt/notify/.rollbacks/landing-c0caa36` on that host.
