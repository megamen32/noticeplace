"""SQLite-backed incident state machine for the notification-center MVP."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


SEVERITIES = ("debug", "info", "notice", "important", "critical", "emergency")
OPEN_STATES = ("open", "acknowledged", "snoozed")
DELIVERABLE_STATES = ("open", "snoozed")


class NotificationCenterError(Exception):
    """Base error raised by the notification-center domain layer."""


class AuthorizationError(NotificationCenterError):
    """Raised when an API token is absent, invalid, or lacks a required scope."""


class ValidationError(NotificationCenterError):
    """Raised when an event or state-transition request violates the contract."""


class IdempotencyConflict(ValidationError):
    """Raised when a producer reuses a request key for different event content."""


class NotificationCenter:
    """Persist events, incidents, deliveries, and audit history in SQLite.

    Args:
        database_path: SQLite database location. Parent directories are created.
        tokens: Mapping from bearer token to allowed project and maximum severity.

    The class is safe for the HTTP server's worker threads. It intentionally
    implements at-least-once delivery: a claimed delivery may be retried after
    failure, while stable delivery keys prevent routine duplicate scheduling.
    """

    def __init__(self, database_path: Path | str, tokens: Mapping[str, Mapping[str, Any]]) -> None:
        """Open and initialize durable state; raises sqlite errors on storage failures."""
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._tokens = {str(key): dict(value) for key, value in tokens.items()}
        self._dispatcher_heartbeat = time.time()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        """Create the schema used for idempotency, incident state, jobs, and audit."""
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    idempotency_key TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    collapse_key TEXT,
                    state TEXT NOT NULL,
                    occurrences INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    acknowledged_at REAL,
                    resolved_at REAL,
                    snoozed_until REAL
                );
                CREATE TABLE IF NOT EXISTS consumers (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    token_fingerprint TEXT NOT NULL,
                    max_severity TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumer_policy_stages (
                    consumer_id TEXT NOT NULL REFERENCES consumers(id),
                    stage INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    target_json TEXT NOT NULL,
                    step_id TEXT,
                    platform TEXT,
                    action TEXT,
                    previous_step_id TEXT,
                    retry_interval_seconds REAL,
                    max_repeats INTEGER,
                    PRIMARY KEY (consumer_id, stage)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    channel TEXT NOT NULL,
                    delivery_key TEXT NOT NULL UNIQUE,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    claimed_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    type TEXT NOT NULL,
                    actor TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resolution_events (
                    idempotency_key TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    incident_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    update_id INTEGER PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                """
            )
            incident_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(incidents)")}
            if "consumer_id" not in incident_columns:
                self._connection.execute("ALTER TABLE incidents ADD COLUMN consumer_id TEXT REFERENCES consumers(id)")
            delivery_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(deliveries)")}
            if "target_json" not in delivery_columns:
                self._connection.execute("ALTER TABLE deliveries ADD COLUMN target_json TEXT NOT NULL DEFAULT '{}'")
            for column, definition in (
                ("policy_step_id", "TEXT"),
                ("repeat_number", "INTEGER"),
            ):
                if column not in delivery_columns:
                    self._connection.execute(f"ALTER TABLE deliveries ADD COLUMN {column} {definition}")
            policy_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(consumer_policy_stages)")}
            for column, definition in (
                ("step_id", "TEXT"),
                ("platform", "TEXT"),
                ("action", "TEXT"),
                ("previous_step_id", "TEXT"),
                ("retry_interval_seconds", "REAL"),
                ("max_repeats", "INTEGER"),
            ):
                if column not in policy_columns:
                    self._connection.execute(f"ALTER TABLE consumer_policy_stages ADD COLUMN {column} {definition}")
            self._connection.execute("DROP INDEX IF EXISTS incidents_open_dedup")
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS incidents_open_dedup_scope "
                "ON incidents(project, recipient, IFNULL(consumer_id, ''), dedup_key) WHERE state != 'resolved'"
            )

    def _scope(self, token: str, project: str) -> Mapping[str, Any]:
        """Validate a token's project boundary without requiring an event severity."""
        scope = self._tokens.get(token)
        if scope is None:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            row = self._connection.execute(
                "SELECT id, project, max_severity FROM consumers WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                raise AuthorizationError("invalid bearer token")
            scope = {"project": row["project"], "max_severity": row["max_severity"], "consumer_id": row["id"]}
        if scope.get("project") not in ("*", project):
            raise AuthorizationError("token is not allowed for this project")
        return scope

    def _token(self, token: str, event: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate token project and severity boundaries; raises AuthorizationError."""
        scope = self._scope(token, str(event.get("project") or ""))
        self._require_severity(scope, str(event.get("severity") or ""))
        return scope

    @staticmethod
    def _require_severity(scope: Mapping[str, Any], severity: str) -> None:
        """Reject a scope that is not allowed to create or resolve this severity."""
        maximum = scope.get("max_severity", "notice")
        if severity not in SEVERITIES or maximum not in SEVERITIES or SEVERITIES.index(severity) > SEVERITIES.index(maximum):
            raise AuthorizationError("token is not allowed for this severity")

    def authorize_incident(self, token: str, incident_id: str) -> None:
        """Authorize a token to read or mutate one incident; raises AuthorizationError."""
        incident = self.get_incident(incident_id)
        if incident is None:
            if token not in self._tokens:
                raise AuthorizationError("invalid bearer token")
            return
        scope = self._scope(token, str(incident["project"]))
        self._require_severity(scope, str(incident["severity"]))

    @staticmethod
    def _validate_event(event: Mapping[str, Any]) -> None:
        """Validate the intentionally small v1 event contract; raises ValidationError."""
        required = ("project", "recipient", "kind", "severity", "title", "dedup_key")
        missing = [key for key in required if not str(event.get(key) or "").strip()]
        if missing:
            raise ValidationError(f"missing required event fields: {', '.join(missing)}")
        if event.get("schema", "notify.event.v1") != "notify.event.v1":
            raise ValidationError("unsupported event schema")
        if str(event["severity"]) not in SEVERITIES:
            raise ValidationError("unsupported severity")
        if str(event["kind"]) not in ("incident", "notification", "audit", "log"):
            raise ValidationError("unsupported kind")
        forbidden = sorted(
            set(event).intersection(
                {"target", "platform", "phone_number", "command", "delay_seconds", "retry", "stage", "url", "harness", "cwd", "prompt", "tool", "mcp", "credential", "credentials", "token", "secret", "callback_url"}
            )
        )
        if forbidden:
            raise ValidationError(f"event contains forbidden delivery authority fields: {', '.join(forbidden)}")
        agent_job = event.get("agent_job")
        if agent_job is not None:
            if not isinstance(agent_job, str) or not agent_job or len(agent_job) > 128 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in agent_job):
                raise ValidationError("agent_job must be a safe allowlisted identifier")

    @staticmethod
    def _validate_resolution(event: Mapping[str, Any]) -> None:
        """Validate a minimal source-driven resolution event."""
        required = ("project", "recipient", "dedup_key")
        missing = [key for key in required if not str(event.get(key) or "").strip()]
        if missing:
            raise ValidationError(f"missing required resolution fields: {', '.join(missing)}")
        if event.get("schema", "notify.event.v1") != "notify.event.v1" or event.get("action") != "resolve":
            raise ValidationError("unsupported resolution event")

    def _audit(self, incident_id: str | None, event_type: str, actor: str | None, payload: Mapping[str, Any]) -> None:
        """Record an immutable state transition for later human and machine audit."""
        self._connection.execute(
            "INSERT INTO audit_events(id, incident_id, type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, incident_id, event_type, actor, json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()),
        )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convert a SQLite row to a JSON-safe dictionary, preserving nulls."""
        return dict(row) if row is not None else None

    @staticmethod
    def _validate_consumer_policy(policy: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Validate generic linked steps, while accepting the legacy policy shape."""
        if not isinstance(policy, list):
            raise ValidationError("consumer policy must be a list")
        if any(not isinstance(stage, Mapping) for stage in policy):
            raise ValidationError("consumer policy stages must be objects")
        generic = any("platform" in stage or "action" in stage or "previous_step_id" in stage for stage in policy)
        if generic:
            allowed = {
                ("telegram", "message"), ("telegram", "call"),
                ("matrix", "message"), ("matrix", "call"),
                ("whatsapp", "message"), ("whatsapp", "call"),
                ("phone", "call"),
            }
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            for index, raw_stage in enumerate(policy, start=1):
                if not bool(raw_stage.get("enabled", True)):
                    raise ValidationError("generic consumer steps cannot be disabled")
                step_id = str(raw_stage.get("id") or raw_stage.get("step_id") or f"step-{index}")
                if not step_id or step_id in seen:
                    raise ValidationError("generic consumer step ids must be unique")
                seen.add(step_id)
                platform = str(raw_stage.get("platform") or "")
                action = str(raw_stage.get("action") or "")
                if (platform, action) not in allowed:
                    raise ValidationError("unsupported consumer platform/action")
                target = raw_stage.get("target", {})
                if not isinstance(target, Mapping):
                    raise ValidationError("consumer step target must be an object")
                interval = raw_stage.get("retry_interval_seconds")
                if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
                    raise ValidationError("consumer step requires positive retry_interval_seconds")
                repeats = raw_stage.get("max_repeats")
                if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
                    raise ValidationError("consumer step requires positive max_repeats")
                previous = raw_stage.get("previous_step_id")
                if previous is not None and not isinstance(previous, str):
                    raise ValidationError("previous_step_id must be a string")
                normalized.append({
                    "stage": index, "kind": f"{platform}.{action}", "enabled": True,
                    "target": dict(target), "step_id": step_id, "platform": platform,
                    "action": action, "previous_step_id": previous,
                    "retry_interval_seconds": float(interval), "max_repeats": repeats,
                    "generic": True,
                })
            roots = [step for step in normalized if step["previous_step_id"] is None]
            if len(roots) != 1:
                raise ValidationError("generic consumer policy requires exactly one root step")
            ids = {step["step_id"] for step in normalized}
            for step in normalized:
                previous = step["previous_step_id"]
                if previous is not None and previous not in ids:
                    raise ValidationError("previous_step_id must reference a policy step")
            return normalized

        normalized = []
        active = [stage for stage in policy if bool(stage.get("enabled", True))]
        active_kinds = [str(stage.get("kind") or "") for stage in active]
        if active_kinds not in (["telegram", "phone"], ["telegram", "matrix", "phone"]):
            raise ValidationError("consumer policy requires telegram, optional matrix, then phone stages")
        for index, raw_stage in enumerate(policy, start=1):
            kind = str(raw_stage.get("kind") or "")
            enabled = bool(raw_stage.get("enabled", True))
            if kind not in ("telegram", "phone", "matrix", "whatsapp"):
                raise ValidationError("unsupported consumer target kind")
            if kind == "whatsapp":
                if enabled:
                    raise ValidationError("whatsapp is reserved and must remain disabled")
                normalized.append({"stage": index, "kind": kind, "enabled": False, "target": {}, "generic": False})
                continue
            if kind == "matrix":
                if not enabled:
                    normalized.append({"stage": index, "kind": kind, "enabled": False, "target": {}, "generic": False})
                    continue
                delay = raw_stage.get("delay_seconds")
                if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay <= 0:
                    raise ValidationError("matrix stage requires positive delay_seconds")
                normalized.append({"stage": index, "kind": kind, "enabled": enabled, "target": {"delay_seconds": float(delay)}, "generic": False})
                continue
            if kind == "telegram":
                chat_id = raw_stage.get("chat_id")
                if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                    raise ValidationError("telegram stage requires integer chat_id")
                topic_id = raw_stage.get("topic_id")
                if topic_id is not None and (isinstance(topic_id, bool) or not isinstance(topic_id, int)):
                    raise ValidationError("telegram topic_id must be an integer")
                target = {"chat_id": chat_id}
                if topic_id is not None:
                    target["topic_id"] = topic_id
            else:
                delay = raw_stage.get("delay_seconds")
                if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay <= 0:
                    raise ValidationError("phone stage requires positive delay_seconds")
                target = {"delay_seconds": float(delay)}
            normalized.append({"stage": index, "kind": kind, "enabled": enabled, "target": target, "generic": False})
        return normalized

    def create_consumer(
        self, project: str, name: str, policy: list[Mapping[str, Any]], max_severity: str = "critical"
    ) -> dict[str, Any]:
        """Persist an operator-owned consumer and reveal its intake token once."""
        project = project.strip()
        name = name.strip()
        if not project or not name:
            raise ValidationError("consumer project and name are required")
        if max_severity not in SEVERITIES:
            raise ValidationError("unsupported consumer maximum severity")
        normalized_policy = self._validate_consumer_policy(policy)
        consumer_id = f"consumer_{uuid.uuid4().hex}"
        intake_token = f"nct_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        token_hash = hashlib.sha256(intake_token.encode("utf-8")).hexdigest()
        fingerprint = token_hash[:12]
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO consumers(id, project, name, token_hash, token_fingerprint, max_severity, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (consumer_id, project, name, token_hash, fingerprint, max_severity, now, now),
            )
            self._connection.executemany(
                "INSERT INTO consumer_policy_stages(consumer_id, stage, kind, enabled, target_json, step_id, platform, action, previous_step_id, retry_interval_seconds, max_repeats) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (consumer_id, stage["stage"], stage["kind"], int(stage["enabled"]), json.dumps(stage["target"], sort_keys=True), stage.get("step_id"), stage.get("platform"), stage.get("action"), stage.get("previous_step_id"), stage.get("retry_interval_seconds"), stage.get("max_repeats"))
                    for stage in normalized_policy
                ],
            )
            self._audit(None, "consumer_created", "operator", {"consumer_id": consumer_id, "project": project})
        return {"id": consumer_id, "token_fingerprint": fingerprint, "intake_token": intake_token}

    def get_consumer(self, consumer_id: str) -> dict[str, Any] | None:
        """Return safe consumer metadata and its operator-owned delivery policy."""
        with self._lock:
            consumer = self._connection.execute(
                "SELECT id, project, name, token_fingerprint, max_severity, created_at, updated_at FROM consumers WHERE id = ?",
                (consumer_id,),
            ).fetchone()
            if consumer is None:
                return None
            result = dict(consumer)
            stages = self._connection.execute(
                "SELECT stage, kind, enabled, target_json, step_id, platform, action, previous_step_id, retry_interval_seconds, max_repeats FROM consumer_policy_stages WHERE consumer_id = ? ORDER BY stage",
                (consumer_id,),
            ).fetchall()
            result["policy"] = []
            for row in stages:
                item = {"stage": row["stage"], "kind": row["kind"], "enabled": bool(row["enabled"]), **json.loads(row["target_json"])}
                if row["step_id"] is not None:
                    item.update({"id": row["step_id"], "step_id": row["step_id"], "platform": row["platform"], "action": row["action"], "retry_interval_seconds": row["retry_interval_seconds"], "max_repeats": row["max_repeats"]})
                    if row["previous_step_id"] is not None:
                        item["previous_step_id"] = row["previous_step_id"]
                result["policy"].append(item)
            return result

    def create_event(self, token: str, idempotency_key: str, event: Mapping[str, Any]) -> dict[str, Any]:
        """Accept one event and schedule its initial Telegram delivery.

        Args:
            token: Project bearer token without the HTTP ``Bearer`` prefix.
            idempotency_key: Stable producer key for safe HTTP retries.
            event: ``notify.event.v1`` object.

        Returns:
            Event and incident identity plus the scheduled initial delivery.
        Raises:
            AuthorizationError: The producer token is not permitted.
            ValidationError: The input is incomplete or inconsistent.
        """
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        self._validate_event(event)
        scope = self._token(token, event)
        agent_job = str(event.get("agent_job") or "")
        if agent_job:
            allowed_jobs = scope.get("agent_jobs", [])
            if not isinstance(allowed_jobs, (list, tuple)) or agent_job not in {str(value) for value in allowed_jobs}:
                raise AuthorizationError("token is not allowed to start this agent job")
        now = time.time()
        with self._lock, self._connection:
            payload_json = json.dumps(dict(event), ensure_ascii=False, sort_keys=True)
            consumer_id = str(scope.get("consumer_id") or "") or None
            previous = self._connection.execute("SELECT event_id, incident_id, payload_json FROM events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different event content")
                previous_incident = self.get_incident(str(previous["incident_id"]))
                initial_channel = f"telegram.consumer:{previous_incident['consumer_id']}" if previous_incident and previous_incident.get("consumer_id") else "telegram.main"
                initial = self._connection.execute("SELECT id FROM deliveries WHERE delivery_key = ?", (f"{previous['incident_id']}:{initial_channel}:initial",)).fetchone()
                previous_event = json.loads(previous["payload_json"])
                previous_job = str(previous_event.get("agent_job") or "") if isinstance(previous_event, dict) else ""
                agent_delivery = self._connection.execute(
                    "SELECT id FROM deliveries WHERE delivery_key = ?",
                    (f"{previous['incident_id']}:gptadmin.agent:{previous_job}:event:{previous['event_id']}",),
                ).fetchone() if previous_job else None
                return {"event_id": previous["event_id"], "incident_id": previous["incident_id"], "state": self.get_incident(previous["incident_id"])["state"], "deduplicated": False, "idempotent": True, "initial_delivery_id": initial["id"] if initial else None, "agent_job_delivery_id": agent_delivery["id"] if agent_delivery else None}
            project, recipient, dedup_key = str(event["project"]), str(event["recipient"]), str(event["dedup_key"])
            existing = self._connection.execute("SELECT id FROM incidents WHERE project = ? AND recipient = ? AND IFNULL(consumer_id, '') = IFNULL(?, '') AND dedup_key = ? AND state != 'resolved'", (project, recipient, consumer_id, dedup_key)).fetchone()
            deduplicated = existing is not None
            if existing is None:
                incident_id = f"inc_{uuid.uuid4().hex}"
                self._connection.execute(
                    "INSERT INTO incidents(id, project, recipient, kind, severity, title, body, dedup_key, collapse_key, consumer_id, state, occurrences, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 1, ?, ?)",
                    (incident_id, project, recipient, str(event["kind"]), str(event["severity"]), str(event["title"]), str(event.get("body") or ""), dedup_key, event.get("collapse_key"), consumer_id, now, now),
                )
                self._audit(incident_id, "incident_created", "producer", {"dedup_key": dedup_key})
            else:
                incident_id = str(existing["id"])
                self._connection.execute("UPDATE incidents SET occurrences = occurrences + 1, title = ?, body = ?, updated_at = ? WHERE id = ?", (str(event["title"]), str(event.get("body") or ""), now, incident_id))
                self._audit(incident_id, "incident_repeated", "producer", {"dedup_key": dedup_key})
            event_id = f"evt_{uuid.uuid4().hex}"
            self._connection.execute("INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", (idempotency_key, event_id, incident_id, payload_json, now))
            if consumer_id:
                delivery_id = self._schedule_consumer_policy(incident_id, consumer_id, now)
            else:
                delivery_id = self._schedule_delivery(incident_id, "telegram.main", "initial", now)
            agent_delivery_id = self._schedule_delivery(incident_id, f"gptadmin.agent:{agent_job}", f"event:{event_id}", now) if agent_job else None
            return {"event_id": event_id, "incident_id": incident_id, "state": self.get_incident(incident_id)["state"], "deduplicated": deduplicated, "idempotent": False, "initial_delivery_id": delivery_id, "agent_job_delivery_id": agent_delivery_id}

    def resolve_event(self, token: str, idempotency_key: str, event: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve an active incident by stable producer identity, idempotently."""
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        self._validate_resolution(event)
        project, recipient, dedup_key = (str(event["project"]), str(event["recipient"]), str(event["dedup_key"]))
        self._scope(token, project)
        payload_json = json.dumps(dict(event), ensure_ascii=False, sort_keys=True)
        now = time.time()
        with self._lock, self._connection:
            previous = self._connection.execute(
                "SELECT event_id, incident_id, payload_json FROM resolution_events WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different event content")
                incident = self.get_incident(str(previous["incident_id"])) if previous["incident_id"] else None
                return {
                    "event_id": previous["event_id"],
                    "incident_id": previous["incident_id"],
                    "resolved": bool(previous["incident_id"] is not None),
                    "state": incident["state"] if incident else "not_found",
                    "idempotent": True,
                }
            row = self._connection.execute(
                "SELECT id, severity FROM incidents WHERE project = ? AND recipient = ? AND dedup_key = ? AND state != 'resolved'",
                (project, recipient, dedup_key),
            ).fetchone()
            event_id = f"evt_{uuid.uuid4().hex}"
            incident_id = str(row["id"]) if row is not None else None
            if incident_id is not None:
                self._require_severity(self._scope(token, project), str(row["severity"]))
                self._transition(incident_id, "resolved", "producer")
            self._connection.execute(
                "INSERT INTO resolution_events(idempotency_key, event_id, project, recipient, dedup_key, payload_json, incident_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (idempotency_key, event_id, project, recipient, dedup_key, payload_json, incident_id, now),
            )
            return {
                "event_id": event_id,
                "incident_id": incident_id,
                "resolved": incident_id is not None,
                "state": "resolved" if incident_id is not None else "not_found",
                "idempotent": False,
            }

    def _schedule_delivery(self, incident_id: str, channel: str, step: str, due_epoch: float, target: Mapping[str, Any] | None = None, policy_step_id: str | None = None, repeat_number: int | None = None) -> str:
        """Create a stable scheduled delivery and return its identity."""
        delivery_key = f"{incident_id}:{channel}:{step}"
        row = self._connection.execute("SELECT id FROM deliveries WHERE delivery_key = ?", (delivery_key,)).fetchone()
        if row is not None:
            return str(row["id"])
        delivery_id = f"dlv_{uuid.uuid4().hex}"
        now = time.time()
        self._connection.execute("INSERT INTO deliveries(id, incident_id, channel, delivery_key, due_at, status, attempt, target_json, policy_step_id, repeat_number, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)", (delivery_id, incident_id, channel, delivery_key, due_epoch, json.dumps(dict(target or {}), sort_keys=True), policy_step_id, repeat_number, now, now))
        self._audit(incident_id, "delivery_scheduled", "policy", {"delivery_id": delivery_id, "channel": channel, "step": step, "due_at": due_epoch})
        return delivery_id

    def _schedule_consumer_policy(self, incident_id: str, consumer_id: str, now: float) -> str:
        """Materialize only the root generic step, or preserve legacy scheduling."""
        generic = self._connection.execute(
            "SELECT step_id, platform, action, target_json, retry_interval_seconds, max_repeats FROM consumer_policy_stages WHERE consumer_id = ? AND enabled = 1 AND step_id IS NOT NULL AND previous_step_id IS NULL",
            (consumer_id,),
        ).fetchone()
        if generic is not None:
            return self._schedule_delivery(
                incident_id, f"{generic['platform']}.{generic['action']}", f"step:{generic['step_id']}:repeat:1", now,
                json.loads(generic["target_json"]), str(generic["step_id"]), 1,
            )
        stages = self._connection.execute(
            "SELECT stage, kind, target_json FROM consumer_policy_stages WHERE consumer_id = ? AND enabled = 1 ORDER BY stage",
            (consumer_id,),
        ).fetchall()
        initial_delivery_id: str | None = None
        for stage in stages:
            target = json.loads(stage["target_json"])
            if stage["kind"] == "telegram":
                channel, step, due_at = f"telegram.consumer:{consumer_id}", "initial", now
            elif stage["kind"] == "matrix":
                channel, step, due_at = "matrix.call", "escalation", now + float(target["delay_seconds"])
                target = {}
            elif stage["kind"] == "phone":
                channel, step, due_at = "android.phone.call", "escalation", now + float(target["delay_seconds"])
            else:
                continue
            delivery_id = self._schedule_delivery(incident_id, channel, step, due_at, target)
            if initial_delivery_id is None:
                initial_delivery_id = delivery_id
            if initial_delivery_id is None:
                raise ValidationError("consumer policy has no enabled delivery stages")
        return initial_delivery_id

    def schedule_escalation(self, incident_id: str, channel: str, due_epoch: float) -> str:
        """Schedule one named escalation while the incident remains active."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            if incident["state"] not in DELIVERABLE_STATES:
                raise ValidationError(f"cannot escalate an {incident['state']} incident")
            return self._schedule_delivery(incident_id, channel, "escalation", due_epoch)

    def schedule_escalation_if_active(self, incident_id: str, channel: str, due_epoch: float) -> str | None:
        """Schedule an escalation unless an ACK or resolve already closed it."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None or incident["state"] not in DELIVERABLE_STATES:
                return None
            return self._schedule_delivery(incident_id, channel, "escalation", due_epoch)

    def schedule_telegram_repeat_if_active(self, incident_id: str, sequence: int, due_epoch: float) -> str | None:
        """Persist one uniquely keyed critical repeat unless the incident is closed."""
        if sequence < 1:
            raise ValidationError("repeat sequence must be positive")
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None or incident["state"] not in DELIVERABLE_STATES:
                return None
            return self._schedule_delivery(incident_id, "telegram.main", f"repeat:{sequence}", due_epoch)

    def claim_due_deliveries(self, now_epoch: float | None = None, limit: int = 20, lease_seconds: float = 60) -> list[dict[str, Any]]:
        """Claim due work and reclaim an expired worker lease after a crash.

        The bounded lease intentionally permits a rare duplicate after a worker
        dies mid-send. That is safer than stranding a critical notification.
        """
        now = time.time() if now_epoch is None else now_epoch
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT d.* FROM deliveries d JOIN incidents i ON i.id = d.incident_id WHERE ((d.status = 'queued' AND d.due_at <= ?) OR (d.status = 'claimed' AND d.claimed_at <= ?)) AND i.state IN (?, ?) AND (i.snoozed_until IS NULL OR i.snoozed_until <= ?) ORDER BY d.due_at LIMIT ?",
                (now, now - max(1, lease_seconds), *DELIVERABLE_STATES, now, limit),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                cursor = self._connection.execute("UPDATE deliveries SET status = 'claimed', claimed_at = ?, attempt = attempt + 1, updated_at = ? WHERE id = ? AND (status = 'queued' OR (status = 'claimed' AND claimed_at <= ?))", (now, now, row["id"], now - max(1, lease_seconds)))
                if cursor.rowcount != 1:
                    continue
                claimed = self._connection.execute("SELECT * FROM deliveries WHERE id = ?", (row["id"],)).fetchone()
                result.append(dict(claimed))
            return result

    def complete_delivery(self, delivery_id: str, outcome: str, error: str | None = None, retry_after_seconds: float = 30) -> None:
        """Mark one claim sent, cancelled, or safely queued for a future retry."""
        if outcome not in ("sent", "failed", "cancelled", "retry"):
            raise ValidationError("delivery outcome must be sent, failed, cancelled, or retry")
        now = time.time()
        safe_error = " ".join((error or "").replace("\x00", "").splitlines())[-1000:] or None
        with self._lock, self._connection:
            row = self._connection.execute("SELECT d.incident_id, d.policy_step_id, d.repeat_number, i.state, i.consumer_id FROM deliveries d JOIN incidents i ON i.id = d.incident_id WHERE d.id = ?", (delivery_id,)).fetchone()
            if row is None:
                raise ValidationError("delivery not found")
            if outcome in ("sent", "failed", "retry") and str(row["state"]) not in DELIVERABLE_STATES:
                outcome = "cancelled"
                safe_error = safe_error or "incident is no longer active"
            if outcome == "retry":
                self._connection.execute("UPDATE deliveries SET status = 'queued', due_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (now + max(1, retry_after_seconds), safe_error, now, delivery_id))
            else:
                self._connection.execute("UPDATE deliveries SET status = ?, last_error = ?, updated_at = ? WHERE id = ?", (outcome, safe_error, now, delivery_id))
            if outcome == "sent" and row["policy_step_id"] is not None and str(row["state"]) in DELIVERABLE_STATES:
                step = self._connection.execute(
                    "SELECT platform, action, target_json, retry_interval_seconds, max_repeats FROM consumer_policy_stages WHERE consumer_id = ? AND step_id = ? AND enabled = 1",
                    (row["consumer_id"], row["policy_step_id"]),
                ).fetchone()
                repeat_number = int(row["repeat_number"] or 1)
                if step is not None and repeat_number < int(step["max_repeats"]):
                    self._schedule_delivery(
                        str(row["incident_id"]), f"{step['platform']}.{step['action']}",
                        f"step:{row['policy_step_id']}:repeat:{repeat_number + 1}",
                        now + float(step["retry_interval_seconds"]), json.loads(step["target_json"]),
                        str(row["policy_step_id"]), repeat_number + 1,
                    )
                elif step is not None:
                    successor = self._connection.execute(
                        "SELECT step_id, platform, action, target_json FROM consumer_policy_stages WHERE consumer_id = ? AND previous_step_id = ? AND enabled = 1",
                        (row["consumer_id"], row["policy_step_id"]),
                    ).fetchone()
                    if successor is not None:
                        self._schedule_delivery(
                            str(row["incident_id"]), f"{successor['platform']}.{successor['action']}",
                            f"step:{successor['step_id']}:repeat:1", now, json.loads(successor["target_json"]),
                            str(successor["step_id"]), 1,
                        )
            self._audit(str(row["incident_id"]), f"delivery_{outcome}", "worker", {"delivery_id": delivery_id, "error": safe_error})

    def _transition(self, incident_id: str, state: str, actor: str, snoozed_until: float | None = None) -> dict[str, Any]:
        """Apply an incident transition and cancel future alerts when appropriate."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            now = time.time()
            if state == "acknowledged":
                self._connection.execute("UPDATE incidents SET state = ?, acknowledged_at = ?, snoozed_until = NULL, updated_at = ? WHERE id = ?", (state, now, now, incident_id))
                self._connection.execute("UPDATE deliveries SET status = 'cancelled', updated_at = ? WHERE incident_id = ? AND status IN ('queued', 'claimed')", (now, incident_id))
            elif state == "resolved":
                self._connection.execute("UPDATE incidents SET state = ?, resolved_at = ?, snoozed_until = NULL, updated_at = ? WHERE id = ?", (state, now, now, incident_id))
                self._connection.execute("UPDATE deliveries SET status = 'cancelled', updated_at = ? WHERE incident_id = ? AND status IN ('queued', 'claimed')", (now, incident_id))
            else:
                self._connection.execute("UPDATE incidents SET state = ?, snoozed_until = ?, updated_at = ? WHERE id = ?", (state, snoozed_until, now, incident_id))
            self._audit(incident_id, f"incident_{state}", actor, {"snoozed_until": snoozed_until})
            return self.get_incident(incident_id) or {}

    def acknowledge(self, incident_id: str, actor: str) -> dict[str, Any]:
        """Explicitly ACK an active incident and cancel future escalation."""
        existing = self.get_incident(incident_id)
        if existing is not None and existing["state"] == "acknowledged":
            return existing
        result = self.acknowledge_if_active(incident_id, actor)
        if result is None:
            raise ValidationError("cannot acknowledge an inactive incident")
        return result

    def acknowledge_if_active(self, incident_id: str, actor: str) -> dict[str, Any] | None:
        """ACK only an open or snoozed incident; never resurrect a resolved one."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            if incident["state"] not in DELIVERABLE_STATES:
                return None
            return self._transition(incident_id, "acknowledged", actor)

    def resolve(self, incident_id: str, actor: str) -> dict[str, Any]:
        """Resolve an incident and prevent future delivery from its prior state."""
        return self._transition(incident_id, "resolved", actor)

    def snooze(self, incident_id: str, until_epoch: float, actor: str) -> dict[str, Any]:
        """Temporarily defer pending delivery; raises ValidationError for past times."""
        if until_epoch <= time.time():
            raise ValidationError("snooze deadline must be in the future")
        return self._transition(incident_id, "snoozed", actor, until_epoch)

    def claim_telegram_update(self, update_id: int) -> bool:
        """Claim a Bot API update once so retries cannot repeat its action."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO telegram_updates(update_id, created_at) VALUES (?, ?)",
                (update_id, time.time()),
            )
            return cursor.rowcount == 1

    def apply_telegram_action(self, incident_id: str, action: str, actor: str) -> dict[str, Any]:
        """Apply an authorized compact Telegram control action to one active incident."""
        if action == "ack":
            result = self.acknowledge_if_active(incident_id, actor)
            return {"action": action, "state": result["state"] if result else "inactive"}
        if action == "snz":
            incident = self.get_incident(incident_id)
            if incident is None or incident["state"] not in DELIVERABLE_STATES:
                return {"action": action, "state": "inactive"}
            result = self.snooze(incident_id, time.time() + 900, actor)
            return {"action": action, "state": result["state"]}
        if action == "ask":
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            with self._lock, self._connection:
                self._audit(incident_id, "telegram_ask_requested", actor, {})
            return {"action": action, "state": incident["state"]}
        raise ValidationError("unsupported Telegram action")

    def record_telegram_ask(self, incident_id: str, actor: str, question: str) -> None:
        """Audit an operator question without treating its text as executable input."""
        normalized = question.strip()
        if not normalized or len(normalized) > 1000:
            raise ValidationError("ask question must be between 1 and 1000 characters")
        if self.get_incident(incident_id) is None:
            raise ValidationError("incident not found")
        with self._lock, self._connection:
            self._audit(incident_id, "telegram_ask_recorded", actor, {"question": normalized})

    def record_agent_job_result(self, incident_id: str, delivery_id: str, job_name: str, receipt: Mapping[str, Any]) -> None:
        """Persist a bounded terminal agent-job receipt without raw command output."""
        if self.get_incident(incident_id) is None:
            raise ValidationError("incident not found")
        result = receipt.get("agent_receipt") if isinstance(receipt.get("agent_receipt"), Mapping) else {}
        summary = {
            "delivery_id": delivery_id,
            "agent_job": job_name,
            "hub_job_id": str(receipt.get("job_id") or "")[:128],
            "route_id": str(receipt.get("route_id") or "")[:128],
            "status": str(receipt.get("status") or "")[:32],
            "session_id": str(result.get("session_id") or result.get("sessionId") or "")[:128],
            "created": result.get("created") is True,
            "delivery": str(result.get("delivery") or "")[:32],
        }
        with self._lock, self._connection:
            event_type = "agent_job_failed" if summary["status"] == "failed" else "agent_job_completed"
            self._audit(incident_id, event_type, "worker", summary)

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Return the current incident record, or None when it has never existed."""
        with self._lock:
            return self._row(self._connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone())

    def list_incidents(self) -> list[dict[str, Any]]:
        """Return incidents newest first for the initial inbox/API implementation."""
        with self._lock:
            return [dict(row) for row in self._connection.execute("SELECT * FROM incidents ORDER BY updated_at DESC").fetchall()]

    def delivery_payload(self, delivery: Mapping[str, Any]) -> dict[str, Any]:
        """Build the safe adapter payload for a previously claimed delivery."""
        incident = self.get_incident(str(delivery["incident_id"]))
        if incident is None:
            raise ValidationError("delivery references missing incident")
        payload = {"delivery": dict(delivery), "incident": incident}
        target_json = delivery.get("target_json")
        if isinstance(target_json, str):
            try:
                target = json.loads(target_json)
            except json.JSONDecodeError:
                target = None
            if isinstance(target, dict):
                payload["target"] = target
        return payload

    def mark_dispatcher_healthy(self) -> None:
        """Record a local worker heartbeat used by public readiness, not liveness."""
        with self._lock:
            self._dispatcher_heartbeat = time.time()

    def health(self) -> dict[str, Any]:
        """Probe durable dependencies and return only safe externally visible state."""
        storage_ready = False
        queued: int | None = None
        try:
            with self._lock:
                self._connection.execute("SELECT 1").fetchone()
                queued = self._connection.execute("SELECT COUNT(*) AS count FROM deliveries WHERE status = 'queued'").fetchone()["count"]
                storage_ready = True
        except sqlite3.Error:
            pass
        dispatcher_ready = time.time() - self._dispatcher_heartbeat <= 30
        ready = storage_ready and dispatcher_ready
        return {
            "schema": "notify.health.v1",
            "service": "notification-center",
            "status": "ok" if ready else "degraded",
            "storage_ready": storage_ready,
            "dispatcher_ready": dispatcher_ready,
            "queued_deliveries": queued,
            "version": "0.1.0",
        }
