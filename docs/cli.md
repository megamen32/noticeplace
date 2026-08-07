# `/usr/local/bin/notify` CLI

**Languages:** English | [Русский](cli.ru.md) | [中文](cli.zh.md)

`notify` is a Bash utility that watches a Linux process and sends Telegram messages when watching starts, when optional logs are streamed, and when the process exits.

The MCP server uses this CLI under the hood.

## Install

From npx/npm package:

```bash
npm exec -y --package github:megamen32/notify -- notify-install
```

From a cloned or extracted release archive:

```bash
sudo install -m 0755 -o root -g root bin/notify /usr/local/bin/notify
```

## Telegram secrets

Create `~/.config/secrets/notifier.env` for the user that will run `notify`:

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

Interactive mode can use prompts and `fzf`.

## Non-interactive usage

Use this in cron, scripts, CI, and AI agents:

```bash
notify --non-interactive --pid 12345 --no-log --replace
notify -n --query sync_sessions --first --no-log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace
notify -n --pid 12345 --log-live /tmp/job.log --replace
notify -n --pid 12345 --log-tail /tmp/job.log --replace --hard-timeout 30m
```

`--non-interactive` / `-n` disables prompts, `fzf`, and `read`. If a query matches multiple processes, `notify` fails with a list unless `--first` is passed.

## Hard timeout

```bash
notify -n --pid 12345 --no-log --hard-timeout 30m
```

Supported forms:

- `1800` — seconds
- `30m` — minutes
- `1h` — hours
- `500ms` — milliseconds, rounded up to 1 second
- `0` — disable timeout

When the hard timeout is reached and the watched process is still alive, `notify` sends a timeout message and stops watching. It does not kill the process.

## Logs

- `--no-log` — no log content in Telegram.
- `--log-tail FILE` — send a bounded final tail of the log.
- `--log-live FILE` — stream log updates while the process runs.

## Safety

`notify` records the process start identity from `/proc/<pid>/stat` to reduce PID reuse mistakes. It also uses per-PID locks, and `--replace` replaces an existing watcher for the same PID.
