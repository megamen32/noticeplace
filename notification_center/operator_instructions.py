"""Project-scoped operator guidance for new NoticePlace repair sessions."""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from typing import TypedDict

from .core import NotificationCenter, ValidationError
from .health_workflow import _BEARER_RE, _SECRET_ASSIGNMENT_RE

MAX_INSTRUCTION_LENGTH = 4000
_SETTING_PREFIX = "operator_instruction:"


class InstructionConflict(ValidationError):
    """The supplied revision is stale."""


class OperatorInstruction(TypedDict):
    project: str
    instruction: str
    revision: int


def _require_project(center: NotificationCenter, project: str) -> None:
    if (not isinstance(project, str) or not 1 <= len(project) <= 128
            or project != project.strip() or "*" in project
            or any(unicodedata.category(char)[0] in "CZ" and char != " " for char in project)):
        raise ValidationError("invalid instruction project")
    if any(scope.get("project") == project for scope in center._tokens.values()):
        return
    row = center._connection.execute(
        "SELECT 1 FROM consumers WHERE project=? "
        "UNION ALL SELECT 1 FROM incidents WHERE project=? LIMIT 1",
        (project, project),
    ).fetchone()
    if row is None:
        raise ValidationError("unknown instruction project")


def _sanitize_instruction(instruction: str) -> str:
    if not isinstance(instruction, str) or len(instruction) > MAX_INSTRUCTION_LENGTH:
        raise ValidationError(f"instruction must be at most {MAX_INSTRUCTION_LENGTH} characters of text")
    text = instruction.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        "\n" if unicodedata.category(char) in {"Zl", "Zp"} else char
        for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    if len(text) > MAX_INSTRUCTION_LENGTH:
        raise ValidationError(f"sanitized instruction exceeds {MAX_INSTRUCTION_LENGTH} characters")
    return text if text.strip() else ""


def _read_saved(center: NotificationCenter, project: str) -> OperatorInstruction:
    raw = center.get_runtime_setting(_SETTING_PREFIX + project)
    saved = json.loads(raw) if raw is not None else {"instruction": "", "revision": 0}
    return {"project": project, "instruction": saved["instruction"], "revision": saved["revision"]}


def instruction_for_delivery(center: NotificationCenter, project: str) -> str:
    """Read the exact project from a server-owned incident row."""
    return _read_saved(center, project)["instruction"]


def read_instruction(center: NotificationCenter, project: str) -> OperatorInstruction:
    """Read the saved guidance and the revision required for the next write."""
    with center._lock:
        _require_project(center, project)
        return _read_saved(center, project)


def write_instruction(
    center: NotificationCenter, project: str, instruction: str, revision: int, actor: str,
) -> OperatorInstruction:
    """Atomically save one revision and audit its metadata in the same transaction."""
    if type(revision) is not int or revision < 0:
        raise ValidationError("invalid instruction revision")
    if (not isinstance(actor, str) or not actor.strip() or len(actor) > 128
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in actor)):
        raise ValidationError("invalid instruction actor")
    text = _sanitize_instruction(instruction)
    with center._lock, center._connection:
        # Mesh already opens a writer transaction on entry. Acquire the
        # equivalent SQLite lock before reading the revision.
        if isinstance(center._connection, sqlite3.Connection):
            center._connection.execute("BEGIN IMMEDIATE")
        _require_project(center, project)
        if _read_saved(center, project)["revision"] != revision:
            raise InstructionConflict("instruction changed; reload before saving")
        next_revision = revision + 1
        saved = {"instruction": text, "revision": next_revision}
        center._connection.execute(
            "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_SETTING_PREFIX + project, json.dumps(saved, ensure_ascii=False), time.time()),
        )
        center._audit(None, "operator_instruction_changed", actor, {
            "project": project, "revision": next_revision, "configured": bool(text),
        })
    return {"project": project, "instruction": text, "revision": next_revision}
