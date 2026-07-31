# Notify MCP

[English](README.md) | **Русский** | [中文](README.zh.md)

![Notify MCP отправляет Telegram-пинги от AI-агентов](docs/assets/readme-hero.png)

**Пингуй человека, когда агенту нужно внимание.**

Notify MCP теперь в первую очередь лёгкий Telegram-пинг для AI-агентов: использовать в конце работы или перед вопросом пользователю. Долгие задачи, ожидание и возврат в правильный чат теперь должен делать `agent-resume`. Старые watcher-инструменты оставлены только для случаев, когда нужен Telegram именно по завершению процесса.

Для долгих сборок, миграций, тестовых прогонов, бэкапов и деплоев используйте `agent-resume` для ожидания/resume. `notify.send_message` — только финальный человеческий пинг: «я закончил, проверь пожалуйста».

## Быстрый старт: установка в Codex

Если MCP-клиент — Codex, одной командой добавьте Notify через реальный launcher из GitHub:

```bash
codex mcp add notify -- npx -y github:megamen32/notify
```

Перезапустите или обновите Codex и проверьте регистрацию командой `codex mcp list`. Для OpenCode, VS Code или локального stdio-процесса используйте примеры ниже.

## Зачем это агентам

- **Human ping mode:** отправить простой Telegram-пинг в конце работы или перед запросом ввода у пользователя.
- **Без ответственности за контекст:** `agent-resume` отвечает за долгие ожидания и resume; Notify только будит человека.
- **Fallback для завершения процесса:** legacy-watchers всё ещё могут прислать успех/ошибку/логи, если это явно нужно.
- **Self-describing MCP:** правила использования встроены в `tools/list`; skill не нужен для MCP-aware клиентов.
- **Простой финальный пинг:** «я закончил X, проверь» остаётся одним MCP-вызовом.

## MCP-установка за 30 секунд

Для MCP-клиентов самый простой способ — `npx`:

```bash
npx -y github:megamen32/notify
```

Эта команда запускает stdio MCP server. В пакет уже встроен Bash watcher, а launcher автоматически выставляет `NOTIFY_BIN`, поэтому MCP server работает без ручного clone.

Для Telegram-уведомлений создайте secrets для пользователя, под которым запускается MCP-клиент:

```bash
mkdir -p ~/.config/secrets
cat > ~/.config/secrets/notifier.env <<'ENV'
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_CHAT_ID=123456789
ENV
chmod 600 ~/.config/secrets/notifier.env
```

Нужна ещё и raw CLI?

```bash
npm exec -y --package github:megamen32/notify -- notify-install
# или скачайте notify-mcp-v*.tar.gz из Releases
```

## Добавить в Codex

```bash
codex mcp add notify -- npx -y github:megamen32/notify
codex mcp list
```

Или отредактируйте `~/.codex/config.toml` / `.codex/config.toml`:

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/notify"]
```

## Добавить в OpenCode

Добавьте это в `~/.config/opencode/opencode.jsonc` или проектный `opencode.jsonc`:

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

## Добавить в VS Code

Создайте `.vscode/mcp.json` в workspace или используйте **MCP: Open User Configuration**:

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

Затем запустите **MCP: List Servers** и стартуйте `notify`.

## Что видит агент

MCP server сам обучает модель правилу:

```text
Используй send_message как дополнительный Telegram-пинг в конце работы
или перед вопросом пользователю. Для долгой работы, ожидания и возврата
в правильный чат используй agent-resume.

run_and_notify / attach_pid / attach_query — legacy watcher-ы завершения
процесса. Используй их только когда человеку явно нужен Telegram при
завершении процесса.
```

## Скачиваемые release assets

Каждый GitHub release включает:

- `notify-mcp-vX.Y.Z.tar.gz` — portable bundle с `install.sh`
- `notify-mcp-vX.Y.Z.zip` — тот же bundle в zip
- `notify-mcp-X.Y.Z.tgz` — npm package tarball
- `SHA256SUMS` — checksums

## Документация

- [MCP server](docs/mcp.ru.md) — tools, schemas, поведение агента, client configs.
- [CLI tool](docs/cli.ru.md) — использование `/usr/local/bin/notify`, Telegram secrets, logs.
- [AI skill](docs/skill.ru.md) — optional fallback для клиентов, которые плохо показывают MCP tool descriptions.

## Skill всё ещё нужен?

Обычно **нет**. Для Codex, OpenCode, VS Code, Claude и других MCP-aware клиентов важные инструкции уже встроены прямо в descriptions MCP tools. Оставляйте skill только как fallback для runtimes, которые ненадёжно показывают MCP schema descriptions.

## License

MIT


### Правило для AI

`notify` теперь human ping, а не основной long-job control plane.

Используйте `agent-resume` для долгой работы, ожидания и возврата в правильный чат. Используйте `notify.send_message` только как дополнительный Telegram-пинг:

- в конце: «я закончил пересборку X, проверь пожалуйста»;
- перед вопросом пользователю: «нужен твой ввод по Y, посмотри пожалуйста»;
- когда важна человеческая видимость milestone.

Не используйте `notify` как механизм по умолчанию для долгого ожидания/resume. Это задача `agent-resume`.
