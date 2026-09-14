# Notify Center: малые Python и Node.js producer SDK

## Исходный запрос

«Напиши малюсенькую либу на pip и npm, чтобы она одной командой
устанавливалась и удалялась, если надо. Но и делала простое, позволяла
логировать в Notification Center, да, и дождаться ответа от него. Опционально
дождаться ответа, если надо.»

## Цель

Предоставить крошечные dependency-free Python и Node.js клиенты, которые
создают одно scoped событие в Notify Center и по желанию ожидают только
операторское `acknowledged` или `resolved` состояние.

## Business canary

Оба SDK через реальный loopback HTTP Center создают один инцидент и возвращают
из ожидания только после подтверждения оператора.

## Подтверждённый scope

- Python-пакет, устанавливаемый через pip из репозитория; npm subpath-export в
  уже поставляемом `notify-mcp` пакете.
- Документация install/uninstall, переменных окружения, уровней и retry rules.
- Ограниченный polling с timeout; server API и действующая severity-политика
  не меняются.
- Сделать Python Git install корневой одношаговой командой без VCS
  `subdirectory`-суффикса, сохранив npm package metadata совместимыми.
- Сделать краткий producer quick start прямо в корневом README, не дублируя
  развёрнутый contract guide и не раскрывая deployment-specific endpoint.

## Явные исключения

- Публикация в PyPI/npm registry без отдельного явного разрешения.
- Утверждение, что 202/delivery/call означает человеческий ответ.
- Логирование или отправка producer-токенов в браузер либо package defaults.
- Корневая Python packaging metadata: пользователь явно предпочёл сохранить
  проверенный `#subdirectory=python` путь ради минимального README.

## Оценка

- Initial active-minute estimate (оптимистичная / вероятная / пессимистичная):
  45 / 90 / 150 минут.
- Revision log: none.
- 2026-08-02: user requested a shorter Git install surface; likely effort
  remains within the original estimate because this is packaging-only.

## Начальный план

1. Подтвердить существующий event/incident контракт и смысл ответа.
2. Добавить сначала контрактные тесты обоих SDK.
3. Реализовать dependency-free клиенты и registry-ready метаданные.
4. Проверить business canary, полный набор тестов и артефакты упаковки.

## Progress log

- 2026-08-02: Existing API and scope boundary researched; Overseer approved
  thin SDKs. `202` is acceptance, while only acknowledged/resolved is a human
  response.
- 2026-08-02: Contract tests were added first and failed because the Python
  package did not exist; the implementation now passes two loopback client
  canaries and the complete 62-test suite. Python wheel and npm dry-run package
  contents were validated.
- 2026-08-02: Committed and pushed `d7743e7` to `origin/main`; no registry
  publish action was taken.
- 2026-08-02: Fresh temporary consumers installed the pushed Python Git
  subdirectory package and the npm Git package, then imported
  `NotificationCenterClient` successfully; both temporary directories were
  removed.
- 2026-08-02: Root-level Python packaging simplification is in progress.
- 2026-08-02: User accepted the existing Python Git install form and requested
  that the root README, rather than packaging metadata, be kept simple.
- 2026-08-02: Added and pushed root README `Producer SDK (optional)` quick
  start in `012f075`; it contains only two install commands, truthful response
  semantics, and a link to the detailed guide.
