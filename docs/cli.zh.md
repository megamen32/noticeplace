# `/usr/local/bin/notify` CLI

[English](cli.md) | [Русский](cli.ru.md) | **中文**

`notify` 是一个 Bash 工具，用于监控 Linux 进程，并在 watcher 开始、可选日志被转发、以及进程退出时发送 Telegram messages。

MCP server 在底层使用这个 CLI。

## Install

通过 npx/npm package：

```bash
npm exec -y --package github:megamen32/notify -- notify-install
```

通过 clone 的 repo 或解压的 release archive：

```bash
sudo install -m 0755 -o root -g root bin/notify /usr/local/bin/notify
```

## Telegram secrets

为运行 `notify` 的用户创建 `~/.config/secrets/notifier.env`：

```bash
mkdir -p ~/.config/secrets
cat > ~/.config/secrets/notifier.env <<'ENV'
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_CHAT_ID=123456789
# optional:
# TELEGRAM_PROXY_URL=socks5h://127.0.0.1:1080
# TELEGRAM_TIMEOUT_MS=3500
ENV
chmod 600 ~/.config/secrets/notifier.env
```

## Interactive usage

```bash
notify
notify sync_sessions
notify 12345
```

Interactive mode 可以使用 prompts 和 `fzf`。

## Non-interactive usage

用于 cron、scripts、CI 和 AI agents：

```bash
notify --non-interactive --pid 12345 --no-log --replace
notify -n --query sync_sessions --first --no-log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace
notify -n --pid 12345 --log-live /tmp/job.log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace --hard-timeout 30m
```

`--non-interactive` / `-n` 会禁用 prompts、`fzf` 和 `read`。如果 query 匹配到多个进程，且没有传入 `--first`，`notify` 会失败并列出匹配项。

## Hard timeout

```bash
notify -n --pid 12345 --no-log --hard-timeout 30m
```

支持的格式：

- `1800` — seconds
- `30m` — minutes
- `1h` — hours
- `500ms` — milliseconds，向上取整为 1 second
- `0` — 禁用 timeout

当 hard timeout 到达且被监控进程仍然存活时，`notify` 会发送 timeout message 并停止监控。它不会杀掉进程。

## Logs

- `--no-log` — 不向 Telegram 发送 log content。
- `--log-tail FILE` — 发送有限的 final log tail。
- `--log-live FILE` — 在进程运行时 stream log updates。

## Safety

`notify` 会从 `/proc/<pid>/stat` 记录 process start identity，以降低 PID reuse mistakes 的风险。它也使用 per-PID locks，`--replace` 会替换同一个 PID 的现有 watcher。
