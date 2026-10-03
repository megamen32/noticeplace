#!/usr/bin/env python3
"""Synchronize NoticePlace project tokens into the local health callback map."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


SEVERITIES = ("debug", "info", "notice", "important", "critical", "emergency")


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    env_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/etc/notification-center.env")
    callback_path = Path(sys.argv[2] if len(sys.argv) > 2 else "/home/roomhacker/.config/gptadmin/health-callback.json")
    if not callback_path.exists():
        print("health callback map absent; skipped")
        return 0
    metadata = callback_path.stat()
    if metadata.st_mode & 0o077:
        raise SystemExit("health callback map must have mode 0600")
    values = env_values(env_path)
    scopes = json.loads(values.get("NOTIFY_CENTER_TOKENS_JSON", "{}"))
    if not isinstance(scopes, dict):
        raise SystemExit("NOTIFY_CENTER_TOKENS_JSON must be an object")
    document = json.loads(callback_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not str(document.get("url") or "").strip():
        raise SystemExit("health callback map is invalid")
    selected: dict[str, tuple[int, int, str]] = {}
    for token, scope in scopes.items():
        if not isinstance(token, str) or not isinstance(scope, dict):
            continue
        project = str(scope.get("project") or "").strip()
        maximum = str(scope.get("max_severity") or "notice")
        if not project or maximum not in SEVERITIES:
            continue
        jobs = scope.get("agent_jobs") if isinstance(scope.get("agent_jobs"), list) else []
        rank = (int("health-diagnosis" in jobs), SEVERITIES.index(maximum), token)
        if project not in selected or rank[:2] > selected[project][:2]:
            selected[project] = rank
    tokens = dict(document.get("tokens") or {})
    for project, (_job_rank, _severity_rank, token) in selected.items():
        tokens[project] = token
    document["tokens"] = tokens
    descriptor, temporary = tempfile.mkstemp(prefix=".health-callback.", dir=str(callback_path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, 0o600)
        os.replace(temporary, callback_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"health callback projects synchronized: {len(tokens)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
