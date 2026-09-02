"""SQLite-backed incident state machine for the notification-center MVP."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SEVERITIES = ("debug", "info", "notice", "important", "critical", "emergency")
OPEN_STATES = ("open", "acknowledged", "snoozed")
DELIVERABLE_STATES = ("open", "snoozed")
DEFAULT_CONSUMER_QUIET_HOURS = (
    {"start": "01:00", "end": "09:00", "timezone": "Europe/Moscow", "suppress": ["call"]},
)
HEALTH_PLAN_IDS = ("observe", "repair", "verify")
HEALTH_UPDATE_EVENT_TYPES = {
    "plans": "health.plans_attached",
    "plan_selection": "health.plan_selected",
    "remediation": "health.remediation_requested",
    "progress": "health.progress",
    "verification": "health.verification_recorded",
}
HEALTH_REMEDIATION_AGENT_JOB = "health-remediation"
HEALTH_DIAGNOSIS_AGENT_JOB = "health-diagnosis"
_HEALTH_REMEDIATION_ACTORS = {"agent-herder", "health-remediation"}
_SYNTHETIC_HEALTH_CORRELATION_PREFIX = "corr:live-health-canary:"
_CHOICE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CHOICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


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

    def __init__(self, database_path: Path | str, tokens: Mapping[str, Mapping[str, Any]], default_quiet_hours: list[Mapping[str, Any]] | None = None) -> None:
        """Open and initialize durable state; raises sqlite errors on storage failures."""
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._tokens = {str(key): dict(value) for key, value in tokens.items()}
        self._default_quiet_hours = list(DEFAULT_CONSUMER_QUIET_HOURS) if default_quiet_hours is None else default_quiet_hours
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
                    created_at REAL NOT NULL,
                    event_type TEXT,
                    producer TEXT,
                    plugin TEXT,
                    correlation_id TEXT,
                    parent_event_id TEXT,
                    parent_incident_id TEXT,
                    peer_ip TEXT,
                    source_ip TEXT,
                    proxy_ip TEXT,
                    forwarded_for TEXT
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    operator_note TEXT,
                    dedup_key TEXT NOT NULL,
                    collapse_key TEXT,
                    state TEXT NOT NULL,
                    occurrences INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    acknowledged_at REAL,
                    resolved_at REAL,
                    snoozed_until REAL,
                    event_type TEXT,
                    producer TEXT,
                    plugin TEXT,
                    correlation_id TEXT,
                    parent_event_id TEXT,
                    parent_incident_id TEXT,
                    peer_ip TEXT,
                    source_ip TEXT,
                    proxy_ip TEXT,
                    forwarded_for TEXT
                );
                CREATE TABLE IF NOT EXISTS consumers (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    token_fingerprint TEXT NOT NULL,
                    max_severity TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    quiet_hours_json TEXT,
                    profile_type TEXT,
                    profile_key TEXT,
                    operator_note TEXT
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
                    updated_at REAL NOT NULL,
                    result_json TEXT
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
                CREATE TABLE IF NOT EXISTS health_plan_selections (
                    incident_id TEXT PRIMARY KEY REFERENCES incidents(id),
                    plan_id TEXT NOT NULL,
                    event_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    update_id INTEGER PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS human_requests (
                    request_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    message TEXT NOT NULL,
                    choices_json TEXT NOT NULL,
                    allowed_actors_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    response_value TEXT,
                    response_actor TEXT,
                    resolved_at REAL,
                    cancelled_at REAL,
                    cancelled_by TEXT,
                    telegram_chat_id TEXT,
                    telegram_message_id INTEGER
                );
                """
            )
            incident_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(incidents)")}
            event_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(events)")}
            for column in ("event_type", "producer", "plugin", "correlation_id", "parent_event_id", "parent_incident_id", "peer_ip", "source_ip", "proxy_ip", "forwarded_for"):
                if column not in event_columns:
                    self._connection.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
            if "consumer_id" not in incident_columns:
                self._connection.execute("ALTER TABLE incidents ADD COLUMN consumer_id TEXT REFERENCES consumers(id)")
            if "operator_note" not in incident_columns:
                self._connection.execute("ALTER TABLE incidents ADD COLUMN operator_note TEXT")
            for column in ("event_type", "producer", "plugin", "correlation_id", "parent_event_id", "parent_incident_id", "peer_ip", "source_ip", "proxy_ip", "forwarded_for"):
                if column not in incident_columns:
                    self._connection.execute(f"ALTER TABLE incidents ADD COLUMN {column} TEXT")
            delivery_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(deliveries)")}
            if "target_json" not in delivery_columns:
                self._connection.execute("ALTER TABLE deliveries ADD COLUMN target_json TEXT NOT NULL DEFAULT '{}'")
            human_request_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(human_requests)")}
            if "telegram_chat_id" not in human_request_columns:
                self._connection.execute("ALTER TABLE human_requests ADD COLUMN telegram_chat_id TEXT")
            if "telegram_message_id" not in human_request_columns:
                self._connection.execute("ALTER TABLE human_requests ADD COLUMN telegram_message_id INTEGER")
            for column, definition in (
                ("policy_step_id", "TEXT"),
                ("repeat_number", "INTEGER"),
                ("result_json", "TEXT"),
            ):
                if column not in delivery_columns:
                    self._connection.execute(f"ALTER TABLE deliveries ADD COLUMN {column} {definition}")
            # Older databases stored health selections only as events. Seed the
            # durable single-winner guard without rewriting or deleting history.
            existing_health_selections = self._connection.execute(
                "SELECT incident_id, event_id, idempotency_key, payload_json, created_at FROM events WHERE event_type = 'health.plan_selected' ORDER BY created_at"
            ).fetchall()
            for row in existing_health_selections:
                try:
                    payload = json.loads(str(row["payload_json"]) or "{}")
                except (TypeError, ValueError):
                    payload = {}
                plan_id = str(payload.get("plan_id") or "")[:64]
                if plan_id:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO health_plan_selections(incident_id, plan_id, event_id, idempotency_key, created_at) VALUES (?, ?, ?, ?, ?)",
                        (row["incident_id"], plan_id, row["event_id"], row["idempotency_key"], row["created_at"]),
                    )
            policy_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(consumer_policy_stages)")}
            consumer_columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(consumers)")}
            if "quiet_hours_json" not in consumer_columns:
                self._connection.execute("ALTER TABLE consumers ADD COLUMN quiet_hours_json TEXT")
            for column in ("profile_type", "profile_key", "operator_note"):
                if column not in consumer_columns:
                    self._connection.execute(f"ALTER TABLE consumers ADD COLUMN {column} TEXT")
            self._connection.execute(
                "UPDATE consumers SET quiet_hours_json = ? WHERE quiet_hours_json IS NULL",
                (json.dumps(self._normalize_quiet_hours(self._default_quiet_hours), sort_keys=True),),
            )
            self._connection.execute("UPDATE consumers SET profile_type = 'custom' WHERE profile_type IS NULL")
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
            self._ensure_builtin_profiles()

    def _ensure_builtin_profiles(self) -> None:
        """Materialize the three built-in routes as ordinary delivery profiles."""
        now = time.time()
        quiet_hours = json.dumps(self._normalize_quiet_hours(self._default_quiet_hours), sort_keys=True)
        for profile_key, max_severity in (("emergency", "emergency"), ("important", "important"), ("log", "critical")):
            self._connection.execute(
                "INSERT OR IGNORE INTO consumers(id, project, name, token_hash, token_fingerprint, max_severity, created_at, updated_at, quiet_hours_json, profile_type, profile_key, operator_note) VALUES (?, '*', ?, ?, ?, ?, ?, ?, ?, 'builtin', ?, NULL)",
                (f"profile_{profile_key}", profile_key.title(), f"builtin:{profile_key}", f"builtin-{profile_key}", max_severity, now, now, quiet_hours, profile_key),
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
        operator_note = event.get("operator_note")
        if operator_note is not None and (not isinstance(operator_note, str) or len(operator_note.strip()) > 500):
            raise ValidationError("operator_note must be a string of at most 500 characters")
        choice_request_id = event.get("choice_request_id")
        choices = event.get("choices")
        if choice_request_id is not None or choices is not None:
            if not isinstance(choice_request_id, str) or not _CHOICE_REQUEST_ID_RE.fullmatch(choice_request_id.strip()):
                raise ValidationError("choice_request_id must be a bounded safe identifier")
            if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
                raise ValidationError("choices must contain between two and four entries")
            seen_choice_ids: set[str] = set()
            for choice in choices:
                if not isinstance(choice, Mapping) or set(choice) != {"choice_id", "label"}:
                    raise ValidationError("choices may contain only choice_id and label")
                choice_id = str(choice.get("choice_id") or "").strip()
                label = choice.get("label")
                if not _CHOICE_ID_RE.fullmatch(choice_id):
                    raise ValidationError("choice_id must be a bounded safe identifier")
                if not isinstance(label, str) or not 1 <= len(label.strip()) <= 128:
                    raise ValidationError("choice label must be between 1 and 128 characters")
                if choice_id in seen_choice_ids:
                    raise ValidationError("choice_id values must be unique")
                seen_choice_ids.add(choice_id)
        for field, limit in (("event_type", 128), ("producer", 128), ("plugin", 128), ("correlation_id", 256), ("parent_event_id", 128), ("parent_incident_id", 128)):
            value = event.get(field)
            if value is not None and (not isinstance(value, str) or len(value.strip()) > limit):
                raise ValidationError(f"{field} must be a string of at most {limit} characters")
        forbidden = sorted(
            set(event).intersection(
                {"target", "platform", "phone_number", "command", "delay_seconds", "retry", "stage", "url", "harness", "cwd", "prompt", "tool", "mcp", "credential", "credentials", "token", "secret", "callback_url"}
            )
        )
        if forbidden:
            raise ValidationError(f"event contains forbidden authority fields (forbidden delivery authority fields): {', '.join(forbidden)}")
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

    @staticmethod
    def _effective_agent_job(event: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
        """Resolve producer intent plus the central deterministic diagnosis policy."""
        explicit = str(event.get("agent_job") or "")
        if explicit:
            return explicit
        allowed_jobs = {str(value) for value in scope.get("agent_jobs", [])} if isinstance(scope.get("agent_jobs"), (list, tuple)) else set()
        required_health_identity = ("source_id", "host_id", "signal_type", "correlation_id")
        if (
            HEALTH_DIAGNOSIS_AGENT_JOB in allowed_jobs
            and str(event.get("kind") or "") == "incident"
            and str(event.get("event_type") or "").startswith("health.")
            and str(event.get("severity") or "") in {"critical", "emergency"}
            and all(str(event.get(field) or "").strip() for field in required_health_identity)
        ):
            return HEALTH_DIAGNOSIS_AGENT_JOB
        return ""

    def _source_health_recovery_matches(self, incident_id: str, event: Mapping[str, Any]) -> bool:
        """Accept an unselected health incident's recovery only from the same typed source."""
        if str(event.get("event_type") or "") != "health.recovered":
            return False
        keys = ("source_id", "host_id", "signal_type", "correlation_id")
        recovery_identity = {key: str(event.get(key) or "").strip() for key in keys}
        if not all(recovery_identity.values()):
            return False
        original = self._connection.execute(
            "SELECT payload_json FROM events WHERE incident_id = ? ORDER BY created_at, event_id LIMIT 1",
            (incident_id,),
        ).fetchone()
        if original is None:
            return False
        try:
            original_event = json.loads(str(original["payload_json"]))
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(original_event, dict) and all(
            str(original_event.get(key) or "").strip() == recovery_identity[key] for key in keys
        )

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
    def _human_request_result(row: sqlite3.Row) -> dict[str, Any]:
        """Convert one durable human request row to its JSON-safe API shape."""
        result = dict(row)
        result["schema"] = "ask_human.response.v1"
        result["choices"] = json.loads(str(result.pop("choices_json")))
        result["allowed_actors"] = json.loads(str(result.pop("allowed_actors_json")))
        return result

    def _human_request_row(self, token: str, request_id: str) -> sqlite3.Row:
        """Load and authorize one human request; raises ValidationError when absent."""
        row = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            if token not in self._tokens:
                raise AuthorizationError("invalid bearer token")
            raise ValidationError("human request not found")
        self._scope(token, str(row["project"]))
        return row

    def _expire_human_request(self, row: sqlite3.Row) -> sqlite3.Row:
        """Persist expiry for an overdue pending request and return its current row."""
        expires_at = row["expires_at"]
        if row["state"] == "pending" and expires_at is not None and float(expires_at) <= time.time():
            self._connection.execute("UPDATE human_requests SET state = 'expired' WHERE request_id = ? AND state = 'pending'", (row["request_id"],))
            row = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (row["request_id"],)).fetchone()
        return row

    def create_human_request(self, token: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and durably create one bounded AskHuman request."""
        if request.get("schema") != "ask_human.request.v1":
            raise ValidationError("unsupported human request schema")
        required = ("request_id", "project", "recipient", "mode", "message")
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ValidationError(f"missing required human request fields: {', '.join(missing)}")
        request_id = str(request["request_id"]).strip()
        project = str(request["project"]).strip()
        mode = str(request["mode"]).strip()
        if mode not in {"notify", "question", "choice"}:
            raise ValidationError("unsupported human request mode")
        self._scope(token, project)
        raw_choices = request.get("choices")
        choices: list[dict[str, str]] = []
        if mode == "choice":
            if not isinstance(raw_choices, list) or len(raw_choices) not in {2, 3}:
                raise ValidationError("choice requests require two or three choices")
            seen_values: set[str] = set()
            for raw in raw_choices:
                if not isinstance(raw, Mapping):
                    raise ValidationError("human request choices must be objects")
                label = str(raw.get("label") or "").strip()
                value = str(raw.get("value") or "").strip()
                if not label or not value or len(label) > 80 or len(value) > 128 or value in seen_values:
                    raise ValidationError("human request choices require unique bounded label and value")
                seen_values.add(value)
                choices.append({"label": label, "value": value})
        elif raw_choices not in (None, []):
            raise ValidationError("choices are only allowed in choice mode")
        raw_actors = request.get("allowed_actors")
        if not isinstance(raw_actors, list) or not raw_actors:
            raise ValidationError("human request requires allowed actors")
        actors = [str(actor).strip() for actor in raw_actors]
        if any(not actor or len(actor) > 128 for actor in actors) or len(set(actors)) != len(actors):
            raise ValidationError("human request actors must be unique bounded values")
        expires_at = request.get("expires_at")
        expires = float(expires_at) if expires_at is not None else None
        now = time.time()
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    "INSERT INTO human_requests(request_id, project, recipient, mode, message, choices_json, allowed_actors_json, state, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (request_id, project, str(request["recipient"]).strip(), mode, str(request["message"]).strip()[:4000], json.dumps(choices, ensure_ascii=False), json.dumps(actors, ensure_ascii=False), now, expires),
                )
            except sqlite3.IntegrityError as error:
                raise ValidationError("human request already exists") from error
            row = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
            return self._human_request_result(self._expire_human_request(row))

    def get_human_request(self, token: str, request_id: str) -> dict[str, Any]:
        """Return the stable current state of one authorized human request."""
        with self._lock, self._connection:
            return self._human_request_result(self._expire_human_request(self._human_request_row(token, request_id)))

    def get_human_request_from_telegram(self, request_id: str) -> dict[str, Any]:
        """Read one request for the signed, allowlisted in-process Telegram poller."""
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                raise ValidationError("human request not found")
            return self._human_request_result(self._expire_human_request(row))

    def bind_human_request_message(self, token: str, request_id: str, chat_id: str, message_id: int) -> dict[str, Any]:
        """Bind one sent Telegram message so a ForceReply can resolve its request."""
        chat_id = str(chat_id).strip()
        if not chat_id or not isinstance(message_id, int) or message_id <= 0:
            raise ValidationError("valid Telegram chat and message ids are required")
        with self._lock, self._connection:
            row = self._expire_human_request(self._human_request_row(token, request_id))
            if row["state"] != "pending":
                raise ValidationError(f"human request is {row['state']}")
            self._connection.execute(
                "UPDATE human_requests SET telegram_chat_id = ?, telegram_message_id = ? WHERE request_id = ?",
                (chat_id, message_id, request_id),
            )
            current = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
            return self._human_request_result(current)

    def _resolve_human_request_row(self, row: sqlite3.Row, actor: str, value: str) -> dict[str, Any]:
        """Resolve an already selected row; caller owns the lock and transport trust."""
        actors = json.loads(str(row["allowed_actors_json"]))
        if actor not in actors:
            raise ValidationError("human response actor is not allowed")
        if row["state"] == "resolved":
            if row["response_actor"] == actor and row["response_value"] == value:
                return self._human_request_result(row)
            raise ValidationError("human request already resolved")
        if row["state"] != "pending":
            raise ValidationError(f"human request is {row['state']}")
        if row["mode"] == "notify":
            raise ValidationError("notification requests cannot be resolved")
        choices = json.loads(str(row["choices_json"]))
        if row["mode"] == "choice" and value not in {choice["value"] for choice in choices}:
            raise ValidationError("human response is not a valid choice")
        resolved_at = time.time()
        update = self._connection.execute(
            "UPDATE human_requests SET state = 'resolved', response_value = ?, response_actor = ?, resolved_at = ? WHERE request_id = ? AND state = 'pending'",
            (value, actor, resolved_at, row["request_id"]),
        )
        current = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (row["request_id"],)).fetchone()
        if update.rowcount != 1:
            if current["state"] == "resolved" and current["response_actor"] == actor and current["response_value"] == value:
                return self._human_request_result(current)
            raise ValidationError(f"human request is {current['state']}")
        return self._human_request_result(current)

    def resolve_human_request(self, token: str, request_id: str, actor: str, value: str) -> dict[str, Any]:
        """Resolve one request once, allowing idempotent replay of the same answer."""
        actor = str(actor).strip()
        value = str(value).strip()
        if not actor or not value or len(value) > 4000:
            raise ValidationError("human response actor and value are required")
        with self._lock, self._connection:
            row = self._expire_human_request(self._human_request_row(token, request_id))
            return self._resolve_human_request_row(row, actor, value)

    def resolve_human_request_from_telegram(self, request_id: str, actor: str, value: str) -> dict[str, Any]:
        """Resolve from the allowlisted in-process Telegram poller without a producer token."""
        actor = str(actor).strip()
        value = str(value).strip()
        if not actor or not value or len(value) > 4000:
            raise ValidationError("human response actor and value are required")
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                raise ValidationError("human request not found")
            return self._resolve_human_request_row(self._expire_human_request(row), actor, value)

    def resolve_human_reply_from_telegram(self, chat_id: str, message_id: int, actor: str, value: str) -> dict[str, Any] | None:
        """Resolve only a reply correlated to the exact sent Telegram question."""
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM human_requests WHERE telegram_chat_id = ? AND telegram_message_id = ? AND mode = 'question' ORDER BY created_at DESC LIMIT 1",
                (str(chat_id), message_id),
            ).fetchone()
            if row is None:
                return None
            return self._resolve_human_request_row(self._expire_human_request(row), str(actor).strip(), str(value).strip())

    def cancel_human_request(self, token: str, request_id: str, actor: str) -> dict[str, Any]:
        """Cancel one pending request without changing any existing terminal state."""
        actor = str(actor).strip() or "api"
        with self._lock, self._connection:
            row = self._expire_human_request(self._human_request_row(token, request_id))
            if row["state"] != "pending":
                raise ValidationError(f"human request is {row['state']}")
            update = self._connection.execute(
                "UPDATE human_requests SET state = 'cancelled', cancelled_at = ?, cancelled_by = ? WHERE request_id = ? AND state = 'pending'",
                (time.time(), actor, request_id),
            )
            current = self._connection.execute("SELECT * FROM human_requests WHERE request_id = ?", (request_id,)).fetchone()
            if update.rowcount != 1:
                raise ValidationError(f"human request is {current['state']}")
            return self._human_request_result(current)

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
                ("vk", "message"),
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
            children: dict[str, list[str]] = {step_id: [] for step_id in ids}
            for step in normalized:
                previous = step["previous_step_id"]
                if previous is not None:
                    children[previous].append(step["step_id"])
            if any(len(successors) > 1 for successors in children.values()):
                raise ValidationError("generic consumer policy must be one linear chain")
            visited: set[str] = set()
            current = roots[0]["step_id"]
            while current not in visited:
                visited.add(current)
                successors = children[current]
                if not successors:
                    break
                current = successors[0]
            if len(visited) != len(ids):
                raise ValidationError("generic consumer policy must be connected and acyclic")
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

    def _normalize_quiet_hours(self, quiet_hours: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
        """Validate per-consumer quiet windows without introducing global policy."""
        raw_rules = list(self._default_quiet_hours) if quiet_hours is None else quiet_hours
        if not isinstance(raw_rules, list):
            raise ValidationError("consumer quiet_hours must be a list")
        normalized: list[dict[str, Any]] = []
        for raw in raw_rules:
            if not isinstance(raw, Mapping):
                raise ValidationError("consumer quiet-hour rules must be objects")
            start = str(raw.get("start") or "")
            end = str(raw.get("end") or "")
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
                raise ValidationError("consumer quiet-hour times must use HH:MM")
            timezone = str(raw.get("timezone") or "Europe/Moscow")
            try:
                ZoneInfo(timezone)
            except Exception as error:
                raise ValidationError("consumer quiet-hour timezone is invalid") from error
            suppress = raw.get("suppress", ["call"])
            if not isinstance(suppress, list) or not suppress or any(str(item) not in {"call", "message"} for item in suppress):
                raise ValidationError("consumer quiet-hour suppress must contain call or message")
            normalized.append({"start": start, "end": end, "timezone": timezone, "suppress": sorted({str(item) for item in suppress})})
        return normalized

    def create_consumer(
        self, project: str, name: str, policy: list[Mapping[str, Any]], max_severity: str = "critical", quiet_hours: list[Mapping[str, Any]] | None = None, operator_note: str | None = None
    ) -> dict[str, Any]:
        """Persist an operator-owned consumer and reveal its intake token once."""
        project = project.strip()
        name = name.strip()
        if not project or not name:
            raise ValidationError("consumer project and name are required")
        if max_severity not in SEVERITIES:
            raise ValidationError("unsupported consumer maximum severity")
        normalized_policy = self._validate_consumer_policy(policy)
        normalized_quiet_hours = self._normalize_quiet_hours(quiet_hours)
        normalized_note = str(operator_note or "").strip()
        if len(normalized_note) > 500:
            raise ValidationError("operator_note must be at most 500 characters")
        consumer_id = f"consumer_{uuid.uuid4().hex}"
        intake_token = f"nct_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        token_hash = hashlib.sha256(intake_token.encode("utf-8")).hexdigest()
        fingerprint = token_hash[:12]
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO consumers(id, project, name, token_hash, token_fingerprint, max_severity, created_at, updated_at, quiet_hours_json, profile_type, profile_key, operator_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'custom', NULL, ?)",
                (consumer_id, project, name, token_hash, fingerprint, max_severity, now, now, json.dumps(normalized_quiet_hours, sort_keys=True), normalized_note or None),
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
                "SELECT id, project, name, token_fingerprint, max_severity, created_at, updated_at, quiet_hours_json, profile_type, profile_key, operator_note FROM consumers WHERE id = ?",
                (consumer_id,),
            ).fetchone()
            if consumer is None:
                return None
            result = dict(consumer)
            try:
                result["quiet_hours"] = json.loads(result.pop("quiet_hours_json") or "[]")
            except json.JSONDecodeError:
                result["quiet_hours"] = list(self._default_quiet_hours)
            result["profile_type"] = result.get("profile_type") or "custom"
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

    def create_event(self, token: str, idempotency_key: str, event: Mapping[str, Any], request_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
        agent_job = self._effective_agent_job(event, scope)
        if agent_job:
            allowed_jobs = scope.get("agent_jobs", [])
            if not isinstance(allowed_jobs, (list, tuple)) or agent_job not in {str(value) for value in allowed_jobs}:
                raise AuthorizationError("token is not allowed to start this agent job")
        now = time.time()
        event_type = str(event.get("event_type") or event.get("kind") or "")[:128]
        producer = str(event.get("producer") or "")[:128] or None
        plugin = str(event.get("plugin") or "")[:128] or None
        correlation_id = str(event.get("correlation_id") or "")[:256] or None
        parent_event_id = str(event.get("parent_event_id") or "")[:128] or None
        parent_incident_id = str(event.get("parent_incident_id") or "")[:128] or None
        ingress = {str(key): str(value)[:512] for key, value in (request_meta or {}).items() if str(key) in {"peer_ip", "source_ip", "proxy_ip", "forwarded_for"}}
        with self._lock, self._connection:
            payload_json = json.dumps(dict(event), ensure_ascii=False, sort_keys=True)
            consumer_id = str(scope.get("consumer_id") or "") or self._builtin_profile_for_event(event)
            previous = self._connection.execute("SELECT event_id, incident_id, payload_json FROM events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different event content")
                previous_incident = self.get_incident(str(previous["incident_id"]))
                initial = self._connection.execute(
                    "SELECT id FROM deliveries WHERE incident_id = ? AND channel NOT LIKE 'gptadmin.agent:%' ORDER BY created_at, id LIMIT 1",
                    (previous["incident_id"],),
                ).fetchone()
                previous_event = json.loads(previous["payload_json"])
                previous_job = self._effective_agent_job(previous_event, scope) if isinstance(previous_event, dict) else ""
                agent_delivery = self._connection.execute(
                    "SELECT id FROM deliveries WHERE delivery_key = ?",
                    (f"{previous['incident_id']}:gptadmin.agent:{previous_job}:event:{previous['event_id']}",),
                ).fetchone() if previous_job else None
                if previous_job == HEALTH_DIAGNOSIS_AGENT_JOB:
                    agent_delivery = self._connection.execute(
                        "SELECT id FROM deliveries WHERE delivery_key = ?",
                        (f"{previous['incident_id']}:gptadmin.agent:{previous_job}:incident",),
                    ).fetchone() or agent_delivery
                return {"event_id": previous["event_id"], "incident_id": previous["incident_id"], "state": self.get_incident(previous["incident_id"])["state"], "deduplicated": False, "idempotent": True, "initial_delivery_id": initial["id"] if initial else None, "agent_job_delivery_id": agent_delivery["id"] if agent_delivery else None}
            project, recipient, dedup_key = str(event["project"]), str(event["recipient"]), str(event["dedup_key"])
            parent = None
            if parent_incident_id:
                parent = self._connection.execute("SELECT id, project, recipient FROM incidents WHERE id = ?", (parent_incident_id,)).fetchone()
                if parent is None or str(parent["project"]) != project or str(parent["recipient"]) != recipient:
                    raise ValidationError("parent_incident_id must reference an incident in the same project and recipient")
            if parent_event_id:
                parent_event = self._connection.execute("SELECT event_id, incident_id FROM events WHERE event_id = ?", (parent_event_id,)).fetchone()
                if parent_event is None or (parent is not None and str(parent_event["incident_id"]) != str(parent["id"])):
                    raise ValidationError("parent_event_id must reference the selected parent incident")
                if parent is None:
                    parent = self._connection.execute("SELECT id, project, recipient FROM incidents WHERE id = ?", (parent_event["incident_id"],)).fetchone()
                    if parent is None or str(parent["project"]) != project or str(parent["recipient"]) != recipient:
                        raise ValidationError("parent_event_id must reference an incident in the same project and recipient")
                parent_incident_id = str(parent["id"])
            existing = self._connection.execute("SELECT id FROM incidents WHERE project = ? AND recipient = ? AND IFNULL(consumer_id, '') = IFNULL(?, '') AND dedup_key = ? AND state != 'resolved'", (project, recipient, consumer_id, dedup_key)).fetchone()
            deduplicated = existing is not None
            if existing is None:
                incident_id = f"inc_{uuid.uuid4().hex}"
                self._connection.execute(
                    "INSERT INTO incidents(id, project, recipient, kind, severity, title, body, operator_note, dedup_key, collapse_key, consumer_id, state, occurrences, created_at, updated_at, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, peer_ip, source_ip, proxy_ip, forwarded_for) VALUES (" + ", ".join(["?"] * 11) + ", 'open', 1, " + ", ".join(["?"] * 12) + ")",
                    (incident_id, project, recipient, str(event["kind"]), str(event["severity"]), str(event["title"]), str(event.get("body") or ""), str(event.get("operator_note") or "").strip() or None, dedup_key, event.get("collapse_key"), consumer_id, now, now, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, ingress.get("peer_ip"), ingress.get("source_ip"), ingress.get("proxy_ip"), ingress.get("forwarded_for")),
                )
                self._audit(incident_id, "incident_created", "producer", {"dedup_key": dedup_key})
                if parent_incident_id:
                    self._audit(incident_id, "incident_child_linked", "producer", {"parent_incident_id": parent_incident_id, "parent_event_id": parent_event_id})
            else:
                incident_id = str(existing["id"])
                self._connection.execute("UPDATE incidents SET occurrences = occurrences + 1, title = ?, body = ?, operator_note = ?, updated_at = ? WHERE id = ?", (str(event["title"]), str(event.get("body") or ""), str(event.get("operator_note") or "").strip() or None, now, incident_id))
                self._audit(incident_id, "incident_repeated", "producer", {"dedup_key": dedup_key})
            event_id = f"evt_{uuid.uuid4().hex}"
            self._connection.execute("INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, peer_ip, source_ip, proxy_ip, forwarded_for) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (idempotency_key, event_id, incident_id, payload_json, now, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, ingress.get("peer_ip"), ingress.get("source_ip"), ingress.get("proxy_ip"), ingress.get("forwarded_for")))
            ingress.update({"project": project, "profile_id": consumer_id, "severity": str(event["severity"]), "event_type": event_type, "producer": producer, "plugin": plugin, "correlation_id": correlation_id, "parent_incident_id": parent_incident_id, "parent_event_id": parent_event_id})
            self._audit(incident_id, "event_ingress", "producer", ingress)
            delivery_id = self._schedule_consumer_policy(incident_id, consumer_id, now)
            agent_step = "incident" if agent_job == HEALTH_DIAGNOSIS_AGENT_JOB else f"event:{event_id}"
            agent_delivery_id = self._schedule_delivery(incident_id, f"gptadmin.agent:{agent_job}", agent_step, now) if agent_job else None
            return {"event_id": event_id, "incident_id": incident_id, "state": self.get_incident(incident_id)["state"], "deduplicated": deduplicated, "idempotent": False, "initial_delivery_id": delivery_id, "agent_job_delivery_id": agent_delivery_id}

    def _builtin_profile_for_event(self, event: Mapping[str, Any]) -> str:
        """Resolve a legacy event to the ordinary built-in mode profile."""
        severity = str(event.get("severity") or "")
        profile_key = severity if severity in {"emergency", "important"} else "log"
        return f"profile_{profile_key}"

    def _initial_channel_for_profile(self, profile_id: str | None) -> str:
        if not profile_id:
            return "telegram.main"
        row = self._connection.execute("SELECT profile_type FROM consumers WHERE id = ?", (profile_id,)).fetchone()
        return "telegram.main" if row is None or str(row["profile_type"]) == "builtin" else f"telegram.consumer:{profile_id}"

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
                if self._health_selected_plan(incident_id) or not self._source_health_recovery_matches(incident_id, event):
                    self._require_health_resolution_gate(incident_id)
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

    def _schedule_consumer_policy(self, incident_id: str, consumer_id: str, now: float) -> str | None:
        """Materialize the root notification before any optional diagnosis."""
        incident = self.get_incident(incident_id)
        is_health = incident is not None and str(incident.get("event_type") or "").startswith("health.")
        profile = self._connection.execute("SELECT profile_type FROM consumers WHERE id = ?", (consumer_id,)).fetchone()
        if profile is not None and str(profile["profile_type"]) == "builtin":
            return self._schedule_delivery(incident_id, "telegram.main", "initial", now)
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

    def _quiet_hours_resume_at(self, consumer_id: str | None, channel: str, now_epoch: float) -> float | None:
        """Return the next allowed time for a per-consumer call delivery."""
        if not consumer_id or not channel.endswith(".call"):
            return None
        row = self._connection.execute("SELECT quiet_hours_json FROM consumers WHERE id = ?", (consumer_id,)).fetchone()
        if row is None:
            return None
        try:
            rules = json.loads(row["quiet_hours_json"] or "[]")
        except json.JSONDecodeError:
            return None
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, Mapping) or "call" not in rule.get("suppress", ["call"]):
                continue
            try:
                timezone = ZoneInfo(str(rule.get("timezone") or "Europe/Moscow"))
                start_hour, start_minute = (int(part) for part in str(rule["start"]).split(":"))
                end_hour, end_minute = (int(part) for part in str(rule["end"]).split(":"))
            except (KeyError, TypeError, ValueError):
                continue
            try:
                local_now = datetime.fromtimestamp(now_epoch, timezone)
            except (OverflowError, OSError, ValueError):
                return None
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            minute = local_now.hour * 60 + local_now.minute
            active = (start <= minute < end) if start < end else (minute >= start or minute < end)
            if not active:
                continue
            end_date = local_now.date() + (timedelta(days=1) if start > end and minute >= start else timedelta())
            resume = datetime.combine(end_date, datetime.min.time(), timezone) + timedelta(hours=end_hour, minutes=end_minute)
            return resume.timestamp()
        return None

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
                "SELECT d.*, i.consumer_id FROM deliveries d JOIN incidents i ON i.id = d.incident_id WHERE ((d.status = 'queued' AND d.due_at <= ?) OR (d.status = 'claimed' AND d.claimed_at <= ?)) AND i.state IN (?, ?) AND (i.snoozed_until IS NULL OR i.snoozed_until <= ?) ORDER BY d.due_at LIMIT ?",
                (now, now - max(1, lease_seconds), *DELIVERABLE_STATES, now, limit),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                quiet_resume = self._quiet_hours_resume_at(row["consumer_id"], str(row["channel"]), now)
                if quiet_resume is not None and quiet_resume > now:
                    self._connection.execute("UPDATE deliveries SET due_at = ?, updated_at = ? WHERE id = ?", (quiet_resume, now, row["id"]))
                    continue
                cursor = self._connection.execute("UPDATE deliveries SET status = 'claimed', claimed_at = ?, attempt = attempt + 1, updated_at = ? WHERE id = ? AND (status = 'queued' OR (status = 'claimed' AND claimed_at <= ?))", (now, now, row["id"], now - max(1, lease_seconds)))
                if cursor.rowcount != 1:
                    continue
                claimed = self._connection.execute("SELECT * FROM deliveries WHERE id = ?", (row["id"],)).fetchone()
                result.append(dict(claimed))
            return result

    @contextmanager
    def delivery_send_lock(self):
        """Serialize claim reconciliation with the final external delivery reservation."""
        with self._lock:
            yield

    def delivery_is_claimed(self, delivery_id: str, claimed_at: float | None = None, attempt: int | None = None) -> bool:
        """Return whether the worker still owns the exact durable lease generation."""
        with self._lock:
            row = self._connection.execute("SELECT status, claimed_at, attempt FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            if row is None or str(row["status"]) != "claimed":
                return False
            if claimed_at is not None and float(row["claimed_at"] or 0) != float(claimed_at):
                return False
            if attempt is not None and int(row["attempt"] or 0) != int(attempt):
                return False
            return True

    def reserve_delivery_send(self, delivery_id: str, claimed_at: float, attempt: int) -> bool:
        """Durably reserve one lease generation before crossing an external send boundary."""
        now = time.time()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE deliveries SET status = 'sending', last_error = NULL, updated_at = ? WHERE id = ? AND status = 'claimed' AND claimed_at = ? AND attempt = ?",
                (now, delivery_id, claimed_at, attempt),
            )
            return cursor.rowcount == 1

    def _schedule_next_policy_successor(self, incident_id: str, consumer_id: str, previous_step_id: str, now: float, health: bool) -> bool:
        """Schedule the first non-Telegram successor when Health skips message repeats."""
        current_step = previous_step_id
        visited: set[str] = set()
        for _ in range(32):
            if current_step in visited:
                return False
            visited.add(current_step)
            successor = self._connection.execute(
                "SELECT step_id, platform, action, target_json FROM consumer_policy_stages WHERE consumer_id = ? AND previous_step_id = ? AND enabled = 1",
                (consumer_id, current_step),
            ).fetchone()
            if successor is None:
                return False
            channel = f"{successor['platform']}.{successor['action']}"
            if health and channel.startswith("telegram."):
                current_step = str(successor["step_id"])
                continue
            self._schedule_delivery(
                incident_id,
                channel,
                f"step:{successor['step_id']}:repeat:1",
                now,
                json.loads(successor["target_json"]),
                str(successor["step_id"]),
                1,
            )
            return True
        return False

    def complete_delivery(self, delivery_id: str, outcome: str, error: str | None = None, retry_after_seconds: float = 30, claimed_at: float | None = None, attempt: int | None = None, result: Mapping[str, Any] | None = None) -> None:
        """Mark one claim sent, cancelled, or safely queued for a future retry."""
        if outcome not in ("sent", "failed", "cancelled", "retry", "uncertain", "superseded"):
            raise ValidationError("delivery outcome must be sent, failed, cancelled, retry, uncertain, or superseded")
        now = time.time()
        safe_error = " ".join((error or "").replace("\x00", "").splitlines())[-1000:] or None
        result_json = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)[:4000] if isinstance(result, Mapping) else None
        with self._lock, self._connection:
            row = self._connection.execute("SELECT d.status, d.claimed_at, d.attempt, d.incident_id, d.channel, d.policy_step_id, d.repeat_number, i.state, i.consumer_id, i.event_type FROM deliveries d JOIN incidents i ON i.id = d.incident_id WHERE d.id = ?", (delivery_id,)).fetchone()
            if row is None:
                raise ValidationError("delivery not found")
            if str(row["status"]) == "cancelled":
                return
            if outcome == "retry" and str(row["status"]) not in {"claimed", "sending"}:
                return
            if (claimed_at is not None or attempt is not None) and str(row["status"]) not in {"claimed", "sending"}:
                return
            if claimed_at is not None and float(row["claimed_at"] or 0) != float(claimed_at):
                return
            if attempt is not None and int(row["attempt"] or 0) != int(attempt):
                return
            if outcome in ("sent", "failed", "retry") and str(row["state"]) not in DELIVERABLE_STATES:
                outcome = "cancelled"
                safe_error = safe_error or "incident is no longer active"
            if outcome == "retry":
                self._connection.execute("UPDATE deliveries SET status = 'queued', due_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (now + max(1, retry_after_seconds), safe_error, now, delivery_id))
            else:
                self._connection.execute("UPDATE deliveries SET status = ?, last_error = ?, result_json = COALESCE(?, result_json), updated_at = ? WHERE id = ?", (outcome, safe_error, result_json, now, delivery_id))
            if outcome == "sent" and row["policy_step_id"] is not None and str(row["state"]) in DELIVERABLE_STATES:
                try:
                    step = self._connection.execute(
                        "SELECT platform, action, target_json, retry_interval_seconds, max_repeats FROM consumer_policy_stages WHERE consumer_id = ? AND step_id = ? AND enabled = 1",
                        (row["consumer_id"], row["policy_step_id"]),
                    ).fetchone()
                    repeat_number = int(row["repeat_number"] or 1)
                    is_health = str(row["event_type"] or "").startswith("health.")
                    if step is not None and repeat_number < int(step["max_repeats"]):
                        next_channel = f"{step['platform']}.{step['action']}"
                        if is_health and next_channel.startswith("telegram."):
                            self._schedule_next_policy_successor(str(row["incident_id"]), str(row["consumer_id"]), str(row["policy_step_id"]), now, is_health)
                        else:
                            self._schedule_delivery(
                                str(row["incident_id"]), next_channel,
                                f"step:{row['policy_step_id']}:repeat:{repeat_number + 1}",
                                now + float(step["retry_interval_seconds"]), json.loads(step["target_json"]),
                                str(row["policy_step_id"]), repeat_number + 1,
                            )
                    elif step is not None:
                        self._schedule_next_policy_successor(str(row["incident_id"]), str(row["consumer_id"]), str(row["policy_step_id"]), now, is_health)
                except Exception as followup_error:
                    self._audit(
                        str(row["incident_id"]),
                        "delivery_followup_failed",
                        "worker",
                        {"delivery_id": delivery_id, "error": str(followup_error)[:1000]},
                    )
            self._audit(str(row["incident_id"]), f"delivery_{outcome}", "worker", {"delivery_id": delivery_id, "error": safe_error})

    def record_health_delivery_migrated(self, source_delivery_id: str, receipt: Mapping[str, Any], actor: str = "health-workflow") -> None:
        """Persist proof that an existing Telegram Health message was edited in place."""
        result_json = json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True)[:4000]
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT incident_id, status, channel FROM deliveries WHERE id = ?",
                (source_delivery_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("source delivery not found")
            if str(row["status"]) != "sent" or not str(row["channel"]).startswith("telegram."):
                raise ValidationError("source delivery is not a sent Telegram delivery")
            self._connection.execute(
                "UPDATE deliveries SET result_json = ?, last_error = NULL, updated_at = ? WHERE id = ? AND status = 'sent'",
                (result_json, now, source_delivery_id),
            )
            self._audit(
                str(row["incident_id"]),
                "health.legacy_card_migrated",
                actor,
                {"source_delivery_id": source_delivery_id, "receipt": dict(receipt)},
            )

    def _transition(self, incident_id: str, state: str, actor: str, snoozed_until: float | None = None) -> dict[str, Any]:
        """Apply an incident transition and cancel future alerts when appropriate."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            now = time.time()
            if state == "acknowledged":
                self._connection.execute("UPDATE incidents SET state = ?, acknowledged_at = ?, snoozed_until = NULL, updated_at = ? WHERE id = ?", (state, now, now, incident_id))
                self._connection.execute("UPDATE deliveries SET status = 'cancelled', updated_at = ? WHERE incident_id = ? AND status IN ('queued', 'claimed', 'sending')", (now, incident_id))
            elif state == "resolved":
                self._connection.execute("UPDATE incidents SET state = ?, resolved_at = ?, snoozed_until = NULL, updated_at = ? WHERE id = ?", (state, now, now, incident_id))
                self._connection.execute("UPDATE deliveries SET status = 'cancelled', updated_at = ? WHERE incident_id = ? AND status IN ('queued', 'claimed', 'sending')", (now, incident_id))
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
        self._require_health_resolution_gate(incident_id)
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

    def telegram_update_offset(self) -> int:
        """Return the first Bot API update not durably processed yet."""
        value = self.get_runtime_setting("telegram_update_offset", "0")
        try:
            return max(0, int(value or "0"))
        except ValueError:
            return 0

    def release_telegram_update(self, update_id: int) -> None:
        """Release a failed update so the same offset can be processed again."""
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM telegram_updates WHERE update_id = ?", (update_id,))

    def complete_telegram_update(self, update_id: int) -> None:
        """Advance the durable Bot API offset only after successful processing."""
        if update_id < 0:
            raise ValidationError("Telegram update id must not be negative")
        next_offset = update_id + 1
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT value FROM runtime_settings WHERE key = 'telegram_update_offset'"
            ).fetchone()
            try:
                current = int(row["value"]) if row is not None else 0
            except ValueError:
                current = 0
            if next_offset > current:
                self._connection.execute(
                    "INSERT INTO runtime_settings(key, value, updated_at) VALUES ('telegram_update_offset', ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (str(next_offset), time.time()),
                )

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
        if action == "ai":
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            if incident["state"] not in DELIVERABLE_STATES:
                return {"action": action, "state": "inactive", "idempotent": True, "agent_job_delivery_id": None}
            delivery_key = f"{incident_id}:gptadmin.agent:{HEALTH_DIAGNOSIS_AGENT_JOB}:incident"
            with self._lock, self._connection:
                previous = self._connection.execute("SELECT id FROM deliveries WHERE delivery_key = ?", (delivery_key,)).fetchone()
                delivery_id = self._schedule_delivery(incident_id, f"gptadmin.agent:{HEALTH_DIAGNOSIS_AGENT_JOB}", "incident", time.time())
                self._audit(
                    incident_id,
                    "telegram_ai_requested",
                    actor,
                    {"delivery_id": delivery_id, "idempotent": previous is not None},
                )
            return {"action": action, "state": incident["state"], "idempotent": previous is not None, "agent_job_delivery_id": delivery_id}
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

    def record_health_agent_progress(self, incident_id: str, delivery_id: str, payload: Mapping[str, Any], job: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Persist useful progress snapshots observed while a remediation runs."""
        selection = payload.get("health_selection") if isinstance(payload.get("health_selection"), Mapping) else {}
        selected_plan = self._health_text(selection.get("plan_id") or "", 64)
        entries = job.get("progress")
        if not selected_plan or not isinstance(entries, list):
            return []
        recorded: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("useful_progress") is not True:
                continue
            plan_id = self._health_text(entry.get("plan_id") or selected_plan, 64)
            if plan_id != selected_plan:
                continue
            step = self._health_text(entry.get("step") or "", 128)
            evidence_refs = [
                self._health_text(item, 128)
                for item in (entry.get("evidence_refs") if isinstance(entry.get("evidence_refs"), list) else [])[:16]
                if self._health_text(item, 128)
            ]
            fingerprint = self._health_text(entry.get("progress_fingerprint") or entry.get("fingerprint") or "", 128)
            if not (step or evidence_refs or fingerprint):
                continue
            if not fingerprint:
                fingerprint = hashlib.sha256(
                    json.dumps({"plan_id": plan_id, "step": step, "evidence_refs": evidence_refs}, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()[:64]
            try:
                result = self.record_health_progress(
                    incident_id,
                    f"{delivery_id}:health.progress:{fingerprint}",
                    plan_id,
                    step,
                    evidence_refs,
                    fingerprint,
                    step.strip().lower() in {"heartbeat", "keepalive", "heartbeat-only"},
                    "agent-herder",
                )
            except ValidationError as error:
                with self._lock, self._connection:
                    self._audit(
                        incident_id,
                        "health.progress_rejected",
                        "worker",
                        {"delivery_id": delivery_id, "reason": self._health_text(error, 256)},
                    )
                continue
            recorded.append(result)
        return recorded

    def _record_health_remediation_receipt(
        self,
        incident_id: str,
        delivery_id: str,
        receipt: Mapping[str, Any],
        health_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Turn one completed remediation receipt into durable health outcomes.

        The agent's terminal message is not itself a resolution claim.  The
        source fingerprint, a distinct verifier identity, useful progress, and
        the matching verification receipt are all required before resolving.
        Stable keys make a worker retry safe after a crash between any step.
        """
        agent_receipt = receipt.get("agent_receipt")
        if not isinstance(agent_receipt, Mapping):
            return {"accepted": False, "resolved": False, "reason": "agent remediation receipt is missing"}
        selection = health_context.get("selection") if isinstance(health_context.get("selection"), Mapping) else {}
        selected_plan = self._health_text(selection.get("plan_id") or "", 64)
        receipt_plan = self._health_text(agent_receipt.get("plan_id") or "", 64)
        if not selected_plan or not receipt_plan or selected_plan != receipt_plan:
            return {"accepted": False, "resolved": False, "reason": "remediation receipt plan does not match the selected plan"}

        def refs(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            result: list[str] = []
            for item in value[:16]:
                bounded = self._health_text(item, 128)
                if bounded and bounded not in result:
                    result.append(bounded)
            return result

        trace_refs: list[str] = []
        for value in (health_context.get("trace_refs"), agent_receipt.get("trace_refs")):
            for trace_ref in refs(value):
                if trace_ref not in trace_refs:
                    trace_refs.append(trace_ref)
        for value in (receipt.get("job_id"), agent_receipt.get("session_id")):
            bounded = self._health_text(value, 128)
            if bounded and bounded not in trace_refs:
                trace_refs.append(bounded)

        evidence_refs = refs(agent_receipt.get("evidence_refs"))
        progress_fingerprint = self._health_text(agent_receipt.get("progress_fingerprint") or "", 128)
        if not progress_fingerprint:
            progress_fingerprint = hashlib.sha256(
                json.dumps({"plan_id": selected_plan, "step": agent_receipt.get("step") or selected_plan, "evidence_refs": evidence_refs}, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:64]
        progress_key = f"{delivery_id}:health.progress:{progress_fingerprint}"
        try:
            progress_step = self._health_text(agent_receipt.get("step") or selected_plan, 128)
            previous_progress = self.latest_health_progress(incident_id, selected_plan)
            if previous_progress is not None and all(
                previous_progress.get(field) == expected
                for field, expected in (
                    ("step", progress_step),
                    ("evidence_refs", evidence_refs),
                    ("progress_fingerprint", progress_fingerprint),
                )
            ):
                progress = {"idempotent": True, "payload": previous_progress}
            else:
                progress = self.record_health_progress(
                    incident_id,
                    progress_key,
                    selected_plan,
                    progress_step,
                    evidence_refs,
                    progress_fingerprint,
                    progress_step.strip().lower() in {"heartbeat", "keepalive", "heartbeat-only"},
                    "agent-herder",
                )
            if self._health_text(agent_receipt.get("observed_state") or "", 32) == "degraded":
                outcome = {
                    "plan_id": selected_plan,
                    "observed_state": "degraded",
                    "step": progress_step,
                    "progress_fingerprint": progress_fingerprint,
                    "evidence_refs": evidence_refs,
                    "trace_refs": trace_refs[:16],
                }
                with self._lock, self._connection:
                    outcome_delivery_id = self._schedule_delivery(
                        incident_id,
                        "telegram.main",
                        f"health.remediation_degraded:{selected_plan}:{progress_fingerprint}",
                        time.time(),
                        {"health_outcome": outcome},
                    )
                return {
                    "accepted": True,
                    "resolved": False,
                    "reason": "selected remediation plan completed; source remains degraded",
                    "progress": progress,
                    "outcome_delivery_id": outcome_delivery_id,
                }
            verification = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["verification"])
            verification_actor = self._health_text(verification.get("actor") or "", 128) if isinstance(verification, Mapping) else ""
            if not isinstance(verification, Mapping) or not verification_actor:
                return {
                    "accepted": False,
                    "resolved": False,
                    "reason": "an independent health verification receipt is required before remediation can resolve",
                    "progress": progress,
                }
            if not self._health_verification_is_independent(verification):
                raise ValidationError("health verification must come from an independent actor")

            source_id = self._health_text(verification.get("source_id") or "", 128)
            expected_source_id = self._health_text(health_context.get("source_id") or "", 128)
            source_fingerprint = self._health_text(verification.get("fingerprint") or "", 128)
            expected_fingerprint = self._health_text(health_context.get("source_fingerprint") or "", 128)
            verifier_id = self._health_text(verification.get("verifier_id") or "", 128)
            verification_id = self._health_text(verification.get("verification_id") or "", 128)
            receipt_source_id = self._health_text(agent_receipt.get("source_id") or "", 128)
            receipt_fingerprint = self._health_text(agent_receipt.get("source_fingerprint") or agent_receipt.get("fingerprint") or "", 128)
            receipt_verification_id = self._health_text(agent_receipt.get("verification_id") or "", 128)
            receipt_verifier_id = self._health_text(agent_receipt.get("verifier_id") or agent_receipt.get("verification_source_id") or "", 128)
            if not source_id or not verifier_id or not verification_id or not bool(verification.get("healthy")):
                raise ValidationError("independent health verification receipt is incomplete or unhealthy")
            if receipt_source_id and receipt_source_id != source_id:
                raise ValidationError("remediation receipt source does not match the independent verification")
            if receipt_fingerprint and receipt_fingerprint != source_fingerprint:
                raise ValidationError("remediation receipt fingerprint does not match the independent verification")
            if receipt_verification_id and receipt_verification_id != verification_id:
                raise ValidationError("remediation receipt verification does not match the independent verification")
            if receipt_verifier_id and receipt_verifier_id != verifier_id:
                raise ValidationError("remediation receipt verifier does not match the independent verification")
            if expected_source_id and source_id != expected_source_id:
                raise ValidationError("health verification source must match the original source")
            if expected_fingerprint and source_fingerprint != expected_fingerprint:
                raise ValidationError("health verification fingerprint must match the original source")
            try:
                elapsed_ms = max(0, min(86_400_000, int(receipt.get("elapsed_ms") or 0)))
            except (TypeError, ValueError):
                elapsed_ms = 0
            resolved = self.resolve_health_incident(
                incident_id,
                source_id,
                verification_id,
                "agent-herder",
                elapsed_ms=elapsed_ms,
                trace_refs=trace_refs,
            )
            return {"accepted": True, "resolved": True, "progress": progress, "verification": verification, "resolved_incident": resolved}
        except ValidationError as error:
            return {"accepted": False, "resolved": False, "reason": self._health_text(error, 256), "progress": locals().get("progress")}

    def record_agent_job_result(self, incident_id: str, delivery_id: str, job_name: str, receipt: Mapping[str, Any], health_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Persist a bounded terminal agent-job receipt without raw command output."""
        if self.get_incident(incident_id) is None:
            raise ValidationError("incident not found")
        result = receipt.get("agent_receipt") if isinstance(receipt.get("agent_receipt"), Mapping) else {}
        context = health_context if isinstance(health_context, Mapping) else {}
        try:
            elapsed_ms = max(0, min(86_400_000, int(receipt.get("elapsed_ms") or 0)))
        except (TypeError, ValueError):
            elapsed_ms = 0
        summary = {
            "delivery_id": self._health_text(delivery_id, 128),
            "agent_job": self._health_text(job_name, 128),
            "hub_job_id": self._health_text(receipt.get("job_id") or "", 128),
            "route_id": self._health_text(receipt.get("route_id") or "", 128),
            "status": self._health_text(receipt.get("status") or "", 32),
            "session_id": self._health_text(result.get("session_id") or result.get("sessionId") or "", 128),
            "created": result.get("created") is True,
            "delivery": self._health_text(result.get("delivery") or "", 32),
            "elapsed_ms": elapsed_ms,
            "correlation_id": self._health_text(context.get("correlation_id") or result.get("correlation_id") or "", 128),
            "trace_refs": [self._health_text(item, 128) for item in (context.get("trace_refs") if isinstance(context.get("trace_refs"), list) else result.get("trace_refs", []))[:16]],
            "evidence_refs": [self._health_text(item, 128) for item in (result.get("evidence_refs") if isinstance(result.get("evidence_refs"), list) else [])[:16]],
            "source_id": self._health_text(result.get("source_id") or "", 128),
            "source_fingerprint": self._health_text(result.get("source_fingerprint") or "", 128),
            "verifier_id": self._health_text(result.get("verifier_id") or "", 128),
            "verification_id": self._health_text(result.get("verification_id") or "", 128),
            "observed_state": self._health_text(result.get("observed_state") or "", 32),
        }
        selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        execution = selection.get("execution") if isinstance(selection, Mapping) else {}
        if isinstance(selection, Mapping):
            summary["plan_id"] = self._health_text(selection.get("plan_id") or result.get("plan_id") or "", 64)
        if isinstance(execution, Mapping):
            summary["model"] = self._health_text(execution.get("model") or result.get("model") or "", 128)
            summary["reasoning"] = self._health_text(execution.get("reasoning") or result.get("reasoning") or "", 16)
            summary["topic"] = self._health_text(execution.get("topic") or result.get("topic") or "", 64)
        with self._lock, self._connection:
            event_type = "agent_job_failed" if summary["status"] == "failed" else "agent_job_completed"
            self._audit(incident_id, event_type, "worker", summary)
        if job_name == HEALTH_REMEDIATION_AGENT_JOB and summary["status"] == "completed" and isinstance(health_context, Mapping):
            workflow_result = self._record_health_remediation_receipt(incident_id, delivery_id, receipt, health_context)
            if not workflow_result.get("accepted"):
                with self._lock, self._connection:
                    self._audit(
                        incident_id,
                        "health.remediation_receipt_rejected",
                        "worker",
                        {"delivery_id": delivery_id, "reason": self._health_text(workflow_result.get("reason") or "unknown", 256)},
                    )
            return workflow_result
        return {"accepted": True, "resolved": False, "health_workflow": False}

    @staticmethod
    def _health_text(value: Any, limit: int = 256) -> str:
        from .health_workflow import sanitize_bounded_text

        return sanitize_bounded_text(value, limit)

    @classmethod
    def _health_execution(cls, value: Any) -> dict[str, str]:
        """Return only the canonical, bounded remediation execution profile."""
        from .health_workflow import normalize_health_execution

        try:
            execution = normalize_health_execution(value)
        except ValidationError:
            return {}
        return {key: cls._health_text(item, 64) for key, item in execution.items()}

    @classmethod
    def _health_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "[truncated]"
        if isinstance(value, Mapping):
            return {str(key): cls._health_value(nested, depth + 1) for key, nested in value.items() if not any(term in str(key).lower() for term in ("secret", "token", "password", "credential", "authorization"))}
        if isinstance(value, list):
            return [cls._health_value(item, depth + 1) for item in value[:10]]
        if isinstance(value, str):
            return cls._health_text(value, 256)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return cls._health_text(value, 256)

    @classmethod
    def _health_payload(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in payload.items():
            safe_key = str(key)
            if safe_key == "execution":
                result[safe_key] = cls._health_execution(value)
            elif safe_key == "plans" and isinstance(value, list):
                result[safe_key] = [
                    {
                        "plan_id": cls._health_text(item.get("plan_id") or item.get("id") or "", 64),
                        "title": cls._health_text(item.get("title") or "", 128),
                        "summary": cls._health_text(item.get("summary") or item.get("body") or "", 256),
                        "step": cls._health_text(item.get("step") or "", 64),
                        "execution": cls._health_execution(item.get("execution")),
                    }
                    for item in value[:3]
                    if isinstance(item, Mapping)
                ]
            else:
                result[safe_key] = cls._health_value(value)
        return result

    def _health_event_row(self, incident_id: str, event_type: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM events WHERE incident_id = ? AND event_type = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (incident_id, event_type),
        ).fetchone()

    def _health_event_payload(self, incident_id: str, event_type: str) -> dict[str, Any] | None:
        row = self._health_event_row(incident_id, event_type)
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _health_original_event(self, incident_id: str) -> dict[str, Any] | None:
        incident = self.get_incident(incident_id)
        if incident is None:
            return None
        if not str(incident.get("event_type") or "").startswith("health."):
            return None
        row = self._connection.execute(
            "SELECT payload_json FROM events WHERE incident_id = ? ORDER BY created_at ASC, rowid ASC LIMIT 1",
            (incident_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def health_incident_is_synthetic(self, incident_id: str) -> bool:
        """Classify synthetic health incidents from their persisted source event."""
        original = self._health_original_event(incident_id)
        return isinstance(original, Mapping) and str(original.get("correlation_id") or "").startswith(_SYNTHETIC_HEALTH_CORRELATION_PREFIX)

    def _health_plan_candidates(self, incident_id: str) -> list[str]:
        attached = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["plans"])
        if attached is not None:
            plans = attached.get("plans")
            if isinstance(plans, list):
                result: list[str] = []
                for plan in plans:
                    if isinstance(plan, Mapping):
                        candidate = str(plan.get("plan_id") or plan.get("id") or "").strip()
                        if candidate:
                            result.append(candidate)
                if result:
                    return result
        # Plans are a post-diagnosis artifact.  Never let a caller select one
        # of the old generic defaults before OmniRoute attached the diagnosis.
        return []

    def _health_plan_details(self, incident_id: str, plan_id: str) -> dict[str, Any]:
        attached = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["plans"])
        plans = attached.get("plans") if isinstance(attached, Mapping) else None
        if isinstance(plans, list):
            for plan in plans:
                if isinstance(plan, Mapping) and str(plan.get("plan_id") or plan.get("id") or "").strip() == plan_id:
                    return {
                        "plan_id": plan_id,
                        "execution": self._health_execution(plan.get("execution")),
                    }
        raise ValidationError("unknown health plan")

    def _health_remediation_delivery_id(self, incident_id: str, plan_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT id FROM deliveries WHERE delivery_key = ?",
            (f"{incident_id}:gptadmin.agent:{HEALTH_REMEDIATION_AGENT_JOB}:plan:{plan_id}",),
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def _health_selected_plan(self, incident_id: str) -> str | None:
        payload = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["plan_selection"])
        if payload is None:
            return None
        plan_id = str(payload.get("plan_id") or "").strip()
        return plan_id or None

    def latest_health_progress(self, incident_id: str, plan_id: str) -> dict[str, Any] | None:
        """Return the latest health progress receipt for one plan."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM events WHERE incident_id = ? AND event_type = ? ORDER BY created_at DESC, rowid DESC",
                (incident_id, HEALTH_UPDATE_EVENT_TYPES["progress"]),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and str(payload.get("plan_id") or "").strip() == plan_id:
                return payload
        return None

    def _health_progress_useful(self, incident_id: str, plan_id: str, step: str, evidence_refs: list[str], progress_fingerprint: str) -> bool:
        previous = self.latest_health_progress(incident_id, plan_id)
        if previous is None:
            return True
        return any(previous.get(field) != value for field, value in (("step", step), ("evidence_refs", evidence_refs), ("progress_fingerprint", progress_fingerprint))
        )

    def _record_health_event(
        self,
        incident_id: str,
        idempotency_key: str,
        event_type: str,
        payload: Mapping[str, Any],
        actor: str,
        *,
        useful: bool | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        incident = self.get_incident(incident_id)
        if incident is None:
            raise ValidationError("incident not found")
        now = time.time()
        safe_payload = self._health_payload(payload)
        payload_json = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connection:
            previous = self._connection.execute(
                "SELECT event_id, payload_json FROM events WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different event content")
                previous_payload = self._health_payload(json.loads(str(previous["payload_json"])) if str(previous["payload_json"]) else {})
                return {"event_id": previous["event_id"], "incident_id": incident_id, "idempotent": True, "useful": bool(previous_payload.get("useful")) if isinstance(previous_payload, dict) else False, "payload": previous_payload}
            event_id = f"evt_{uuid.uuid4().hex}"
            event_type_value = event_type[:128]
            self._connection.execute(
                "INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, peer_ip, source_ip, proxy_ip, forwarded_for) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (idempotency_key, event_id, incident_id, payload_json, now, event_type_value, safe_payload.get("producer"), safe_payload.get("plugin"), safe_payload.get("correlation_id"), safe_payload.get("parent_event_id"), safe_payload.get("parent_incident_id"), safe_payload.get("peer_ip"), safe_payload.get("source_ip"), safe_payload.get("proxy_ip"), safe_payload.get("forwarded_for")),
            )
            audit_payload = dict(safe_payload)
            if useful is not None:
                audit_payload["useful"] = useful
            self._audit(incident_id, event_type_value, actor, audit_payload)
            result = {"event_id": event_id, "incident_id": incident_id, "idempotent": False, "payload": safe_payload}
            if useful is not None:
                result["useful"] = useful
            return result

    def record_health_update(self, incident_id: str, idempotency_key: str, event_type: str, payload: Mapping[str, Any], actor: str = "worker") -> dict[str, Any]:
        """Persist a bounded health update in the existing event and audit tables."""
        return self._record_health_event(incident_id, idempotency_key, event_type, payload, actor, useful=payload.get("useful_progress") if isinstance(payload, Mapping) else None)

    def record_health_plan_selection(self, incident_id: str, plan_id: str, actor: str, idempotency_key: str) -> dict[str, Any]:
        """Persist one choice and queue one bounded remediation request."""
        with self._lock:
            safe_plan = self._health_text(plan_id, 64)
            if safe_plan not in self._health_plan_candidates(incident_id):
                raise ValidationError("unknown health plan")
            plan_details = self._health_plan_details(incident_id, safe_plan)
            selected = self._health_selected_plan(incident_id)
            if selected is not None:
                if selected != safe_plan:
                    raise ValidationError("health plan already selected")
                payload = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["plan_selection"]) or {"plan_id": safe_plan}
                return {
                    "event_id": None,
                    "incident_id": incident_id,
                    "idempotent": True,
                    "plan_id": safe_plan,
                    "useful": False,
                    "payload": payload,
                    "remediation_delivery_id": self._health_remediation_delivery_id(incident_id, safe_plan),
                }
            try:
                # The process lock is not enough when two HTTP workers have
                # separate NotificationCenter instances.  The primary key is
                # the durable single-winner gate across those processes.
                with self._connection:
                    self._connection.execute(
                        "INSERT INTO health_plan_selections(incident_id, plan_id, idempotency_key, created_at) VALUES (?, ?, ?, ?)",
                        (incident_id, safe_plan, idempotency_key, time.time()),
                    )
                    result = self._record_health_event(
                        incident_id,
                        idempotency_key,
                        HEALTH_UPDATE_EVENT_TYPES["plan_selection"],
                        {"plan_id": safe_plan, "actor": actor, "execution": plan_details["execution"]},
                        actor,
                        useful=True,
                    )
                    remediation = self._record_health_event(
                        incident_id,
                        f"{idempotency_key}:remediation",
                        HEALTH_UPDATE_EVENT_TYPES["remediation"],
                        {
                            "plan_id": safe_plan,
                            "actor": actor,
                            "agent_job": HEALTH_REMEDIATION_AGENT_JOB,
                            "execution": plan_details["execution"],
                        },
                        actor,
                        useful=True,
                    )
                    remediation_delivery_id = self._schedule_delivery(
                        incident_id,
                        f"gptadmin.agent:{HEALTH_REMEDIATION_AGENT_JOB}",
                        f"plan:{safe_plan}",
                        time.time(),
                    )
                    self._connection.execute(
                        "UPDATE health_plan_selections SET event_id = ? WHERE incident_id = ?",
                        (result.get("event_id"), incident_id),
                    )
            except sqlite3.IntegrityError as error:
                winner = self._connection.execute(
                    "SELECT plan_id FROM health_plan_selections WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                winning_plan = str(winner["plan_id"] if winner is not None else "")
                if winning_plan == safe_plan:
                    payload = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["plan_selection"]) or {"plan_id": safe_plan}
                    return {
                        "event_id": None,
                        "incident_id": incident_id,
                        "idempotent": True,
                        "plan_id": safe_plan,
                        "useful": False,
                        "payload": payload,
                        "remediation_delivery_id": self._health_remediation_delivery_id(incident_id, safe_plan),
                    }
                raise ValidationError("health plan already selected") from error
            result["plan_id"] = safe_plan
            result["useful"] = True
            result["remediation_event_id"] = remediation.get("event_id")
            result["remediation_delivery_id"] = remediation_delivery_id
            return result

    def select_health_plan(self, incident_id: str, idempotency_key: str, plan_id: str, actor: str) -> dict[str, Any]:
        """Alias for canonical idempotency-key-first callers."""
        return self.record_health_plan_selection(incident_id, plan_id, actor, idempotency_key)

    def record_health_progress(self, incident_id: str, *args: Any) -> dict[str, Any]:
        """Persist only useful health progress receipts.

        Supported call patterns:
        - (actor, progress_mapping, idempotency_key)
        - (idempotency_key, plan_id, step, evidence_refs, progress_fingerprint, heartbeat_at, actor)
        """
        if len(args) == 3 and isinstance(args[1], Mapping):
            actor = str(args[0])
            progress = dict(args[1])
            idempotency_key = str(args[2])
            plan_id = progress.get("plan_id") or ""
            step = progress.get("step") or ""
            evidence_value = progress.get("evidence") or progress.get("evidence_refs") or []
            fingerprint = progress.get("fingerprint") or progress.get("progress_fingerprint") or ""
            heartbeat = bool(progress.get("heartbeat") or progress.get("heartbeat_at"))
        elif len(args) == 7:
            idempotency_key = str(args[0])
            plan_id = args[1]
            step = args[2]
            evidence_value = args[3]
            fingerprint = args[4]
            heartbeat = args[5]
            actor = str(args[6])
        else:
            raise TypeError("record_health_progress expects either (actor, progress, idempotency_key) or (idempotency_key, plan_id, step, evidence_refs, progress_fingerprint, heartbeat_at, actor)")
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        safe_plan = self._health_text(plan_id or "", 64)
        if safe_plan not in self._health_plan_candidates(incident_id):
            raise ValidationError("unknown health plan")
        if self._health_selected_plan(incident_id) != safe_plan:
            raise ValidationError("health progress requires the selected plan")
        safe_step = self._health_text(step or "", 128)
        safe_evidence = self._health_value(evidence_value or [])
        if isinstance(safe_evidence, str):
            evidence_refs = [self._health_text(safe_evidence, 128)] if self._health_text(safe_evidence, 128) else []
        elif isinstance(safe_evidence, list):
            evidence_refs = [self._health_text(item, 128) for item in safe_evidence[:10] if self._health_text(item, 128)]
        else:
            bounded_evidence = self._health_text(safe_evidence, 128)
            evidence_refs = [bounded_evidence] if bounded_evidence else []
        safe_fingerprint = self._health_text(fingerprint or "", 128)
        has_progress_content = bool(safe_step or evidence_refs or safe_fingerprint)
        heartbeat_label = safe_step.strip().lower() in {"heartbeat", "keepalive", "heartbeat-only"}
        heartbeat_only = not evidence_refs and heartbeat_label
        if not has_progress_content:
            raise ValidationError("health progress requires a non-empty step, evidence, or fingerprint")
        if heartbeat_only:
            raise ValidationError("heartbeat-only health progress is not accepted")
        with self._lock:
            existing = self._connection.execute(
                "SELECT event_id, event_type, payload_json FROM events WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            try:
                existing_payload = json.loads(str(existing["payload_json"] or "{}"))
            except json.JSONDecodeError:
                existing_payload = {}
            if (
                existing["event_type"] != HEALTH_UPDATE_EVENT_TYPES["progress"]
                or not isinstance(existing_payload, dict)
                or str(existing_payload.get("plan_id") or "") != safe_plan
                or str(existing_payload.get("step") or "") != safe_step
                or list(existing_payload.get("evidence_refs") or []) != evidence_refs
                or str(existing_payload.get("progress_fingerprint") or existing_payload.get("fingerprint") or "") != safe_fingerprint
                or bool(existing_payload.get("heartbeat") in (True, 1, "true", "True", "1")) != bool(heartbeat)
            ):
                raise IdempotencyConflict("Idempotency-Key was already used with different health progress content")
            stored_useful = existing_payload.get("useful") in (True, 1, "true", "True", "1") or existing_payload.get("useful_progress") in (True, 1, "true", "True", "1")
            return {
                "event_id": existing["event_id"],
                "incident_id": incident_id,
                "idempotent": True,
                "payload": existing_payload,
                "useful": stored_useful,
                "useful_progress": stored_useful,
            }
        useful = self._health_progress_useful(incident_id, safe_plan, safe_step, evidence_refs, safe_fingerprint)
        payload = {
            "plan_id": safe_plan,
            "step": safe_step,
            "evidence": evidence_refs[0] if len(evidence_refs) == 1 else evidence_refs,
            "evidence_refs": evidence_refs,
            "fingerprint": safe_fingerprint,
            "progress_fingerprint": safe_fingerprint,
            "heartbeat": bool(heartbeat),
            "useful": useful,
            "useful_progress": useful,
        }
        result = self._record_health_event(
            incident_id,
            idempotency_key,
            HEALTH_UPDATE_EVENT_TYPES["progress"],
            payload,
            actor,
            useful=useful,
        )
        result["useful"] = useful
        result["useful_progress"] = useful
        return result

    def record_health_verification(self, incident_id: str, *args: Any) -> dict[str, Any]:
        """Persist an independent source verification receipt.

        Supported call patterns:
        - (actor, verification_mapping, idempotency_key)
        - (idempotency_key, source_id, verifier_id, observed_state, evidence_refs, actor)
        """
        if len(args) == 3 and isinstance(args[1], Mapping):
            actor = str(args[0])
            verification = dict(args[1])
            idempotency_key = str(args[2])
            source_id = verification.get("source_id") or ""
            verifier_id = verification.get("verifier_id") or verification.get("source_id") or ""
            verification_id = verification.get("verification_id") or verifier_id
            observed_state = "healthy" if verification.get("healthy") else "degraded"
            fingerprint = verification.get("fingerprint") or verification.get("progress_fingerprint") or ""
            evidence_value = verification.get("evidence") or verification.get("evidence_refs") or []
        elif len(args) == 6:
            idempotency_key = str(args[0])
            source_id = args[1]
            verifier_id = args[2]
            observed_state = args[3]
            evidence_value = args[4]
            actor = str(args[5])
            fingerprint = ""
            verification_id = verifier_id
        else:
            raise TypeError("record_health_verification expects either (actor, verification, idempotency_key) or (idempotency_key, source_id, verifier_id, observed_state, evidence_refs, actor)")
        source_id = self._health_text(source_id or "", 128)
        verifier_id = self._health_text(verifier_id or "", 128)
        safe_actor = self._health_text(actor or "", 128)
        if not source_id or not verifier_id:
            raise ValidationError("health verification requires source and verifier identities")
        if source_id == verifier_id:
            raise ValidationError("health verification must be independent")
        if not safe_actor or safe_actor.lower() in _HEALTH_REMEDIATION_ACTORS:
            raise ValidationError("health verification must come from an independent actor")
        state = self._health_text(observed_state or "", 32).lower()
        if state not in {"healthy", "degraded"}:
            raise ValidationError("verification observed_state must be healthy or degraded")
        safe_evidence = self._health_value(evidence_value or [])
        if isinstance(safe_evidence, str):
            evidence_refs = [self._health_text(safe_evidence, 128)]
        elif isinstance(safe_evidence, list):
            evidence_refs = [self._health_text(item, 128) for item in safe_evidence[:10]]
        else:
            evidence_refs = [self._health_text(safe_evidence, 128)]
        payload = {
            "actor": safe_actor,
            "source_id": source_id,
            "verifier_id": verifier_id,
            "verification_id": self._health_text(verification_id or "", 128),
            "healthy": state == "healthy",
            "observed_state": state,
            "fingerprint": self._health_text(fingerprint or "", 128),
            "evidence": evidence_refs[0] if len(evidence_refs) == 1 else evidence_refs,
            "evidence_refs": evidence_refs,
        }
        result = self._record_health_event(
            incident_id,
            idempotency_key,
            HEALTH_UPDATE_EVENT_TYPES["verification"],
            payload,
            actor,
            useful=state == "healthy",
        )
        result["healthy"] = state == "healthy"
        result["observed_state"] = state
        result["source_id"] = source_id
        result["verifier_id"] = verifier_id
        return result

    def resolve_health_incident(self, incident_id: str, source_id: str, verification_id: str, actor: str, elapsed_ms: int | None = None, trace_refs: list[str] | None = None) -> dict[str, Any]:
        """Resolve only when the original source has a matching healthy receipt."""
        current = self.get_incident(incident_id)
        if current is None:
            raise ValidationError("incident not found")
        if self._health_original_event(incident_id) is None:
            raise ValidationError("health intake receipt is required before resolution")
        selected_plan = self._health_selected_plan(incident_id)
        if not selected_plan:
            raise ValidationError("health incident requires explicit plan selection before resolution")
        if not self._health_useful_progress_exists(incident_id, selected_plan):
            raise ValidationError("health incident requires useful progress for the selected plan before resolution")
        original = self._health_original_event(incident_id) or {}
        original_source = self._health_text(original.get("source_id") or original.get("producer") or "", 128)
        if original_source and self._health_text(source_id or "", 128) != original_source:
            raise ValidationError("health verification source must match the original source")
        verification = self._health_event_payload(incident_id, HEALTH_UPDATE_EVENT_TYPES["verification"])
        if verification is None:
            raise ValidationError("health verification receipt is required before resolution")
        if self._health_text(verification.get("source_id") or "", 128) != self._health_text(source_id or "", 128):
            raise ValidationError("health verification receipt must match the original source")
        if self._health_text(verification.get("verification_id") or "", 128) != self._health_text(verification_id or "", 128):
            raise ValidationError("health verification receipt does not match the requested verification")
        if not bool(verification.get("healthy")):
            raise ValidationError("health verification receipt must report healthy")
        if not self._health_verification_is_independent(verification):
            raise ValidationError("health verification receipt must come from an independent actor")
        if not self._healthy_verification_matches(incident_id):
            raise ValidationError("health verification receipt must match the original source fingerprint and remain independent")
        if current["state"] == "resolved":
            return current
        original = self._health_original_event(incident_id) or {}
        correlation_id = self._health_text(original.get("correlation_id") or current.get("correlation_id") or "", 256)
        resolved_trace_refs: list[str] = []
        for values in (trace_refs, original.get("evidence_refs"), verification.get("evidence_refs")):
            if not isinstance(values, list):
                continue
            for value in values[:16]:
                bounded = self._health_text(value, 128)
                if bounded and bounded not in resolved_trace_refs:
                    resolved_trace_refs.append(bounded)
        resolved_trace_refs = resolved_trace_refs[:16]
        resolved_payload = {
            "source_id": source_id,
            "verification_id": verification_id,
            "resolved_by": actor,
            "elapsed_ms": elapsed_ms if elapsed_ms is not None else 0,
            "trace_refs": resolved_trace_refs,
        }
        if correlation_id:
            resolved_payload["correlation_id"] = correlation_id
        self._record_health_event(
            incident_id,
            f"{incident_id}:health.resolved:{verification_id}",
            "health.resolved",
            resolved_payload,
            actor,
            useful=True,
        )
        resolved = self._transition(incident_id, "resolved", actor)
        with self._lock, self._connection:
            resolved_delivery_id = self._schedule_delivery(incident_id, "telegram.main", "health.resolved", time.time())
        return {**resolved, "resolved_delivery_id": resolved_delivery_id}

    def _healthy_verification_matches(self, incident_id: str) -> bool:
        incident = self.get_incident(incident_id)
        if incident is None or not str(incident.get("event_type") or "").startswith("health."):
            return True
        original = self._health_original_event(incident_id)
        if not isinstance(original, dict):
            return False
        source_id = str(original.get("source_id") or original.get("producer") or "").strip()
        fingerprint = str(original.get("source_fingerprint") or "").strip()
        rows = self._connection.execute(
            "SELECT payload_json FROM events WHERE incident_id = ? AND event_type = ? ORDER BY created_at DESC, rowid DESC",
            (incident_id, HEALTH_UPDATE_EVENT_TYPES["verification"]),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if not bool(payload.get("healthy")):
                continue
            if source_id and str(payload.get("source_id") or "").strip() != source_id:
                continue
            if fingerprint and str(payload.get("fingerprint") or "").strip() != fingerprint:
                continue
            if not self._health_verification_is_independent(payload):
                continue
            return True
        return False

    def _health_verification_is_independent(self, verification: Mapping[str, Any]) -> bool:
        """Apply one fail-closed authority predicate to every resolution path."""
        source_id = self._health_text(verification.get("source_id") or "", 128)
        verifier_id = self._health_text(verification.get("verifier_id") or "", 128)
        verification_id = self._health_text(verification.get("verification_id") or "", 128)
        actor = self._health_text(verification.get("actor") or "", 128)
        return bool(source_id and verifier_id and verification_id and actor) and source_id != verifier_id and actor.lower() not in _HEALTH_REMEDIATION_ACTORS

    def _require_health_resolution_gate(self, incident_id: str) -> None:
        incident = self.get_incident(incident_id)
        if incident is None:
            raise ValidationError("incident not found")
        if not str(incident.get("event_type") or "").startswith("health."):
            return
        selected_plan = self._health_selected_plan(incident_id)
        if not selected_plan:
            raise ValidationError("health incident requires explicit plan selection before resolution")
        if not self._health_useful_progress_exists(incident_id, selected_plan):
            raise ValidationError("health incident requires useful progress for the selected plan before resolution")
        if not self._healthy_verification_matches(incident_id):
            raise ValidationError("health incident requires independent healthy verification before resolution")

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Return the current incident record, or None when it has never existed."""
        with self._lock:
            return self._row(self._connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone())

    def list_incidents(self) -> list[dict[str, Any]]:
        """Return incidents newest first for the initial inbox/API implementation."""
        with self._lock:
            return [dict(row) for row in self._connection.execute("SELECT * FROM incidents ORDER BY updated_at DESC").fetchall()]

    @classmethod
    def _history_safe_value(cls, value: Any, depth: int = 0) -> Any:
        """Bound and redact audit values before exposing them to the operator UI."""
        if depth >= 5:
            return "[truncated]"
        if isinstance(value, Mapping):
            return {
                str(key): cls._history_safe_value(nested, depth + 1)
                for key, nested in value.items()
                if not any(word in str(key).lower() for word in ("secret", "token", "password", "credential", "authorization"))
            }
        if isinstance(value, list):
            return [cls._history_safe_value(item, depth + 1) for item in value[:50]]
        if isinstance(value, str):
            return value[:2000]
        return value

    @classmethod
    def _history_payload(cls, raw: str | None) -> Any:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            value = {"value": raw or ""}
        return cls._history_safe_value(value)

    def list_event_history(self, limit: int = 100, query: str | None = None) -> list[dict[str, Any]]:
        """Return a bounded event-to-outcome history for the protected operator UI."""
        bounded_limit = min(max(int(limit), 1), 500)
        terms = [str(query or "").strip().lower()]
        params: list[Any] = []
        where = ""
        if terms[0]:
            where = "WHERE lower(COALESCE(e.event_type, i.kind, '')) LIKE ? OR lower(COALESCE(e.producer, '')) LIKE ? OR lower(COALESCE(e.plugin, '')) LIKE ? OR lower(COALESCE(e.correlation_id, '')) LIKE ? OR lower(i.id) LIKE ? OR lower(i.title) LIKE ?"
            params = [f"%{terms[0]}%"] * 6
        with self._lock:
            rows = self._connection.execute(
                f"SELECT e.event_id, e.incident_id, e.created_at AS event_created_at, e.event_type, e.producer, e.plugin, e.correlation_id, e.parent_event_id, e.parent_incident_id, e.peer_ip, e.source_ip, e.proxy_ip, e.forwarded_for, i.project, i.recipient, i.kind, i.severity, i.title, i.state, i.occurrences, i.updated_at FROM events e JOIN incidents i ON i.id = e.incident_id {where} ORDER BY e.created_at DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
            history: list[dict[str, Any]] = []
            for row in rows:
                incident_id = str(row["incident_id"])
                children = self._connection.execute(
                    "SELECT i.id AS incident_id, i.event_type, i.title, i.state, i.updated_at, e.event_id FROM incidents i LEFT JOIN events e ON e.incident_id = i.id WHERE i.parent_incident_id = ? GROUP BY i.id ORDER BY i.created_at",
                    (incident_id,),
                ).fetchall()
                deliveries = self._connection.execute(
                    "SELECT id, channel, status, attempt, last_error, due_at, created_at, updated_at, result_json FROM deliveries WHERE incident_id = ? ORDER BY created_at",
                    (incident_id,),
                ).fetchall()
                audit = self._connection.execute(
                    "SELECT type, actor, payload_json, created_at FROM audit_events WHERE incident_id = ? ORDER BY created_at",
                    (incident_id,),
                ).fetchall()
                notification_rows = [
                    {
                        "delivery_id": str(delivery["id"]),
                        "channel": str(delivery["channel"]),
                        "status": str(delivery["status"]),
                        "attempt": int(delivery["attempt"] or 0),
                        "last_error": delivery["last_error"],
                        "due_at": delivery["due_at"],
                        "created_at": delivery["created_at"],
                        "updated_at": delivery["updated_at"],
                        "result": self._history_payload(delivery["result_json"]),
                    }
                    for delivery in deliveries
                ]
                counts: dict[str, int] = {}
                for item in notification_rows:
                    counts[item["status"]] = counts.get(item["status"], 0) + 1
                audit_items = [
                    {"type": audit_row["type"], "actor": audit_row["actor"], "created_at": audit_row["created_at"], "payload": self._history_payload(audit_row["payload_json"])}
                    for audit_row in audit
                ]
                health_plans: list[dict[str, str]] = []
                for audit_item in reversed(audit_items):
                    if audit_item["type"] != "health.plans_attached" or not isinstance(audit_item["payload"], Mapping):
                        continue
                    raw_plans = audit_item["payload"].get("plans")
                    if not isinstance(raw_plans, list) or len(raw_plans) != 3:
                        break
                    projected_plans = [
                        {
                            "plan_id": str(plan.get("plan_id") or plan.get("id") or "").strip()[:64],
                            "title": str(plan.get("title") or plan.get("plan_id") or plan.get("id") or "").strip()[:128],
                        }
                        for plan in raw_plans[:3]
                        if isinstance(plan, Mapping)
                    ]
                    if (
                        len(projected_plans) == 3
                        and all(plan["plan_id"] and plan["title"] for plan in projected_plans)
                        and len({plan["plan_id"] for plan in projected_plans}) == 3
                    ):
                        health_plans = projected_plans
                    break
                history.append({
                    "event_id": str(row["event_id"]),
                    "incident_id": incident_id,
                    "project": str(row["project"]),
                    "recipient": str(row["recipient"]),
                    "event_type": str(row["event_type"] or row["kind"]),
                    "kind": str(row["kind"]),
                    "severity": str(row["severity"]),
                    "title": str(row["title"]),
                    "producer": row["producer"],
                    "plugin": row["plugin"],
                    "correlation_id": row["correlation_id"],
                    "parent_event_id": row["parent_event_id"],
                    "parent_incident_id": row["parent_incident_id"],
                    "peer_ip": row["peer_ip"],
                    "source_ip": row["source_ip"],
                    "proxy_ip": row["proxy_ip"],
                    "forwarded_for": row["forwarded_for"],
                    "state": str(row["state"]),
                    "occurrences": int(row["occurrences"]),
                    "event_created_at": row["event_created_at"],
                    "updated_at": row["updated_at"],
                    "children": [dict(child) for child in children],
                    "notifications": notification_rows,
                    "outcome": {"incident_state": str(row["state"]), "notification_counts": counts},
                    "health_plans": health_plans,
                    "audit": audit_items,
                })
            return history

    def _latest_choice_event(self, incident_id: str) -> dict[str, Any] | None:
        """Return the newest validated opaque choice presentation for an incident."""
        rows = self._connection.execute(
            "SELECT payload_json FROM events WHERE incident_id = ? ORDER BY created_at DESC",
            (incident_id,),
        ).fetchall()
        for row in rows:
            try:
                event = json.loads(str(row["payload_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(event, Mapping):
                continue
            request_id = str(event.get("choice_request_id") or "").strip()
            choices = event.get("choices")
            if not _CHOICE_REQUEST_ID_RE.fullmatch(request_id) or not isinstance(choices, list) or not 2 <= len(choices) <= 4:
                continue
            normalized: list[dict[str, str]] = []
            seen: set[str] = set()
            valid = True
            for choice in choices:
                if not isinstance(choice, Mapping) or set(choice) != {"choice_id", "label"}:
                    valid = False
                    break
                choice_id = str(choice.get("choice_id") or "").strip()
                label = choice.get("label")
                if not _CHOICE_ID_RE.fullmatch(choice_id) or not isinstance(label, str) or not 1 <= len(label.strip()) <= 128 or choice_id in seen:
                    valid = False
                    break
                seen.add(choice_id)
                normalized.append({"choice_id": choice_id, "label": label.strip()})
            if valid:
                return {"choice_request_id": request_id, "choices": normalized}
        return None

    def get_telegram_choice(self, incident_id: str, choice_id: str) -> dict[str, str]:
        """Resolve an opaque Telegram choice to its pending request identity."""
        with self._lock:
            if self.get_incident(incident_id) is None:
                raise ValidationError("incident not found")
            presentation = self._latest_choice_event(incident_id)
            if presentation is None:
                raise ValidationError("choice request not found")
            choice_ids = [str(item["choice_id"]) for item in presentation["choices"]]
            if choice_id.isdigit() and int(choice_id) < len(choice_ids):
                choice_id = choice_ids[int(choice_id)]
            if choice_id not in choice_ids:
                raise ValidationError("choice does not belong to incident")
            label = next(str(item["label"]) for item in presentation["choices"] if str(item["choice_id"]) == choice_id)
            return {"request_id": str(presentation["choice_request_id"]), "choice_id": choice_id, "label": label}

    def record_telegram_choice(self, incident_id: str, choice_id: str, actor: str, request_id: str, status: str) -> None:
        """Audit a choice callback without persisting executable goal text."""
        if not _CHOICE_REQUEST_ID_RE.fullmatch(request_id) or not _CHOICE_ID_RE.fullmatch(choice_id):
            raise ValidationError("choice callback identity is invalid")
        with self._lock, self._connection:
            self._audit(
                incident_id,
                "telegram_choice_selected",
                actor,
                {"choice_id": choice_id, "request_id": request_id, "status": status[:64]},
            )

    def delivery_payload(self, delivery: Mapping[str, Any]) -> dict[str, Any]:
        """Build the safe adapter payload for a previously claimed delivery."""
        incident = self.get_incident(str(delivery["incident_id"]))
        if incident is None:
            raise ValidationError("delivery references missing incident")
        payload = {"delivery": dict(delivery), "incident": incident}
        choice_presentation = self._latest_choice_event(str(incident["id"]))
        if choice_presentation is not None:
            payload.update(choice_presentation)
        target_json = delivery.get("target_json")
        if isinstance(target_json, str):
            try:
                target = json.loads(target_json)
            except json.JSONDecodeError:
                target = None
            if isinstance(target, dict):
                payload["target"] = target
                if isinstance(target.get("health_outcome"), dict):
                    payload["health_outcome"] = self._health_value(target["health_outcome"])
        if str(incident.get("event_type") or "").startswith("health."):
            original = self._health_original_event(str(incident["id"]))
            if isinstance(original, dict):
                payload["health_context"] = {
                    "source_id": self._health_text(original.get("source_id") or original.get("producer") or "", 128),
                    "host_id": self._health_text(original.get("host_id") or original.get("plugin") or "", 128),
                    "signal_type": self._health_text(original.get("signal_type") or original.get("event_type") or "", 64),
                    "correlation_id": self._health_text(original.get("correlation_id") or "", 256),
                    "source_fingerprint": self._health_text(original.get("source_fingerprint") or "", 128),
                    "trace_refs": [
                        self._health_text(ref, 128)
                        for ref in (original.get("evidence_refs") if isinstance(original.get("evidence_refs"), list) else [])[:10]
                        if self._health_text(ref, 128)
                    ],
                }
            latest_plans = None if isinstance(payload.get("health_outcome"), dict) else self.latest_health_event(str(incident["id"]), "health.plans_attached")
            if latest_plans is not None:
                plan_payload = latest_plans["payload"]
                plans = plan_payload.get("plans")
                if isinstance(plans, list):
                    payload["health_plans"] = [
                        {
                            "plan_id": self._health_text(plan.get("plan_id") or plan.get("id") or "", 64),
                            "title": self._health_text(plan.get("title") or "", 128),
                            "summary": self._health_text(plan.get("summary") or "", 256),
                            "step": self._health_text(plan.get("step") or "", 64),
                        }
                        for plan in plans[:3]
                        if isinstance(plan, Mapping)
                    ]
                if isinstance(payload.get("health_context"), dict):
                    context = payload["health_context"]
                    if plan_payload.get("correlation_id"):
                        context["correlation_id"] = self._health_text(plan_payload.get("correlation_id"), 256)
                    trace_refs = list(context.get("trace_refs") or []) if isinstance(context.get("trace_refs"), list) else []
                    for trace_ref in plan_payload.get("trace_refs", []) if isinstance(plan_payload.get("trace_refs"), list) else []:
                        bounded = self._health_text(trace_ref, 128)
                        if bounded and bounded not in trace_refs:
                            trace_refs.append(bounded)
                    orchestration = plan_payload.get("orchestration")
                    if isinstance(orchestration, Mapping):
                        context["orchestration"] = self._health_value(orchestration)
                        for field in ("diagnosis_session_id", "orchestrator_session_id"):
                            bounded = self._health_text(orchestration.get(field), 128)
                            if bounded and bounded not in trace_refs:
                                trace_refs.append(bounded)
                    context["trace_refs"] = trace_refs[:16]
            latest_selection = self.latest_health_event(str(incident["id"]), HEALTH_UPDATE_EVENT_TYPES["plan_selection"])
            if latest_selection is not None:
                selection = latest_selection["payload"]
                execution = selection.get("execution") if isinstance(selection, Mapping) else None
                payload["health_selection"] = {
                    "plan_id": self._health_text(selection.get("plan_id") if isinstance(selection, Mapping) else "", 64),
                    "actor": self._health_text(selection.get("actor") if isinstance(selection, Mapping) else "", 128),
                    "execution": self._health_execution(execution),
                }
                if isinstance(payload.get("health_context"), dict):
                    payload["health_context"]["selection"] = payload["health_selection"]
        return payload

    def mark_dispatcher_healthy(self) -> None:
        """Record a local worker heartbeat used by public readiness, not liveness."""
        with self._lock:
            self._dispatcher_heartbeat = time.time()

    def get_runtime_setting(self, key: str, default: str | None = None) -> str | None:
        """Read one operator-controlled live setting without restarting the worker."""
        with self._lock:
            row = self._connection.execute("SELECT value FROM runtime_settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row is not None else default

    def set_runtime_setting(self, key: str, value: str) -> None:
        """Atomically persist one live setting for all current and future workers."""
        if not key or len(key) > 128:
            raise ValidationError("runtime setting key is invalid")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), time.time()),
            )

    def health(self) -> dict[str, Any]:
        """Probe durable dependencies and return only safe externally visible state."""
        storage_ready = False
        queued: int | None = None
        sending: int | None = None
        uncertain: int | None = None
        reconciliation_required: int | None = None
        try:
            with self._lock:
                self._connection.execute("SELECT 1").fetchone()
                queued = self._connection.execute("SELECT COUNT(*) AS count FROM deliveries WHERE status = 'queued'").fetchone()["count"]
                sending = self._connection.execute("SELECT COUNT(*) AS count FROM deliveries WHERE status = 'sending'").fetchone()["count"]
                uncertain = self._connection.execute("SELECT COUNT(*) AS count FROM deliveries WHERE status = 'uncertain'").fetchone()["count"]
                reconciliation_rows = self._connection.execute(
                    """SELECT incident_id FROM (
                        SELECT d.incident_id
                        FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                        WHERE i.event_type LIKE 'health.%' AND d.channel LIKE 'telegram.%' AND d.status IN ('sending', 'uncertain')
                        GROUP BY d.incident_id
                        UNION
                        SELECT d.incident_id
                        FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                        WHERE i.event_type LIKE 'health.%' AND d.channel = 'telegram.edit' AND d.status IN ('queued', 'claimed', 'sending', 'uncertain')
                        GROUP BY d.incident_id
                        UNION
                        SELECT d.incident_id
                        FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                        WHERE i.event_type LIKE 'health.%' AND d.channel LIKE 'telegram.%' AND d.status IN ('queued', 'claimed')
                          AND EXISTS (
                              SELECT 1
                              FROM deliveries sent
                              WHERE sent.incident_id = d.incident_id
                                AND sent.channel LIKE 'telegram.%'
                                AND sent.status = 'sent'
                          )
                        UNION
                        SELECT d.incident_id
                        FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                        WHERE i.event_type LIKE 'health.%' AND d.channel LIKE 'telegram.%' AND d.status = 'sent'
                        GROUP BY d.incident_id
                        HAVING COUNT(*) > 1
                        UNION
                        SELECT d.incident_id
                        FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                        WHERE i.event_type LIKE 'health.%' AND d.channel LIKE 'telegram.%' AND d.status = 'sent'
                          AND (
                              json_valid(COALESCE(d.result_json, '{}')) = 0
                              OR NOT (
                              COALESCE(CAST(json_extract(d.result_json, '$.message_id') AS INTEGER), 0) > 0
                              AND COALESCE(CAST(json_extract(d.result_json, '$.health_button_count') AS INTEGER), 0) = 3
                              AND COALESCE(CAST(json_extract(d.result_json, '$.health_signed_callback_count') AS INTEGER), 0) = 3
                              )
                          )
                        GROUP BY d.incident_id
                    ) AS reconciliation"""
                ).fetchall()
                reconciliation_incidents = {
                    str(row["incident_id"])
                    for row in reconciliation_rows
                    if not self.health_incident_is_synthetic(str(row["incident_id"]))
                }
                # The aggregate SQL above covers lifecycle collisions.  A
                # sent card is only safe when its receipt proves the exact
                # latest three-plan bundle; otherwise re-enabling Health
                # could expose a stale or non-actionable card.
                sent_rows = self._connection.execute(
                    """SELECT d.incident_id, d.id, d.result_json
                       FROM deliveries d JOIN incidents i ON i.id = d.incident_id
                       WHERE i.event_type LIKE 'health.%' AND d.channel LIKE 'telegram.%' AND d.status = 'sent'"""
                ).fetchall()
                invalid_sent_incidents: set[str] = set()
                for row in sent_rows:
                    incident_id = str(row["incident_id"])
                    if self.health_incident_is_synthetic(incident_id):
                        continue
                    attached_plan_ids = tuple(
                        str(plan.get("plan_id") or plan.get("id") or "").strip()
                        for plan in self.latest_health_plans(incident_id)
                        if isinstance(plan, Mapping) and str(plan.get("plan_id") or plan.get("id") or "").strip()
                    )
                    if not self._sent_health_delivery_matches_plans(row, attached_plan_ids):
                        invalid_sent_incidents.add(incident_id)
                reconciliation_required = len(
                    set(reconciliation_incidents) | invalid_sent_incidents
                )
                storage_ready = True
        except sqlite3.Error:
            pass
        dispatcher_ready = time.time() - self._dispatcher_heartbeat <= 30
        ready = storage_ready and dispatcher_ready and sending == 0 and uncertain == 0 and reconciliation_required == 0
        return {
            "schema": "notify.health.v1",
            "service": "notification-center",
            "status": "ok" if ready else "degraded",
            "storage_ready": storage_ready,
            "dispatcher_ready": dispatcher_ready,
            "queued_deliveries": queued,
            "sending_deliveries": sending,
            "uncertain_deliveries": uncertain,
            "reconciliation_required": reconciliation_required,
            "version": "0.1.0",
        }

    @staticmethod
    def _health_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Bound health workflow payloads before they are persisted."""
        from .health_workflow import _bounded_refs, _bounded_summary, _bounded_text, normalize_health_execution

        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"step", "progress_fingerprint", "plan_id", "source_id", "verification_id", "observed_state", "selected_plan_id", "selected_by", "event_type", "correlation_id", "actor", "source_state"} and value is not None:
                result[key] = _bounded_text(value, 128)
            elif key in {"title", "summary", "body", "reason"} and value is not None:
                result[key] = _bounded_summary(value)
            elif key in {"evidence_refs", "trace_refs"}:
                result[key] = _bounded_refs(value)
            elif key == "execution":
                try:
                    result[key] = normalize_health_execution(value)
                except ValidationError:
                    result[key] = {}
            elif key == "plans" and isinstance(value, list):
                result[key] = [
                    {
                        "plan_id": _bounded_text(item.get("plan_id"), 64),
                        "title": _bounded_summary(item.get("title")),
                        "summary": _bounded_summary(item.get("summary")),
                        "step": _bounded_text(item.get("step"), 64),
                        "execution": normalize_health_execution(item.get("execution")),
                    }
                    for item in value
                    if isinstance(item, Mapping)
                ]
            elif key == "orchestration" and isinstance(value, Mapping):
                result[key] = {
                    field: _bounded_text(value.get(field), 128)
                    for field in (
                        "diagnosis_session_id",
                        "diagnosis_model",
                        "orchestrator_session_id",
                        "orchestrator_requested_model",
                        "orchestrator_effective_model",
                        "harness",
                    )
                    if value.get(field) is not None
                }
                for field in ("diagnosis_elapsed_ms", "orchestrator_elapsed_ms", "total_elapsed_ms"):
                    try:
                        result[key][field] = max(0, min(86_400_000, int(value.get(field) or 0)))
                    except (TypeError, ValueError):
                        result[key][field] = 0
            elif key == "heartbeat_at":
                result[key] = value
            elif key == "elapsed_ms":
                try:
                    result[key] = max(0, min(86_400_000, int(value)))
                except (TypeError, ValueError):
                    result[key] = 0
            elif value is not None:
                result[key] = _bounded_text(value, 128)
        return result

    def record_health_update(self, incident_id: str, idempotency_key: str, event_type: str, payload: Mapping[str, Any], actor: str | None = None) -> dict[str, Any]:
        """Persist a bounded health workflow update without creating a second incident."""
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        if self.get_incident(incident_id) is None:
            raise ValidationError("incident not found")
        bounded_payload = self._health_payload(payload)
        payload_json = json.dumps(bounded_payload, ensure_ascii=False, sort_keys=True)
        now = time.time()
        safe_event_type = str(event_type or "").strip()[:128]
        safe_actor = str(actor or "").strip()[:128] or None
        with self._lock, self._connection:
            previous = self._connection.execute(
                "SELECT event_id, payload_json FROM events WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different health update content")
                return {"event_id": previous["event_id"], "incident_id": incident_id, "idempotent": True, **bounded_payload}
            event_id = f"evt_{uuid.uuid4().hex}"
            self._connection.execute(
                "INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at, event_type, producer, plugin, correlation_id, parent_event_id, parent_incident_id, peer_ip, source_ip, proxy_ip, forwarded_for) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (idempotency_key, event_id, incident_id, payload_json, now, safe_event_type, safe_actor, "health-workflow", bounded_payload.get("correlation_id"), None, None, None, None, None, None),
            )
            self._audit(incident_id, safe_event_type or "health_update", safe_actor, bounded_payload)
            if safe_event_type == "health.plans_attached":
                if not self.health_incident_is_synthetic(incident_id):
                    telegram_rows = self._connection.execute(
                        "SELECT id, channel, delivery_key, status, result_json FROM deliveries WHERE incident_id = ? AND channel LIKE 'telegram.%' ORDER BY created_at, rowid",
                        (incident_id,),
                    ).fetchall()
                    active = [row for row in telegram_rows if str(row["status"]) in {"queued", "claimed", "sending", "sent", "uncertain"}]
                    if active:
                        uncertain_rows = [row for row in active if str(row["status"]) == "uncertain"]
                        sending_rows = [row for row in active if str(row["status"]) == "sending"]
                        sent_rows = [row for row in active if str(row["status"]) == "sent"]
                        attached_plan_ids = tuple(
                            str(plan.get("plan_id") or plan.get("id") or "").strip()
                            for plan in (bounded_payload.get("plans") if isinstance(bounded_payload.get("plans"), list) else [])
                            if isinstance(plan, Mapping)
                        )
                        sent_rows_are_compliant = bool(attached_plan_ids) and all(
                            self._sent_health_delivery_matches_plans(row, attached_plan_ids)
                            for row in sent_rows
                        )
                        if len(sent_rows) > 1:
                            for duplicate in active:
                                if str(duplicate["status"]) in {"sent", "sending", "uncertain"}:
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("multiple sent Health cards require message reconciliation", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.multiple_sent_delivery_reconciliation_required"},
                                )
                            self._audit(
                                incident_id,
                                "health.multiple_sent_delivery_reconciliation_required",
                                "health-workflow",
                                {"sent_delivery_ids": [str(row["id"]) for row in sent_rows]},
                            )
                        elif sent_rows and sending_rows:
                            for duplicate in active:
                                if str(duplicate["status"]) in {"sent", "sending", "uncertain"}:
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("sent and in-flight Health cards require message reconciliation", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.sent_and_sending_reconciliation_required"},
                                )
                            self._audit(
                                incident_id,
                                "health.sent_and_sending_reconciliation_required",
                                "health-workflow",
                                {"sent_delivery_ids": [str(row["id"]) for row in sent_rows], "sending_delivery_ids": [str(row["id"]) for row in sending_rows]},
                            )
                        elif uncertain_rows:
                            for duplicate in active:
                                if str(duplicate["status"]) in {"sent", "sending", "uncertain"}:
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("uncertain Telegram send requires reconciliation before plan delivery", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.uncertain_delivery_reconciliation_required"},
                                )
                            self._audit(
                                incident_id,
                                "health.uncertain_delivery_reconciliation_required",
                                "health-workflow",
                                {"delivery_ids": [str(row["id"]) for row in uncertain_rows]},
                            )
                        elif sending_rows:
                            for duplicate in active:
                                if str(duplicate["status"]) in {"sent", "sending"}:
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("Telegram send is in progress; plan delivery is deferred", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.send_in_progress"},
                                )
                            self._audit(
                                incident_id,
                                "health.send_in_progress",
                                "health-workflow",
                                {"delivery_ids": [str(row["id"]) for row in sending_rows]},
                            )
                        elif sent_rows and not sent_rows_are_compliant:
                            migration_target = self._health_message_migration_target(sent_rows[0]) if len(sent_rows) == 1 else None
                            if migration_target is not None:
                                existing_edit = next(
                                    (
                                        row for row in active
                                        if str(row["channel"]) == "telegram.edit"
                                        and str(row["status"]) in {"queued", "claimed", "sending"}
                                    ),
                                    None,
                                )
                                for duplicate in active:
                                    if str(duplicate["status"]) == "sent" or (existing_edit is not None and duplicate["id"] == existing_edit["id"]):
                                        continue
                                    self._connection.execute(
                                        "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                        ("legacy health card is being edited in place", now, duplicate["id"]),
                                    )
                                    self._audit(
                                        incident_id,
                                        "delivery_cancelled",
                                        "health-workflow",
                                        {"delivery_id": duplicate["id"], "reason": "health.legacy_card_edit_scheduled"},
                                    )
                                edit_id = existing_edit["id"] if existing_edit is not None else self._schedule_delivery(incident_id, "telegram.edit", "health.migration", now, migration_target)
                                if existing_edit is not None and str(existing_edit["status"]) == "queued":
                                    self._connection.execute(
                                        "UPDATE deliveries SET due_at = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                                        (now, now, edit_id),
                                    )
                                self._audit(
                                    incident_id,
                                    "health.legacy_card_edit_scheduled",
                                    "health-workflow",
                                    {"source_delivery_id": str(sent_rows[0]["id"]), "edit_delivery_id": edit_id},
                                )
                            else:
                                for duplicate in active:
                                    if str(duplicate["status"]) == "sent":
                                        continue
                                    self._connection.execute(
                                        "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                        ("legacy health card requires message migration before plan delivery", now, duplicate["id"]),
                                    )
                                    self._audit(
                                        incident_id,
                                        "delivery_cancelled",
                                        "health-workflow",
                                        {"delivery_id": duplicate["id"], "reason": "health.legacy_card_migration_required"},
                                    )
                                self._audit(
                                    incident_id,
                                    "health.legacy_card_migration_required",
                                    "health-workflow",
                                    {"sent_delivery_ids": [str(row["id"]) for row in sent_rows], "plan_ids": list(attached_plan_ids)},
                                )
                        elif sent_rows:
                            for duplicate in active:
                                if str(duplicate["status"]) == "sent":
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("superseded by an already sent health delivery", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.delivery_already_sent"},
                                )
                        else:
                            non_sent = [row for row in active if str(row["status"]) != "sent"]
                            plan_rows = [row for row in non_sent if str(row["delivery_key"]).endswith(":health.plans")]
                            custom_rows = [row for row in non_sent if str(row["channel"]).startswith("telegram.consumer:")]
                            canonical = (custom_rows or plan_rows or non_sent or active)[0]
                            for duplicate in active:
                                if duplicate["id"] == canonical["id"]:
                                    continue
                                self._connection.execute(
                                    "UPDATE deliveries SET status = 'cancelled', last_error = ?, updated_at = ? WHERE id = ?",
                                    ("superseded by health plan delivery", now, duplicate["id"]),
                                )
                                self._audit(
                                    incident_id,
                                    "delivery_cancelled",
                                    "health-workflow",
                                    {"delivery_id": duplicate["id"], "reason": "health.plans_attached"},
                                )
                            if str(canonical["status"]) == "queued":
                                self._connection.execute(
                                    "UPDATE deliveries SET due_at = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                                    (now, now, canonical["id"]),
                                )
                    else:
                        # A previous activation may have cancelled the stable
                        # plan slot while Health Telegram was disabled.  Keep
                        # the slot identity for history, but make a new
                        # validated three-plan bundle eligible for delivery
                        # again.  Sent/uncertain rows are intentionally not
                        # revived here; those still require message-level
                        # reconciliation.
                        delivery_key = f"{incident_id}:telegram.main:health.plans"
                        existing = self._connection.execute(
                            "SELECT id, status FROM deliveries WHERE delivery_key = ?",
                            (delivery_key,),
                        ).fetchone()
                        if existing is not None and str(existing["status"]) == "cancelled":
                            self._connection.execute(
                                "UPDATE deliveries SET status = 'queued', due_at = ?, claimed_at = NULL, last_error = NULL, updated_at = ? WHERE id = ?",
                                (now, now, existing["id"]),
                            )
                            self._audit(
                                incident_id,
                                "delivery_rescheduled",
                                "health-workflow",
                                {"delivery_id": str(existing["id"]), "reason": "health.plans_attached"},
                            )
                        else:
                            self._schedule_delivery(incident_id, "telegram.main", "health.plans", now)
            return {"event_id": event_id, "incident_id": incident_id, "idempotent": False, **bounded_payload}

    @staticmethod
    def _sent_health_delivery_matches_plans(row: Mapping[str, Any], attached_plan_ids: tuple[str, ...]) -> bool:
        """Prove a sent Health card carried the exact plan set now attached."""
        try:
            result = json.loads(str(row["result_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(result, Mapping):
            return False
        message_id = result.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            return False
        if not attached_plan_ids:
            return result.get("ai_button_count") == 1 and result.get("ai_signed_callback_count") == 1
        if result.get("health_button_count") != 3 or result.get("health_signed_callback_count") != 3:
            return False
        sent_plan_ids = result.get("health_plan_ids")
        if not isinstance(sent_plan_ids, list):
            return False
        return tuple(str(plan_id).strip() for plan_id in sent_plan_ids) == attached_plan_ids and len(set(attached_plan_ids)) == 3

    @staticmethod
    def _health_message_migration_target(row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return the persisted Bot API identity needed for an in-place edit."""
        try:
            result = json.loads(str(row["result_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(result, Mapping):
            return None
        message_id = result.get("message_id")
        chat_id = str(result.get("chat_id") or "").strip()
        if not isinstance(message_id, int) or message_id <= 0 or not chat_id:
            return None
        return {"chat_id": chat_id, "message_id": message_id, "source_delivery_id": str(row["id"])}

    def _health_events(self, incident_id: str, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return bounded health events newest first."""
        query = "SELECT event_id, payload_json, created_at, event_type FROM events WHERE incident_id = ?"
        params: list[Any] = [incident_id]
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at DESC, event_id DESC"
        rows = self._connection.execute(query, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            result.append({"event_id": row["event_id"], "payload": payload, "created_at": row["created_at"], "event_type": row["event_type"]})
        return result

    def latest_health_event(self, incident_id: str, event_type: str) -> dict[str, Any] | None:
        """Return the newest health event payload for one incident and type."""
        events = self._health_events(incident_id, event_type)
        return events[0] if events else None

    def latest_health_plans(self, incident_id: str) -> list[dict[str, Any]]:
        """Return the newest attached plan bundle, or an empty list."""
        latest = self.latest_health_event(incident_id, "health.plans_attached")
        plans = latest["payload"].get("plans") if latest else []
        return [dict(plan) for plan in plans] if isinstance(plans, list) else []

    def latest_health_selection(self, incident_id: str) -> dict[str, Any] | None:
        """Return the newest durable plan selection, if one exists."""
        latest = self.latest_health_event(incident_id, "health.plan_selected")
        return latest["payload"] if latest else None

    def latest_health_progress(self, incident_id: str, plan_id: str | None = None) -> dict[str, Any] | None:
        """Return the newest progress receipt, optionally narrowed to one plan."""
        for event in self._health_events(incident_id, HEALTH_UPDATE_EVENT_TYPES["progress"]):
            payload = event["payload"]
            if plan_id is None or str(payload.get("plan_id") or "") == plan_id:
                return payload
        return None

    def _health_useful_progress_exists(self, incident_id: str, plan_id: str) -> bool:
        """Return true when any durable receipt for the selected plan was useful."""
        for event in self._health_events(incident_id, HEALTH_UPDATE_EVENT_TYPES["progress"]):
            payload = event["payload"]
            if str(payload.get("plan_id") or "") == plan_id and bool(payload.get("useful") or payload.get("useful_progress")):
                return True
        return False

    def latest_health_verification(self, incident_id: str) -> dict[str, Any] | None:
        """Return the newest independent verification receipt, if any."""
        latest = self.latest_health_event(incident_id, HEALTH_UPDATE_EVENT_TYPES["verification"])
        return latest["payload"] if latest else None
