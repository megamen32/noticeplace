# CLI `/usr/local/bin/notify`

[English](cli.md) | **Русский** | [中文](cli.zh.md)

`notify` — Bash utility, которая следит за Linux-процессом и отправляет Telegram messages, когда watcher стартует, когда optional logs стримятся, и когда процесс завершается.

MCP server использует эту CLI под капотом.

## Install

Из npx/npm package:

```bash
npm exec -y --package github:megamen32/notify -- notify-install
```

Из клонированного repo или распакованного release archive:

```bash
sudo install -m 0755 -o root -g root bin/notify /usr/local/bin/notify
```

## Telegram secrets

Создайте `~/.config/secrets/notifier.env` для пользователя, который будет запускать `notify`:

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

Interactive mode может использовать prompts и `fzf`.

## Non-interactive usage

Используйте это в cron, scripts, CI и AI agents:

```bash
notify --non-interactive --pid 12345 --no-log --replace
notify -n --query sync_sessions --first --no-log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace
notify -n --pid 12345 --log-live /tmp/job.log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace --hard-timeout 30m
```

`--non-interactive` / `-n` отключает prompts, `fzf` и `read`. Если query находит несколько процессов, `notify` падает со списком, если не передан `--first`.

## Hard timeout

```bash
notify -n --pid 12345 --no-log --hard-timeout 30m
```

Поддерживаемые форматы:

- `1800` — seconds
- `30m` — minutes
- `1h` — hours
- `500ms` — milliseconds, округляется вверх до 1 second
- `0` — отключить timeout

Когда hard timeout достигнут, а watched process всё ещё жив, `notify` отправляет timeout message и перестаёт следить. Он не убивает процесс.

## Logs

- `--no-log` — не отправлять log content в Telegram.
- `--log-tail FILE` — отправить bounded final tail лога.
- `--log-live FILE` — stream log updates, пока процесс работает.

## Safety

`notify` записывает process start identity из `/proc/<pid>/stat`, чтобы снизить риск PID reuse mistakes. Также он использует per-PID locks, а `--replace` заменяет существующий watcher для того же PID.
