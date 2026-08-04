#!/usr/bin/env python3
"""Minimal stdio MCP server for /usr/local/bin/notify.

Designed for AI agents: submit lightweight human-facing notification cards.
For long background work and returning to the right chat, use agent-resume;
use notify at the end of work or before asking the user a question.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import urllib.error
import urllib.request
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SERVER_NAME = "notify-mcp"
SERVER_VERSION = "1.2.0"
NOTIFY_BIN = Path(os.environ.get("NOTIFY_BIN", "/usr/local/bin/notify"))
STATE_DIR = Path(os.environ.get("NOTIFY_MCP_STATE_DIR", "~/.local/state/notify-mcp")).expanduser()
JOBS_DIR = STATE_DIR / "jobs"
MAX_TAIL_BYTES = int(os.environ.get("NOTIFY_MCP_MAX_TAIL_BYTES", "20000"))
_RUNTIME_READY = False
_RUNTIME_READY_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_duration_seconds(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower()
    if not text:
        return default
    units = (("ms", 1 / 1000), ("s", 1), ("m", 60), ("h", 3600))
    for suffix, mult in units:
        if text.endswith(suffix):
            num = text[: -len(suffix)].strip()
            try:
                seconds = float(num) * mult
            except ValueError as e:
                raise ValueError(f"invalid duration: {value!r}") from e
            return max(0, int(seconds + 0.999))
    try:
        return max(0, int(float(text)))
    except ValueError as e:
        raise ValueError(f"invalid duration: {value!r}") from e


def ensure_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_runtime_ready() -> None:
    """Prepare the shared job storage used by both stdio and HTTP transports."""
    global _RUNTIME_READY
    if _RUNTIME_READY:
        return
    with _RUNTIME_READY_LOCK:
        if _RUNTIME_READY:
            return
        ensure_dirs()
        _RUNTIME_READY = True


def json_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    os.replace(tmp, path)


def json_read(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return dict(default or {})


def is_pid_alive(pid: int) -> bool:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        if proc_stat.exists():
            parts = proc_stat.read_text(errors="replace").split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def tail_file(path: Path, max_bytes: int = MAX_TAIL_BYTES) -> str:
    if not path.exists() or not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace")


def run_quick(args: List[str], cwd: Optional[str] = None, timeout: int = 15) -> Dict[str, Any]:
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    return {
        "returncode": p.returncode,
        "stdout": p.stdout[-12000:],
        "stderr": p.stderr[-12000:],
        "argv": args,
    }


def parse_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            env[key] = value
    return env


def notify_center_config() -> Dict[str, str]:
    """Load a project-scoped producer identity, never Telegram credentials."""
    secrets_file = Path(os.environ.get("NOTIFY_SECRETS_FILE", "~/.config/secrets/notifier.env")).expanduser()
    file_env = parse_env_file(secrets_file)
    token = os.environ.get("NOTIFY_CENTER_TOKEN") or file_env.get("NOTIFY_CENTER_TOKEN") or ""
    event_url = os.environ.get("NOTIFY_CENTER_EVENT_URL") or file_env.get("NOTIFY_CENTER_EVENT_URL") or ""
    project = os.environ.get("NOTIFY_CENTER_PROJECT") or file_env.get("NOTIFY_CENTER_PROJECT") or "notify-mcp"
    recipient = os.environ.get("NOTIFY_CENTER_RECIPIENT") or file_env.get("NOTIFY_CENTER_RECIPIENT") or "me"
    if not token or not event_url:
        raise ValueError(f"NOTIFY_CENTER_EVENT_URL/NOTIFY_CENTER_TOKEN not configured in env or {secrets_file}")
    return {"token": token, "event_url": event_url, "project": project, "recipient": recipient}


def notify_center_send_text(message: str, title: str, request_key: str) -> Dict[str, Any]:
    """Submit a normal-priority MCP card to the durable center."""
    cfg = notify_center_config()
    payload = json.dumps({
        "schema": "notify.event.v1", "project": cfg["project"], "recipient": cfg["recipient"],
        "kind": "notification", "severity": "notice", "title": title or "Notify MCP", "body": message,
        "dedup_key": request_key,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        cfg["event_url"], data=payload, method="POST",
        headers={"Authorization": f"Bearer {cfg['token']}", "Idempotency-Key": request_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=8) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        status = error.code
        body = json.loads(error.read() or b"{}")
    return {"http_status": status, "ok": status == 202, "incident_id": body.get("incident_id"), "event_id": body.get("event_id")}


def split_notification_message(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    rest = text
    while rest:
        chunk = rest[:limit]
        cut = max(chunk.rfind("\n"), chunk.rfind(" "))
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return parts


def tool_send_message(args: Dict[str, Any]) -> Dict[str, Any]:
    message = str(args.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    title = str(args.get("title") or "").strip()
    text = f"{title}\n\n{message}" if title else message
    parts = split_notification_message(text)
    sent = []
    for idx, part in enumerate(parts, start=1):
        if len(parts) > 1:
            part = f"[{idx}/{len(parts)}]\n" + part
        sent.append(notify_center_send_text(part, title, f"notify-mcp:{uuid.uuid4().hex}"))
    ok = all(item.get("ok") for item in sent)
    return {"ok": ok, "sent_parts": len(sent), "results": sent}


def tool_call(args: Dict[str, Any]) -> Dict[str, Any]:
    """Place one direct configured call without creating or escalating an incident."""
    channel = str(args.get("channel") or "phone").strip().lower()
    if channel not in {"phone", "matrix"}:
        raise ValueError("channel must be one of: phone, matrix")
    message = str(args.get("message") or "").strip()
    incident = {
        "id": f"direct-call-{uuid.uuid4().hex}",
        "project": "notify-mcp",
        "kind": "direct",
        "severity": "notice",
        "title": "Direct Notify call",
        "body": message or "Direct call requested by the user.",
    }
    if channel == "matrix":
        from notification_center.http_api import matrix_call_from_environment

        adapter = matrix_call_from_environment()
        if adapter is None:
            raise RuntimeError("Matrix call adapter is not configured")
        return {"ok": True, "channel": channel, "receipt": adapter.send({"incident": incident})}

    from notification_center.http_api import android_phone_from_environment

    adapter = android_phone_from_environment()
    if adapter is None or not getattr(adapter, "can_phone_call", False):
        raise RuntimeError("Android phone call adapter is not configured")
    payload: Dict[str, Any] = {"kind": "direct", "incident": incident}
    if message:
        payload["voice"] = {
            "text": message,
            "repeat": args.get("repeat") or 2,
            "hangup_after": args.get("hangup_after", True),
        }
        if args.get("connect_wait_seconds") is not None:
            payload["voice"]["connect_wait_seconds"] = args["connect_wait_seconds"]
    adapter.phone_call(payload)
    return {"ok": True, "channel": channel, "receipt": {"started": True}}


def notify_args_for(pid: Optional[int] = None, query: Optional[str] = None, log_mode: str = "none", log_file: Optional[str] = None, replace: bool = True, first: bool = False, hard_timeout: Any = None) -> List[str]:
    if not NOTIFY_BIN.exists():
        raise FileNotFoundError(f"notify binary not found: {NOTIFY_BIN}")
    args = [str(NOTIFY_BIN), "--non-interactive"]
    if pid is not None:
        args += ["--pid", str(pid)]
    elif query:
        args += ["--query", query]
        if first:
            args.append("--first")
    else:
        raise ValueError("pid or query is required")

    if log_mode == "none":
        args.append("--no-log")
    elif log_mode in ("tail", "live"):
        if not log_file:
            raise ValueError(f"log_file is required for log_mode={log_mode}")
        args += [f"--log-{log_mode}", log_file]
    else:
        raise ValueError("log_mode must be one of: none, tail, live")

    if hard_timeout not in (None, "", 0, "0"):
        args += ["--hard-timeout", str(hard_timeout)]

    if replace:
        args.append("--replace")
    return args


def tool_attach_pid(args: Dict[str, Any]) -> Dict[str, Any]:
    pid = int(args.get("pid") or 0)
    if pid <= 0:
        raise ValueError("pid must be a positive integer")
    log_mode = str(args.get("log_mode") or "none")
    log_file = args.get("log_file")
    replace = bool(args.get("replace", True))
    hard_timeout = args.get("hard_timeout", "30m")
    result = run_quick(notify_args_for(pid=pid, log_mode=log_mode, log_file=log_file, replace=replace, hard_timeout=hard_timeout))
    return {"ok": result["returncode"] == 0, "pid": pid, "notify": result}


def tool_attach_query(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    log_mode = str(args.get("log_mode") or "none")
    log_file = args.get("log_file")
    replace = bool(args.get("replace", True))
    first = bool(args.get("first", False))
    hard_timeout = args.get("hard_timeout", "30m")
    result = run_quick(notify_args_for(query=query, log_mode=log_mode, log_file=log_file, replace=replace, first=first, hard_timeout=hard_timeout))
    return {"ok": result["returncode"] == 0, "query": query, "notify": result}


def shell_quote_env(value: str) -> str:
    return shlex.quote(value)


def tool_run_and_notify(args: Dict[str, Any]) -> Dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    cwd = str(args.get("cwd") or os.getcwd())
    cwd_path = Path(cwd).expanduser().resolve()
    if not cwd_path.exists() or not cwd_path.is_dir():
        raise FileNotFoundError(f"cwd not found or not a directory: {cwd_path}")

    log_mode = str(args.get("log_mode") or "tail")
    if log_mode not in ("none", "tail", "live"):
        raise ValueError("log_mode must be one of: none, tail, live")
    replace = bool(args.get("replace", True))
    hard_timeout = args.get("hard_timeout", "30m")
    hard_timeout_seconds = parse_duration_seconds(hard_timeout, default=1800)
    wait_seconds = min(parse_duration_seconds(args.get("wait_seconds", 0), default=0), hard_timeout_seconds or 10**9)
    note = str(args.get("note") or "")

    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    log_file = job_dir / "job.log"
    status_file = job_dir / "status.json"
    runner = job_dir / "runner.sh"

    meta: Dict[str, Any] = {
        "job_id": job_id,
        "command": command,
        "cwd": str(cwd_path),
        "log_file": str(log_file),
        "status_file": str(status_file),
        "created_at": now_iso(),
        "note": note,
        "hard_timeout": hard_timeout,
        "hard_timeout_seconds": hard_timeout_seconds,
        "wait_seconds": wait_seconds,
        "state": "starting",
    }
    json_write(job_dir / "meta.json", meta)

    runner.write_text(f"""#!/usr/bin/env bash
