"""Root-only, narrowly scoped configuration store for Notify Center admin UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .core import NotificationCenter, SEVERITIES, ValidationError
from .http_api import telegram_create_forum_topic

PROJECT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
ROUTE_SEVERITIES = ("notice", "important", "critical", "emergency")


def parse_environment(path: Path) -> dict[str, str]:
    """Read a simple EnvironmentFile without interpreting shell syntax."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _atomic_environment_update(path: Path, updates: Mapping[str, str]) -> None:
    """Replace only named EnvironmentFile values, keeping mode and comments."""
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()
    remaining = dict(updates)
    rendered: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                rendered.append(f"{key}={remaining.pop(key)}")
                continue
        rendered.append(line)
    rendered.extend(f"{key}={value}" for key, value in remaining.items())
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("\n".join(rendered).rstrip("\n") + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AdminConfigStore:
    """Manage only producer scopes and Telegram topic routing with rollback."""

    def __init__(
        self,
        primary_env: Path,
        routes_env: Path,
        state_dir: Path,
        restart: Callable[[], None] | None = None,
        apply_service: str | None = None,
    ) -> None:
        self.primary_env = primary_env
        self.routes_env = routes_env
        self.state_dir = state_dir
        self.audit_path = state_dir / "audit.jsonl"
        self.rollback_dir = state_dir / "rollbacks"
        self.restart = restart or self._restart_center
        self.apply_service = apply_service
        self._consumer_center: NotificationCenter | None = None

    @staticmethod
    def _restart_center() -> None:
        subprocess.run(["systemctl", "restart", "notification-center"], check=True, timeout=30)

    def snapshot(self) -> dict[str, Any]:
        """Return display-safe configuration; raw producer tokens never leave here."""
        scopes = self._scopes()
        projects = []
        for token, scope in scopes.items():
            projects.append({
                "project": str(scope["project"]),
                "max_severity": str(scope["max_severity"]),
                "fingerprint": self._fingerprint(token),
            })
        return {
            "projects": sorted(projects, key=lambda item: item["project"]),
            "routes": self._routes(),
            "consumers": self._consumers(),
        }

    def create_consumer(self, project: str, name: str, chat_id: str, topic_id: str, matrix_delay_seconds: str, phone_delay_seconds: str, max_severity: str, actor: str, policy_json: str = "") -> dict[str, Any]:
        """Create an operator-owned consumer policy and reveal its intake token once."""
        self._validate_project(project)
        self._validate_severity(max_severity)
        try:
            telegram_target: dict[str, Any] = {"kind": "telegram", "chat_id": int(chat_id)}
            if topic_id.strip():
                telegram_target["topic_id"] = int(topic_id)
            matrix_delay = float(matrix_delay_seconds) if matrix_delay_seconds.strip() else None
            if matrix_delay is not None and matrix_delay <= 0:
                raise ValueError("matrix delay must be positive")
            delay_seconds = float(phone_delay_seconds)
        except (TypeError, ValueError) as error:
            raise ValidationError("consumer Telegram target and delays must be numeric") from error
        if policy_json.strip():
            try:
                policy = json.loads(policy_json)
            except json.JSONDecodeError as error:
                raise ValidationError("consumer policy JSON is invalid") from error
            if not isinstance(policy, list):
                raise ValidationError("consumer policy JSON must be a list")
        else:
            policy = [telegram_target]
            if matrix_delay is not None:
                policy.append({"kind": "matrix", "delay_seconds": matrix_delay})
            policy.append({"kind": "phone", "delay_seconds": delay_seconds})
        self._attach_custom_telegram_topic(policy, name)
        created = self._consumer_notification_center().create_consumer(
            project=project,
            name=name,
            max_severity=max_severity,
            policy=policy,
        )
        self._audit({"timestamp": int(time.time()), "actor": actor, "action": "consumer_created", "subject": created["id"], "token_fingerprint": created["token_fingerprint"]})
        return created

    def _attach_custom_telegram_topic(self, policy: list[dict[str, Any]], name: str) -> None:
        """Give each Telegram-targeting custom consumer one ordinary forum topic."""
        env = parse_environment(self.primary_env)
        if env.get("TELEGRAM_AUTO_CREATE_TOPICS", "").lower() not in {"1", "true", "yes"}:
            return
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        routes_env = parse_environment(self.routes_env)
        try:
            routes = json.loads(routes_env.get("TELEGRAM_SEVERITY_ROUTES_JSON", "{}"))
        except json.JSONDecodeError as error:
            raise ValidationError("Telegram routes configuration is invalid") from error
        default_chat = str(env.get("TELEGRAM_CHAT_ID") or "")
        if not default_chat:
            default_chat = str(next((route.get("chat_id") for route in routes.values() if isinstance(route, dict) and route.get("chat_id")), ""))
        if not token or not default_chat:
            return
        topic_id: int | None = None
        for step in policy:
            if str(step.get("platform") or step.get("kind") or "") != "telegram":
                continue
            target = step.setdefault("target", {}) if step.get("platform") else step
            if not isinstance(target, dict) or str(target.get("chat_id") or "") != default_chat:
                continue
            if target.get("topic_id") is not None:
                topic_id = int(target["topic_id"])
                break
        if topic_id is None and any(str(step.get("platform") or step.get("kind") or "") == "telegram" for step in policy):
            topic_id = telegram_create_forum_topic(token, default_chat, name)
        if topic_id is not None:
            for step in policy:
                if str(step.get("platform") or step.get("kind") or "") == "telegram":
                    target = step.setdefault("target", {}) if step.get("platform") else step
                    if isinstance(target, dict) and str(target.get("chat_id") or "") == default_chat:
                        target["topic_id"] = topic_id

    def _consumer_notification_center(self) -> NotificationCenter:
        if self._consumer_center is None:
            database = parse_environment(self.primary_env).get("NOTIFY_CENTER_DB", "").strip()
            if not database:
                raise ValidationError("NOTIFY_CENTER_DB must be configured for consumer policies")
            self._consumer_center = NotificationCenter(database, self._scopes())
        return self._consumer_center

    def _consumers(self) -> list[dict[str, Any]]:
        center = self._consumer_notification_center()
        rows = center._connection.execute("SELECT id FROM consumers ORDER BY created_at DESC").fetchall()
        return [consumer for row in rows if (consumer := center.get_consumer(str(row["id"]))) is not None]

    def create_project(self, project: str, max_severity: str, actor: str) -> str:
        self._validate_project(project)
        self._validate_severity(max_severity)
        scopes = self._scopes()
        if any(scope["project"] == project for scope in scopes.values()):
            raise ValidationError("project already has a producer token")
        token = secrets.token_urlsafe(32)
        scopes[token] = {"project": project, "max_severity": max_severity}
        self._apply_primary(scopes, actor, "project_created", project, token)
        return token

    def set_project_severity(self, project: str, max_severity: str, actor: str) -> None:
        self._validate_project(project)
        self._validate_severity(max_severity)
        scopes = self._scopes()
        token = self._token_for_project(scopes, project)
        scopes[token]["max_severity"] = max_severity
        self._apply_primary(scopes, actor, "project_severity_changed", project, token)

    def revoke_project(self, project: str, actor: str) -> None:
        self._validate_project(project)
        scopes = self._scopes()
        token = self._token_for_project(scopes, project)
        del scopes[token]
        self._apply_primary(scopes, actor, "project_revoked", project, token)

    def set_routes(self, routes: Mapping[str, Mapping[str, Any]], actor: str) -> None:
        normalized: dict[str, dict[str, Any]] = {}
        for severity, route in routes.items():
            if severity not in ROUTE_SEVERITIES:
                raise ValidationError("unsupported route severity")
            chat_id = str(route.get("chat_id") or "").strip()
            topic = route.get("message_thread_id")
            if not chat_id:
                continue
            if not re.fullmatch(r"-?\d+", chat_id):
                raise ValidationError("chat_id must be numeric")
            try:
                topic_id = int(topic)
            except (TypeError, ValueError) as error:
                raise ValidationError("message_thread_id must be a positive integer") from error
            if topic_id <= 0:
                raise ValidationError("message_thread_id must be a positive integer")
            normalized[severity] = {"chat_id": chat_id, "message_thread_id": topic_id}
        self._apply_routes(normalized, actor)

    def _scopes(self) -> dict[str, dict[str, str]]:
        raw = parse_environment(self.primary_env).get("NOTIFY_CENTER_TOKENS_JSON", "{}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValidationError("producer scope configuration is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("producer scope configuration is invalid")
        normalized: dict[str, dict[str, str]] = {}
        for token, scope in value.items():
            if not isinstance(token, str) or not isinstance(scope, dict):
                raise ValidationError("producer scope configuration is invalid")
            project, severity = str(scope.get("project") or ""), str(scope.get("max_severity") or "notice")
            self._validate_project(project)
            self._validate_severity(severity)
            normalized[token] = {"project": project, "max_severity": severity}
        return normalized

    def _routes(self) -> dict[str, dict[str, Any]]:
        raw = parse_environment(self.routes_env).get("TELEGRAM_SEVERITY_ROUTES_JSON", "{}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValidationError("route configuration is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("route configuration is invalid")
        return {str(key): dict(route) for key, route in value.items() if isinstance(route, dict)}

    def _apply_primary(self, scopes: Mapping[str, Mapping[str, str]], actor: str, action: str, project: str, token: str) -> None:
        encoded = json.dumps(scopes, sort_keys=True, separators=(",", ":"))
        self._apply(self.primary_env, {"NOTIFY_CENTER_TOKENS_JSON": encoded}, actor, action, project, token)

    def _apply_routes(self, routes: Mapping[str, Mapping[str, Any]], actor: str) -> None:
        encoded = json.dumps(routes, sort_keys=True, separators=(",", ":"))
        self._apply(self.routes_env, {"TELEGRAM_SEVERITY_ROUTES_JSON": encoded}, actor, "routes_changed", "routes", None)

    def _apply(self, path: Path, updates: Mapping[str, str], actor: str, action: str, subject: str, token: str | None) -> None:
        if self.apply_service:
            self._submit_job(path, updates, actor, action, subject, token)
            return
        self._apply_direct(path, updates, actor, action, subject, token)

    def _apply_direct(self, path: Path, updates: Mapping[str, str], actor: str, action: str, subject: str, token: str | None) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.rollback_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        rollback = self.rollback_dir / f"{int(time.time())}-{secrets.token_hex(4)}"
        rollback.mkdir(mode=0o700)
        backup = rollback / path.name
        shutil.copy2(path, backup)
        try:
            _atomic_environment_update(path, updates)
            self.restart()
        except Exception:
            shutil.copy2(backup, path)
            self.restart()
            raise
        self._audit({"timestamp": int(time.time()), "actor": actor, "action": action, "subject": subject, "token_fingerprint": self._fingerprint(token) if token else None, "rollback": str(rollback)})

    def _submit_job(self, path: Path, updates: Mapping[str, str], actor: str, action: str, subject: str, token: str | None) -> None:
        """Hand a root-only configuration job to the narrowly scoped helper."""
        target = "primary" if path == self.primary_env else "routes" if path == self.routes_env else None
        if target is None:
            raise ValidationError("unsupported configuration target")
        pending = self.state_dir / "pending"
        pending.mkdir(mode=0o700, parents=True, exist_ok=True)
        job_id = secrets.token_hex(16)
        job = pending / f"{job_id}.json"
        payload = {"target": target, "updates": dict(updates), "actor": actor, "action": action, "subject": subject, "token": token}
        handle, temporary = tempfile.mkstemp(prefix=f".{job_id}.", dir=str(pending))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(payload, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, job)
            subprocess.run(["systemctl", "start", f"{self.apply_service}@{job_id}.service"], check=True, timeout=45)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            if job.exists():
                job.unlink()

    def _audit(self, record: Mapping[str, Any]) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(self.audit_path, 0o600)

    @staticmethod
    def _token_for_project(scopes: Mapping[str, Mapping[str, str]], project: str) -> str:
        found = [token for token, scope in scopes.items() if scope["project"] == project]
        if len(found) != 1:
            raise ValidationError("project was not found")
        return found[0]

    @staticmethod
    def _fingerprint(token: str | None) -> str:
        return "sha256:" + hashlib.sha256(str(token or "").encode()).hexdigest()[:12]

    @staticmethod
    def _validate_project(project: str) -> None:
        if not PROJECT_PATTERN.fullmatch(project):
            raise ValidationError("project must use letters, digits, dot, underscore, or hyphen")

    @staticmethod
    def _validate_severity(severity: str) -> None:
        if severity not in SEVERITIES:
            raise ValidationError("unsupported max severity")


def apply_pending_job(job_id: str, primary_env: Path, routes_env: Path, state_dir: Path) -> None:
    """Apply one BFF-created job; used only by the root systemd helper unit."""
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValidationError("invalid admin job")
    job = state_dir / "pending" / f"{job_id}.json"
    with job.open(encoding="utf-8") as file:
        payload = json.load(file)
    target = payload.get("target")
    path = primary_env if target == "primary" else routes_env if target == "routes" else None
    if path is None or not isinstance(payload.get("updates"), dict):
        raise ValidationError("invalid admin job")
    store = AdminConfigStore(primary_env, routes_env, state_dir)
    store._apply_direct(path, {str(key): str(value) for key, value in payload["updates"].items()}, str(payload.get("actor") or "sso:operator"), str(payload.get("action") or "configuration_changed"), str(payload.get("subject") or "configuration"), str(payload["token"]) if payload.get("token") else None)
