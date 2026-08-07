# NoticePlace

[English](README.md) · [Русский](README.ru.md) · **中文** · [文档](docs/)

![NoticePlace 是 AI 与人类之间的 Universal Outbox](assets/hero-universal-outbox.png)

> 面向 AI-to-human attention 的 Universal Outbox。

AI 不会因为等待你的回答而停止工作。NoticePlace 会通过电话、聊天、消息、
email 或其他已配置渠道发送重要提醒。独立的 [Universal Inbox](https://github.com/megamen32/universal-inbox)
负责接收和整理返回的人类消息。

## 安装

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
```

如果只需要小型 SSH/process watcher，请使用独立的公开项目
[Notify.cli](https://github.com/megamen32/notify-cli)。

## 快速开始

1. 在受保护的 admin 中创建 project-scoped token。
2. 向 `POST /v1/events` 发送事件。
3. 在 `/admin/` 配置 topics、adapters 和 live escalation。

更多内容：[API](docs/notification-center-mvp.md)、[admin](docs/admin.md)、
[SDK](docs/producer-sdk.md)、[MCP](docs/mcp.zh.md)。
