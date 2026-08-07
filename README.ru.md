# NoticePlace

[English](README.md) · **Русский** · [中文](README.zh.md) · [Документация](docs/)

![NoticePlace — universal inbox для связи AI с человеком](assets/hero-universal-inbox.png)

> Universal inbox, через который AI может достучаться до человека любым способом.

AI продолжает работу, пока NoticePlace доставляет важное через звонок, чат,
сообщение, email или другой настроенный канал. Человек получает и подтверждает
всё в одном месте.

## Установка

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
```

Для маленького SSH/process watcher используйте отдельный публичный проект
[Notify.cli](https://github.com/megamen32/notify-cli).

## Быстрый старт

1. Создайте project-scoped token в защищённой админке.
2. Отправьте событие на `POST /v1/events`.
3. Настройте topics, adapters и live escalation в `/admin/`.

Подробнее: [API](docs/notification-center-mvp.md), [админка](docs/admin.md),
[SDK](docs/producer-sdk.md), [MCP](docs/mcp.ru.md).
