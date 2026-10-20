# NoticePlace

[English](README.md) · [Русский](README.ru.md) · **中文** · [文档](docs/)

![NoticePlace 是 AI 与人类之间的 Universal Outbox](assets/hero-universal-outbox.png)

> 面向 AI-to-human attention 的 Universal Outbox。

Notice Place 是通知、问题和升级策略层，通过一个经过认证的 API 和 MCP
对外提供入口；UserIO 是跨 Telegram、Matrix、Telemost、SIM/GSM2SIP 等能力
渠道的通信网关。渠道能力按账户单独验证；Agent Herder 独立负责恢复原生
智能体会话。请阅读[通信架构与职责边界](docs/communications-architecture.md)。

AI 不会因为等待你的回答而停止工作。NoticePlace 会通过电话、聊天、消息、
email 或其他已配置渠道发送重要提醒。独立的 [Universal Inbox](https://github.com/megamen32/universal-inbox)
负责接收和整理返回的人类消息。

## 安装

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
```

如果只需要小型 SSH/process watcher，请使用独立的公开项目
[Notify](https://github.com/megamen32/notify) — 小型 CLI 和 AI skill。

## 快速开始

1. 在受保护的 admin 中创建 project-scoped token。
2. 向 `POST /v1/events` 发送事件。
3. 在 `/admin/` 配置 topics、adapters 和 live escalation。

更多内容：[API](docs/notification-center-mvp.md)、[admin](docs/admin.md)、
[完整链路强制测试](docs/mandatory-e2e-supertest.md)、
[SDK](docs/producer-sdk.md)、[MCP](docs/mcp.zh.md)、
[通信架构与职责边界](docs/communications-architecture.md)。
