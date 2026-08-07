# Notify Center

[English](README.md) · **Русский** · [中文](README.zh.md) · [Документация](docs/)

![Notify Center маршрутизирует события в пользовательские каналы](assets/hero.svg)

> Единый центр уведомлений для AI-агентов и production-сервисов.

Notify Center принимает durable events, хранит инциденты и доставляет их в
Telegram, Matrix и телефонные адаптеры с ACK, resolve, retry и escalation.

## Установка

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

Для маленького SSH/process watcher используйте отдельный публичный проект
[Notify.cli](https://github.com/megamen32/notify-cli).

## Быстрый старт

1. Создайте project-scoped token в защищённой админке.
2. Отправьте событие на `POST /v1/events`.
3. Настройте topics, adapters и live escalation в `/admin/`.

Подробнее: [API](docs/notification-center-mvp.md), [админка](docs/admin.md),
[SDK](docs/producer-sdk.md), [MCP](docs/mcp.ru.md).