set +e
cd {shell_quote_env(str(cwd_path))}
cat > {shell_quote_env(str(status_file))} <<'STATUS_JSON_EOF'
{{"job_id": {json.dumps(job_id)}, "state": "running", "started_at": {json.dumps(now_iso())}}}
STATUS_JSON_EOF
(
  echo "== notify-mcp job {job_id} =="
  echo "started_at: $(date -Is)"
  echo "cwd: {str(cwd_path)}"
  echo "command: {command}"
  echo
  bash -lc {shell_quote_env(command)}
)
rc=$?
python3 -c 'import json,sys,time; from datetime import datetime,timezone; p=sys.argv[1]; rc=int(sys.argv[2]); data={{"job_id": {json.dumps(job_id)}, "state":"finished", "returncode":rc, "finished_at":datetime.now(timezone.utc).isoformat(), "finished_epoch":time.time()}}; open(p,"w").write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))' {shell_quote_env(str(status_file))} "$rc"
exit "$rc"
""")
    runner.chmod(0o755)

    launch = subprocess.run(
        ["/bin/bash", "-lc", f"setsid /usr/bin/env bash {shlex.quote(str(runner))} > {shlex.quote(str(log_file))} 2>&1 < /dev/null & echo $!"],
        cwd=str(cwd_path),
        text=True,
        capture_output=True,
        timeout=10,
    )
    if launch.returncode != 0:
        raise RuntimeError(f"failed to launch detached job: {launch.stderr or launch.stdout}")
    try:
        pid = int(launch.stdout.strip().splitlines()[-1])
    except Exception as e:
        raise RuntimeError(f"failed to parse launched PID from: {launch.stdout!r}") from e

    meta.update({"pid": pid, "state": "running", "started_at": now_iso(), "runner": str(runner), "launch_stdout": launch.stdout[-2000:], "launch_stderr": launch.stderr[-2000:]})
    json_write(job_dir / "meta.json", meta)

    notify_log_file = str(log_file) if log_mode in ("tail", "live") else None
    notify_result = run_quick(notify_args_for(pid=pid, log_mode=log_mode, log_file=notify_log_file, replace=replace, hard_timeout=hard_timeout))
    meta["notify"] = notify_result
    meta["notify_attached"] = notify_result["returncode"] == 0
    json_write(job_dir / "meta.json", meta)

    waited_seconds = 0.0
    if wait_seconds > 0:
        deadline = time.time() + wait_seconds
        while time.time() < deadline and is_pid_alive(pid):
            sleep_for = min(2.0, max(0.1, deadline - time.time()))
            time.sleep(sleep_for)
        waited_seconds = max(0.0, wait_seconds - max(0.0, deadline - time.time()))

    status_snapshot = json_read(status_file, {"state": "running"})
    alive_snapshot = is_pid_alive(pid)

    return {
        "ok": notify_result["returncode"] == 0,
        "job_id": job_id,
        "pid": pid,
        "cwd": str(cwd_path),
        "log_file": str(log_file),
        "status_file": str(status_file),
        "runner": str(runner),
        "notify_attached": notify_result["returncode"] == 0,
        "hard_timeout": hard_timeout,
        "hard_timeout_seconds": hard_timeout_seconds,
        "wait_seconds": wait_seconds,
        "waited_seconds": round(waited_seconds, 3),
        "alive": alive_snapshot,
        "status": status_snapshot,
        "notify_stdout": notify_result["stdout"][-3000:],
        "notify_stderr": notify_result["stderr"][-3000:],
        "message": "Started in background with Telegram notification. If still alive after wait_seconds, stop waiting and return this job_id instead of polling and burning tokens.",
    }


def job_paths(job_id: str) -> Dict[str, Path]:
    if not job_id or "/" in job_id or ".." in job_id:
        raise ValueError("invalid job_id")
    job_dir = JOBS_DIR / job_id
    return {"dir": job_dir, "meta": job_dir / "meta.json", "status": job_dir / "status.json", "log": job_dir / "job.log"}


def tool_job_status(args: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(args.get("job_id") or "")
    paths = job_paths(job_id)
    if not paths["meta"].exists():
        raise FileNotFoundError(f"job not found: {job_id}")
    meta = json_read(paths["meta"])
    status = json_read(paths["status"], {"state": "unknown"})
    pid = int(meta.get("pid") or 0)
    alive = is_pid_alive(pid) if pid else False
    return {"job_id": job_id, "alive": alive, "pid": pid, "meta": meta, "status": status, "log_file": str(paths["log"]), "log_size": paths["log"].stat().st_size if paths["log"].exists() else 0}


def tool_job_tail(args: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(args.get("job_id") or "")
    max_bytes = int(args.get("max_bytes") or 6000)
    max_bytes = max(1, min(max_bytes, MAX_TAIL_BYTES))
    paths = job_paths(job_id)
    if not paths["dir"].exists():
        raise FileNotFoundError(f"job not found: {job_id}")
    return {"job_id": job_id, "log_file": str(paths["log"]), "tail": tail_file(paths["log"], max_bytes=max_bytes)}


def tool_list_jobs(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(args.get("limit") or 20)
    rows = []
    for d in sorted(JOBS_DIR.glob("*"), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        meta = json_read(d / "meta.json")
        status = json_read(d / "status.json", {"state": meta.get("state", "unknown")})
        pid = int(meta.get("pid") or 0)
        rows.append({"job_id": d.name, "pid": pid, "alive": is_pid_alive(pid) if pid else False, "state": status.get("state"), "returncode": status.get("returncode"), "created_at": meta.get("created_at"), "command": meta.get("command"), "log_file": meta.get("log_file")})
        if len(rows) >= limit:
            break
    return {"count": len(rows), "jobs": rows}


def tool_kill_job(args: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(args.get("job_id") or "")
    sig_name = str(args.get("signal") or "TERM").upper().removeprefix("SIG")
    sig = getattr(signal, "SIG" + sig_name, None)
    if sig is None:
        raise ValueError("unknown signal; use TERM, INT, KILL")
    st = tool_job_status({"job_id": job_id})
    pid = int(st["pid"])
    if not pid:
        raise ValueError("job has no pid")
    try:
        os.killpg(pid, sig)
        sent = True
    except ProcessLookupError:
        sent = False
    return {"job_id": job_id, "pid": pid, "signal": "SIG" + sig_name, "sent": sent}


TOOLS = {
    "send_message": {
        "description": "Send a plain Telegram message immediately. Preferred use: ping the human at the end of work or right before asking a question, e.g. 'I finished X, please check'. This is only an extra notification; agent-resume handles long waits and context resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message text to send to Telegram."},
                "title": {"type": ["string", "null"], "description": "Optional title/prefix, e.g. 'GPTAdmin finished'."},
                "disable_web_page_preview": {"type": "boolean", "default": True},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "handler": tool_send_message,
    },
    "call": {
        "description": "Place one direct call through the configured phone or Matrix adapter. Use only on an explicit user request; this does not create an incident or wait for escalation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["phone", "matrix"], "default": "phone", "description": "phone places a direct cellular call; matrix starts a direct MatrixRTC call."},
                "message": {"type": ["string", "null"], "description": "Optional call context. For phone, it is spoken acoustically through speakerphone."},
                "repeat": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2, "description": "Phone only: how many times to speak message."},
                "hangup_after": {"type": "boolean", "default": True, "description": "Phone only: end the call after spoken playback."},
                "connect_wait_seconds": {"type": ["number", "null"], "minimum": 5, "maximum": 30, "default": 12, "description": "Phone only: fixed wait before speakerphone playback; no answer detection."},
            },
            "additionalProperties": False,
        },
        "handler": tool_call,
    },
    "run_and_notify": {
        "description": (
            "Legacy process watcher: run a non-interactive shell command and send Telegram on completion. "
            "Do not use this as the default long-wait/resume mechanism; use agent-resume for long work and returning to the correct chat. "
            "Use run_and_notify only when the human specifically needs a process-completion Telegram notification."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run via bash -lc in the background. Must be non-interactive; use pty-mcp for prompts/TUI/keystrokes.",
                },
                "cwd": {
                    "type": ["string", "null"],
                    "default": "/home/roomhacker",
                    "description": "Working directory for the command. Defaults to the MCP process cwd/home.",
                },
                "log_mode": {
                    "type": "string",
                    "enum": ["none", "tail", "live"],
                    "default": "tail",
                    "description": "How notify should include logs in Telegram. Prefer tail only for explicit process-completion notifications.",
                },
                "replace": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use Telegram replace/edit mode when possible to avoid notification spam. Prefer true.",
                },
                "note": {
                    "type": ["string", "null"],
                    "description": "Optional human note stored in job metadata; useful to explain why the job was launched.",
                },
                "wait_seconds": {
                    "type": ["integer", "null"],
                    "default": 0,
                    "description": "Bounded synchronous wait before returning. For jobs expected >3 minutes use <=180; use 0 to return immediately. Never set this above hard_timeout.",
                },
                "hard_timeout": {
                    "type": ["string", "integer", "null"],
                    "default": "30m",
                    "description": "Maximum notify watcher lifetime, e.g. 1800, 30m, 1h. Default 30m. If reached while the process is still alive, notify sends timeout and stops watching.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "handler": tool_run_and_notify,
    },
    "attach_pid": {
        "description": (
            "Legacy process watcher: attach Telegram completion notification to an already running non-interactive PID. "
            "Prefer agent-resume for long work/context resume; use this only when the human specifically needs process-completion Telegram."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Existing process PID to watch."},
                "log_mode": {
                    "type": "string",
                    "enum": ["none", "tail", "live"],
                    "default": "none",
                    "description": "Log mode for Telegram. If 'tail' or 'live', log_file is required.",
                },
                "log_file": {
                    "type": ["string", "null"],
                    "description": "Path to a log file for log_mode='tail' or 'live'.",
                },
                "replace": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use Telegram replace/edit mode when possible. Prefer true.",
                },
                "hard_timeout": {
                    "type": ["string", "integer", "null"],
                    "default": "30m",
                    "description": "Maximum notify watcher lifetime, e.g. 30m. 0 disables. Default 30m.",
                },
            },
            "required": ["pid"],
            "additionalProperties": False,
        },
        "handler": tool_attach_pid,
    },
    "attach_query": {
        "description": (
            "Legacy process watcher: attach Telegram completion notification to a process found by command substring. "
            "Prefer agent-resume for long work/context resume; use this only when the human specifically needs process-completion Telegram."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to search in process command lines."},
                "first": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, attach to the first matching process when multiple matches exist. Prefer false for safety.",
                },
                "log_mode": {
                    "type": "string",
                    "enum": ["none", "tail", "live"],
                    "default": "none",
                    "description": "Log mode for Telegram. If 'tail' or 'live', log_file is required.",
                },
                "log_file": {
                    "type": ["string", "null"],
                    "description": "Path to a log file for log_mode='tail' or 'live'.",
                },
                "replace": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use Telegram replace/edit mode when possible. Prefer true.",
                },
                "hard_timeout": {
                    "type": ["string", "integer", "null"],
                    "default": "30m",
                    "description": "Maximum notify watcher lifetime, e.g. 30m. 0 disables. Default 30m.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": tool_attach_query,
    },
    "job_status": {
        "description": "Small bounded status check for a legacy notify-mcp background job. Use sparingly; agent-resume is preferred for long work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id returned by run_and_notify."},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "handler": tool_job_status,
    },
    "job_tail": {
        "description": "Return a bounded tail of a legacy notify-mcp job log. Only request small tails for explicit debugging; do not use this as a polling loop.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id returned by run_and_notify."},
                "max_bytes": {
                    "type": ["integer", "null"],
                    "default": 6000,
                    "description": "Maximum log bytes to return. Keep small to avoid token waste.",
                },
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "handler": tool_job_tail,
    },
    "list_jobs": {
        "description": "List recent notify-mcp jobs without dumping logs. Safe for a quick overview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": ["integer", "null"], "default": 20, "description": "Maximum number of recent jobs to list."},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_jobs,
    },
    "kill_job": {
        "description": "Send a signal to a notify-mcp job process group. Use only when the user asks to stop/cancel a job or the job is clearly unsafe/stuck.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id returned by run_and_notify."},
                "signal": {"type": ["string", "null"], "default": "TERM", "description": "Signal name: TERM, INT, or KILL. Prefer TERM first."},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "handler": tool_kill_job,
    },
}


def tool_specs() -> List[Dict[str, Any]]:
    """Return the exact stdio tool manifest in insertion order."""
    return [{"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]} for name, spec in TOOLS.items()]


def dispatch(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Execute one JSON-RPC request and return the wire payload or no-op."""
    method = req.get("method")
    req_id = req.get("id")
    try:
        if method == "tools/call":
            ensure_runtime_ready()
        if method == "initialize":
            payload = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
        elif method == "tools/list":
            payload = {"tools": tool_specs()}
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            targs = params.get("arguments") or {}
            if name not in TOOLS:
                raise ValueError(f"unknown tool: {name}")
            result = TOOLS[name]["handler"](targs)
            payload = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        elif method and method.startswith("notifications/"):
            return None
        else:
            payload = {}
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": payload}
    except Exception as e:
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e), "data": e.__class__.__name__}}


def handle(req: Dict[str, Any]) -> None:
    """Keep the stdio contract stable while sharing the same dispatcher."""
    response = dispatch(req)
    if response is None:
        return
    if "error" in response:
        print(f"notify_mcp error: {response['error']['message']}", file=sys.stderr, flush=True)
    print(json.dumps(response, ensure_ascii=False), flush=True)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            print(f"bad request: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
