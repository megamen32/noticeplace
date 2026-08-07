# Notify Center

[English](README.md) · [Русский](README.ru.md) · **中文** · [文档](docs/)

![Notify Center 将事件路由到人类通知渠道](assets/hero.svg)

> 面向 AI agent 和生产服务的统一通知中心。

Notify Center 接收 durable events，保存 incident，并通过 Telegram、Matrix
和电话适配器发送通知，支持 ACK、resolve、retry 和 escalation。

## 安装

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

如果只需要小型 SSH/process watcher，请使用独立的公开项目
[Notify.cli](https://github.com/megamen32/notify-cli)。

## 快速开始

1. 在受保护的 admin 中创建 project-scoped token。
2. 向 `POST /v1/events` 发送事件。
3. 在 `/admin/` 配置 topics、adapters 和 live escalation。

更多内容：[API](docs/notification-center-mvp.md)、[admin](docs/admin.md)、
[SDK](docs/producer-sdk.md)、[MCP](docs/mcp.zh.md)。
