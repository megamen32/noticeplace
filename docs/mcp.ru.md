# Notify MCP server

[English](mcp.md) | **Русский** | [中文](mcp.zh.md)

Notify MCP — это stdio MCP server для долгих неинтерактивных shell-задач. Он запускает команды detached, пишет логи на диск, подключает `/usr/local/bin/notify` и возвращается, не заставляя агента бодрствовать.

## Главный tool: `run_and_notify`

Используйте его для команд, которые ожидаемо идут дольше 3 минут и не требуют stdin, prompts, TUI или настоящего терминала.

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

Поведение:

1. Создаёт `~/.local/state/notify-mcp/jobs/<job_id>/`.
2. Пишет `runner.sh`, `job.log`, `meta.json` и `status.json`.
3. Запускает команду через `setsid` в фоне.
4. Подключает `/usr/local/bin/notify` к PID процесса.
5. Ждёт максимум `wait_seconds`.
6. Если процесс всё ещё идёт, возвращает `alive=true` с `job_id`, `pid` и `log_file`.

Агент должен прекратить ожидание, когда `alive=true`. Telegram сообщит о завершении или timeout.

## Встроенные инструкции для агента

Notify MCP self-describing. Его ответ `tools/list` включает long-job policy прямо в descriptions tools и descriptions свойств JSON schema. Это сделано специально: модель может понять поведение из MCP metadata без отдельного skill file.

Встроенное правило:

- Для неинтерактивных задач, которые ожидаемо идут дольше 3 минут, предпочитать `run_and_notify` вместо блокирующих shell-вызовов.
- Использовать `log_mode="tail"`, `replace=true`, `hard_timeout="30m"` и `wait_seconds <= 180`.
- Если результат содержит `alive=true` или `state="running"`, прекратить polling и сообщить `job_id`, `pid`, `log_file`.
- Использовать `job_status` или `job_tail` только для маленьких ограниченных проверок.
- Использовать `pty-mcp` для prompts, TUI, editors, interactive installers или keystrokes.

## Tools

### `run_and_notify`

Запускает новую detached неинтерактивную shell-команду и уведомляет о завершении.

Возвращает: `job_id`, `pid`, `cwd`, `log_file`, `status_file`, `runner`, `notify_attached`, `hard_timeout`, `wait_seconds`, `waited_seconds`, `alive` и `status`.

### `attach_pid`

Подключает Telegram notification к уже работающему PID.

Используйте, когда процесс был запущен вне Notify MCP, но должен прислать уведомление по завершении.

### `attach_query`

Ищет процесс по substring command line и подключает уведомление. Если найдено несколько совпадений, падает, если не указан `first=true`.

### `job_status`

Маленькая metadata/status-проверка для известной job.

### `job_tail`

Ограниченный tail лога. Держите `max_bytes` маленьким, чтобы не тратить токены.

### `list_jobs`

Список последних jobs без дампа логов.

### `kill_job`

Отправляет `TERM`, `INT` или `KILL` в process group job.

## Установка в MCP clients

### Codex

Рекомендуемая установка через npx:

```bash
codex mcp add notify -- npx -y github:megamen32/noticeplace
codex mcp list
```

Manual TOML:

```toml
[mcp_servers.notify]
command = "npx"
args = ["-y", "github:megamen32/noticeplace"]
```

Локальная Python-установка тоже поддерживается:

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

`.vscode/mcp.json` или user `mcp.json`:

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

GitHub Releases включают portable archives и npm package tarballs:

- `notify-mcp-vX.Y.Z.tar.gz`
- `notify-mcp-vX.Y.Z.zip`
- `notify-mcp-X.Y.Z.tgz`
- `SHA256SUMS`

Архив содержит `install.sh`, `bin/notify`, `mcp/notify_mcp.py`, docs и npm wrapper.

## Environment

- `NOTIFY_BIN` — путь к CLI, default `/usr/local/bin/notify`.
- `NOTIFY_MCP_STATE_DIR` — state directory, default `~/.local/state/notify-mcp/jobs`.
- `MAX_TAIL_BYTES` — default maximum log tail bytes, которые возвращает `job_tail`.

## Когда не использовать

Используйте `pty-mcp` вместо Notify MCP, если команда может задавать вопросы, открывать редактор, требовать Ctrl+C/Enter input, рисовать TUI или зависеть от terminal behavior.

## Ссылки на документацию клиентов

- Codex config basics: https://developers.openai.com/codex/config-basic
- Codex config reference: https://developers.openai.com/codex/config-reference
- OpenCode MCP servers: https://opencode.ai/docs/mcp-servers/
- VS Code MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- VS Code MCP configuration: https://code.visualstudio.com/docs/agents/reference/mcp-configuration
