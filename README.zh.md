# Notify MCP

[English](README.md) | [Русский](README.ru.md) | **中文**

![Notify MCP 从 AI agent 发送 Telegram 提醒](docs/assets/readme-hero.png)

**当 agent 需要人类注意时发送提醒。**

Notify MCP 现在主要是给 AI agent 使用的轻量 Telegram 人类提示：在工作结束时或向用户提问之前发送提醒。长任务、等待以及回到正确聊天上下文应由 `agent-resume` 处理。旧的进程 watcher 仍然保留，只用于确实需要“进程结束时发 Telegram”的场景。

对于长构建、迁移、测试、备份和部署，请使用 `agent-resume` 进行等待/resume。`notify.send_message` 只作为面向人的最终提示：“我完成了，请检查”。

## 快速开始：安装到 Codex

如果 MCP 客户端是 Codex，可以用一条命令通过 GitHub 中真实的 launcher 添加 Notify：

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

重启或重新加载 Codex，然后运行 `codex mcp list` 确认服务器已注册。OpenCode、VS Code 或本地 stdio 进程请使用下面对应的配置示例。

## 为什么智能体需要它

- **Human ping mode：** 在工作结束或向用户请求输入前发送简单 Telegram 提醒。
- **不负责上下文恢复：** `agent-resume` 负责长等待和 resume；Notify 只提醒人。
- **进程完成 fallback：** legacy watchers 仍可在明确需要时发送成功/失败/日志提醒。
- **Self-describing MCP：** 使用规则内置在 `tools/list` 里；支持 MCP 的客户端不需要 skill。
- **简单最终提醒：** “我完成了 X，请检查” 只需要一次 MCP 调用。

## 30 秒 MCP 安装

对 MCP 客户端来说，最简单的方式是 `npx`：

```bash
npx -y github:megamen32/notify
```

这个命令会启动 stdio MCP server。包里已经包含 Bash watcher，并且 launcher 会自动设置 `NOTIFY_BIN`，所以 MCP server 不需要手动 clone 也能运行。

如果要使用 Telegram 通知，请为运行 MCP 客户端的用户创建 secrets：

```bash
mkdir -p ~/.config/secrets
cat > ~/.config/secrets/notifier.env <<'ENV'
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_CHAT_ID=123456789
ENV
chmod 600 ~/.config/secrets/notifier.env
```

还想安装 raw CLI？

```bash
npm exec -y --package github:megamen32/notify -- notify-install
# 或者从 Releases 下载 notify-mcp-v*.tar.gz
```

## 添加到 Codex

```bash
codex mcp add notify -- npx -y github:megamen32/notify
codex mcp list
```

或者编辑 `~/.codex/config.toml` / `.codex/config.toml`：

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/notify"]
```

## 添加到 OpenCode

把下面内容加入 `~/.config/opencode/opencode.jsonc` 或项目的 `opencode.jsonc`：

```jsonc
{
  "mcp": {
    "notify": {
      "type": "local",
      "command": ["npx", "-y", "github:megamen32/notify"],
      "enabled": true
    }
  }
}
```

## 添加到 VS Code

在 workspace 中创建 `.vscode/mcp.json`，或者使用 **MCP: Open User Configuration**：

```json
{
  "servers": {
    "notify": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "github:megamen32/notify"]
    }
  }
}
```

然后运行 **MCP: List Servers** 并启动 `notify`。

## 智能体会看到什么

MCP server 会自己把规则告诉模型：

```text
Use send_message as an extra Telegram ping at the end of work or before
asking the user a question. For long work, waiting, and returning to the
correct chat, use agent-resume.

run_and_notify / attach_pid / attach_query are legacy process-completion
watchers. Use them only when the human specifically needs Telegram when a
process exits.
```

## 可下载的 release assets

每个 GitHub release 都包含：

- `notify-mcp-vX.Y.Z.tar.gz` — 带 `install.sh` 的 portable bundle
- `notify-mcp-vX.Y.Z.zip` — zip 格式的同一 bundle
- `notify-mcp-X.Y.Z.tgz` — npm package tarball
- `SHA256SUMS` — 校验和

## 文档

- [MCP server](docs/mcp.zh.md) — tools、schemas、智能体行为、client configs。
- [CLI tool](docs/cli.zh.md) — `/usr/local/bin/notify` 用法、Telegram secrets、logs。
- [AI skill](docs/skill.zh.md) — 如果客户端不能很好显示 MCP tool descriptions，可作为 optional fallback。

## 还需要 skill 吗？

通常**不需要**。对于 Codex、OpenCode、VS Code、Claude 和其他 MCP-aware 客户端，重要说明已经直接内置在 MCP tool descriptions 里。只有当某个 runtime 不能可靠显示 MCP schema descriptions 时，才把 skill 作为 fallback 保留。

## License

MIT


### AI 使用规则

`notify` 现在是 human ping，不是主要的 long-job control plane。

长任务、等待以及回到正确聊天请使用 `agent-resume`。`notify.send_message` 只作为额外 Telegram 提醒：

- 结束时：“我完成了 X，请检查”；
- 提问前：“我需要你确认 Y，请看一下”；
- 当某个 milestone 需要人看到时。

不要把 `notify` 当作默认的长等待/resume 机制；这是 `agent-resume` 的职责。
