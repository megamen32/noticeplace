# Notify MCP server

[English](mcp.md) | [Русский](mcp.ru.md) | **中文**

Notify MCP 是一个用于长时间非交互式 shell 任务的 stdio MCP server。它会 detached 启动命令，把日志写入磁盘，连接 `/usr/local/bin/notify`，然后返回，不需要让智能体一直保持等待状态。

## 主要 tool：`run_and_notify`

用于预计超过 3 分钟、并且不需要 stdin、prompts、TUI 或真实终端的命令。

```json
{
  "command": "pytest -q",
  "cwd": "/home/me/project",
  "log_mode": "tail",
  "replace": true,
  "wait_seconds": 180,
  "hard_timeout": "30m",
  "note": "full test suite"
}
```

行为：

1. 创建 `~/.local/state/notify-mcp/jobs/<job_id>/`。
2. 写入 `runner.sh`、`job.log`、`meta.json` 和 `status.json`。
3. 使用 `setsid` 在后台启动命令。
4. 将 `/usr/local/bin/notify` 连接到进程 PID。
5. 最多等待 `wait_seconds`。
6. 如果进程仍在运行，返回带有 `job_id`、`pid` 和 `log_file` 的 `alive=true`。

当 `alive=true` 时，智能体应该停止等待。Telegram 会报告完成或 timeout。

## 内置智能体说明

Notify MCP 是 self-describing 的。它的 `tools/list` 响应会把 long-job policy 直接放在 tool descriptions 和 JSON schema property descriptions 中。这是有意设计的：模型可以从 MCP metadata 中学习行为，不需要额外的 skill file。

内置规则：

- 对于预计超过 3 分钟的非交互式任务，优先使用 `run_and_notify`，而不是阻塞式 shell 调用。
- 使用 `log_mode="tail"`、`replace=true`、`hard_timeout="30m"` 和 `wait_seconds <= 180`。
- 如果结果包含 `alive=true` 或 `state="running"`，停止 polling，并报告 `job_id`、`pid` 和 `log_file`。
- 只在小范围检查时使用 `job_status` 或 `job_tail`。
- 对 prompts、TUI、editors、interactive installers 或 keystrokes 使用 `pty-mcp`。

## Tools

### `run_and_notify`

启动新的 detached 非交互式 shell 命令，并在完成时通知。

返回：`job_id`、`pid`、`cwd`、`log_file`、`status_file`、`runner`、`notify_attached`、`hard_timeout`、`wait_seconds`、`waited_seconds`、`alive` 和 `status`。

### `attach_pid`

给已经运行的 PID 添加 Telegram notification。

当进程不是由 Notify MCP 启动、但仍需要完成通知时使用。

### `attach_query`

通过 command line substring 查找进程并添加通知。如果有多个匹配且没有 `first=true`，会失败。

### `job_status`

对已知 job 做小范围 metadata/status 检查。

### `job_tail`

返回有限的日志 tail。保持 `max_bytes` 较小，以避免浪费 token。

### `list_jobs`

列出最近 jobs，不 dump logs。

### `kill_job`

向 job process group 发送 `TERM`、`INT` 或 `KILL`。

## 安装到 MCP clients

### Codex

推荐通过 npx 安装：

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
codex mcp list
```

手动 TOML：

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/noticeplace"]
```

也支持本地 Python 安装：

```toml
[mcp_servers.notify]
command = "/usr/bin/python3"
args = ["/home/YOU/.local/share/notify/mcp/notify_mcp.py"]
```

### OpenCode

```jsonc
{
  "mcp": {
    "notify": {
      "type": "local",
      "command": ["npx", "-y", "github:megamen32/noticeplace"],
      "enabled": true
    }
  }
}
```

### VS Code

`.vscode/mcp.json` 或 user `mcp.json`：

```json
{
  "servers": {
    "notify": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "github:megamen32/noticeplace"]
    }
  }
}
```

## Release assets

GitHub Releases 包含 portable archives 和 npm package tarballs：

- `notify-mcp-vX.Y.Z.tar.gz`
- `notify-mcp-vX.Y.Z.zip`
- `notify-mcp-X.Y.Z.tgz`
- `SHA256SUMS`

压缩包包含 `install.sh`、`bin/notify`、`mcp/notify_mcp.py`、docs 和 npm wrapper。

## Environment

- `NOTIFY_BIN` — CLI 路径，默认 `/usr/local/bin/notify`。
- `NOTIFY_MCP_STATE_DIR` — state directory，默认 `~/.local/state/notify-mcp/jobs`。
- `MAX_TAIL_BYTES` — `job_tail` 返回的默认最大日志 tail 字节数。

## 什么时候不要使用它

如果命令可能提问、打开编辑器、需要 Ctrl+C/Enter input、渲染 TUI 或依赖 terminal behavior，请使用 `pty-mcp`，不要使用 Notify MCP。

## 客户端文档参考

- Codex config basics: https://developers.openai.com/codex/config-basic
- Codex config reference: https://developers.openai.com/codex/config-reference
- OpenCode MCP servers: https://opencode.ai/docs/mcp-servers/
- VS Code MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- VS Code MCP configuration: https://code.visualstudio.com/docs/agents/reference/mcp-configuration
