# Split legacy Notify CLI repository

Status: work

## Original request

Старый простой CLI, который ждёт завершения процесса и уведомляет, вынести в отдельное репо; текущий репо оставить полноценным центром нотификаций и дашбордов.

## Objective

Publish the legacy process watcher as a separate private repository while keeping `notify` as the Notification Center.

## Business canary

Private GitHub repository `megamen32/notify-cli` exists, contains the runnable watcher and producer, and has a working main branch; `megamen32/notify` remains the Center repository.

## Confirmed scope

- Extract `bin/notify`, `bin/notify-producer`, CLI docs, README, and license.
- Keep the Center source repository unchanged except for documentation clarifying the split.

## Explicit exclusions

- No deletion of legacy files from the Center until documentation migration and production install references are verified.

## Initial estimate (immutable)

- Optimistic: 30 active minutes
- Likely: 60 active minutes
- Pessimistic: 120 active minutes

## Initial plan (Russian)

1. Зафиксировать границу старого CLI и нового Center.
2. Создать приватный `notify-cli` и проверить install/run docs.
3. Обновить документацию Center и проверить оба репозитория.

## Evidence (English)

- Created and pushed private repository: https://github.com/megamen32/notify-cli
- Repository is now public: https://github.com/megamen32/notify-cli
- Commit: `1443c21`, branch `main`.
- Extracted files: `bin/notify`, `bin/notify-producer`, `docs/cli.md`, `docs/cli.ru.md`, `docs/cli.zh.md`, `README.md`, `LICENSE`.
- Workspace folders are separate siblings: `notify` (Center) and `notify-cli` (Notify.cli).
