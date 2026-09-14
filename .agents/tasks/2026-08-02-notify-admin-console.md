# Notify Center: защищённая редактируемая админка

## Исходный запрос

Пользователь указал, что на сайте нельзя ничего настроить, и подтвердил:
«да делай» — после объяснения, что для этого нужен operator backend за
`auth.bezrabotny.com` с сессией, CSRF-защитой и настройкой проектов, уровней,
токенов и маршрутов.

## Цель

Реализовать защищённую operator-консоль Notify Center, в которой авторизованный
оператор может безопасно смотреть и менять producer-проекты, maximum severity
и маршрутизацию, а новый producer-токен виден ровно один раз.

## Business canary

Оператор с подтверждённой SSO-сессией может создать scoped producer identity,
получить токен один раз, отправить им допустимый event, а неавторизованный
browser request не получает ни конфиг, ни mutation.

## Подтверждённый scope

- Исследовать текущие nginx/SSO/service/config ownership boundaries.
- Предложить и после выбора реализовать browser UI, operator API, CSRF и
  минимальный privileged configuration writer.
- Сохранить публичную landing-страницу и producer API policy.
- На одноразовой странице нового producer добавить copy-paste integration card:
  env-файл, curl, Python/Node и systemd пример без повторного раскрытия token.

## Явные исключения до выбора варианта

- Basic Auth, перенос producer-token в браузер, открытие loopback backend,
  показ raw token после первичного creation, неявная смена Telegram/Matrix
  credentials.
- Ротация существующих credentials или широкая перестройка ingress.

## Оценка

- Initial active-minute estimate (оптимистичная / вероятная / пессимистичная):
  240 / 420 / 720 минут.
- Revision log: none.

## Начальный план

1. Подтвердить auth ingress, process identity и config write boundary.
2. Сформировать три варианта с разным набором mutations и human-gate.
3. После выбора показать call stack, file diff и signatures.
4. Реализовать, проверить browser/API canary, backup/rollback и release.

## Progress log

- 2026-08-02: Task created; topology and SSO contract research is in progress.
- 2026-08-02: Research confirmed that the current Notify nginx vhost has no
  SSO/auth_request or trusted identity header. The Center itself is a dedicated
  unprivileged loopback service and its primary env is root-owned mode 0600.
  Existing cookie-auth snippets elsewhere on the host cannot be assumed to
  protect Notify. A separate authenticated BFF plus privileged writer is
  required for real configuration mutations.
- 2026-08-02: Normal scope selected by user. Implemented and pushed protected
  HTML console plus loopback BFF (`f7a473c` and fixes through `d32b681`), nginx
  SSO gate, root-only CSRF secret, and separate one-shot apply helper. The BFF
  cannot write `/etc`; its validated job may target only producer scopes or
  Telegram routes, and the helper writes atomically with rollback on restart
  failure.
- 2026-08-02: Deployment canaries passed: forged public admin header still
  redirects to SSO, direct backend request without trusted header is 403, and a
  real temporary producer was created then revoked through the live writer.
  Main Center and admin service remain active and authenticated health is OK.
- 2026-08-02: User requested integration guidance directly in the console;
  implementation is in progress.
- 2026-08-02: Added and pushed `0fdee74`; the one-time token page now includes
  secret-file, curl, Python, Node.js and systemd EnvironmentFile snippets.
  The raw token remains only in the one-time value block; snippets reference an
  environment variable or paste placeholder. The restarted admin service is
  active and still rejects direct requests without the nginx trust header.
