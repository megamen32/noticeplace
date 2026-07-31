"""SQLite-backed incident state machine for the notification-center MVP."""

from __future__ import annotations

import json
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

    def __init__(self, database_path: Path | str, tokens: Mapping[str, Mapping[str, str]]) -> None:
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
                CREATE UNIQUE INDEX IF NOT EXISTS incidents_open_dedup
                    ON incidents(project, recipient, dedup_key)
                    WHERE state != 'resolved';
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
                """
            )

    def _scope(self, token: str, project: str) -> Mapping[str, str]:
        """Validate a token's project boundary without requiring an event severity."""
        scope = self._tokens.get(token)
        if scope is None:
            raise AuthorizationError("invalid bearer token")
        if scope.get("project") not in ("*", project):
            raise AuthorizationError("token is not allowed for this project")
        return scope

    def _token(self, token: str, event: Mapping[str, Any]) -> Mapping[str, str]:
        """Validate token project and severity boundaries; raises AuthorizationError."""
        scope = self._scope(token, str(event.get("project") or ""))
        self._require_severity(scope, str(event.get("severity") or ""))
        return scope

    @staticmethod
    def _require_severity(scope: Mapping[str, str], severity: str) -> None:
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
        self._token(token, event)
        now = time.time()
        with self._lock, self._connection:
            payload_json = json.dumps(dict(event), ensure_ascii=False, sort_keys=True)
            previous = self._connection.execute("SELECT event_id, incident_id, payload_json FROM events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if previous is not None:
                if previous["payload_json"] != payload_json:
                    raise IdempotencyConflict("Idempotency-Key was already used with different event content")
                initial = self._connection.execute("SELECT id FROM deliveries WHERE delivery_key = ?", (f"{previous['incident_id']}:telegram.main:initial",)).fetchone()
                return {"event_id": previous["event_id"], "incident_id": previous["incident_id"], "state": self.get_incident(previous["incident_id"])["state"], "deduplicated": False, "idempotent": True, "initial_delivery_id": initial["id"] if initial else None}
            project, recipient, dedup_key = str(event["project"]), str(event["recipient"]), str(event["dedup_key"])
            existing = self._connection.execute("SELECT id FROM incidents WHERE project = ? AND recipient = ? AND dedup_key = ? AND state != 'resolved'", (project, recipient, dedup_key)).fetchone()
            deduplicated = existing is not None
            if existing is None:
                incident_id = f"inc_{uuid.uuid4().hex}"
                self._connection.execute(
                    "INSERT INTO incidents(id, project, recipient, kind, severity, title, body, dedup_key, collapse_key, state, occurrences, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 1, ?, ?)",
                    (incident_id, project, recipient, str(event["kind"]), str(event["severity"]), str(event["title"]), str(event.get("body") or ""), dedup_key, event.get("collapse_key"), now, now),
                )
                self._audit(incident_id, "incident_created", "producer", {"dedup_key": dedup_key})
            else:
                incident_id = str(existing["id"])
                self._connection.execute("UPDATE incidents SET occurrences = occurrences + 1, title = ?, body = ?, updated_at = ? WHERE id = ?", (str(event["title"]), str(event.get("body") or ""), now, incident_id))
                self._audit(incident_id, "incident_repeated", "producer", {"dedup_key": dedup_key})
            event_id = f"evt_{uuid.uuid4().hex}"
            self._connection.execute("INSERT INTO events(idempotency_key, event_id, incident_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", (idempotency_key, event_id, incident_id, payload_json, now))
            delivery_id = self._schedule_delivery(incident_id, "telegram.main", "initial", now)
            return {"event_id": event_id, "incident_id": incident_id, "state": self.get_incident(incident_id)["state"], "deduplicated": deduplicated, "idempotent": False, "initial_delivery_id": delivery_id}

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

    def _schedule_delivery(self, incident_id: str, channel: str, step: str, due_epoch: float) -> str:
        """Create a stable scheduled delivery and return its identity."""
        delivery_key = f"{incident_id}:{channel}:{step}"
        row = self._connection.execute("SELECT id FROM deliveries WHERE delivery_key = ?", (delivery_key,)).fetchone()
        if row is not None:
            return str(row["id"])
        delivery_id = f"dlv_{uuid.uuid4().hex}"
        now = time.time()
        self._connection.execute("INSERT INTO deliveries(id, incident_id, channel, delivery_key, due_at, status, attempt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?)", (delivery_id, incident_id, channel, delivery_key, due_epoch, now, now))
        self._audit(incident_id, "delivery_scheduled", "policy", {"delivery_id": delivery_id, "channel": channel, "due_at": due_epoch})
        return delivery_id

    def schedule_escalation(self, incident_id: str, channel: str, due_epoch: float) -> str:
        """Schedule one named escalation while the incident remains active."""
        with self._lock, self._connection:
            incident = self.get_incident(incident_id)
            if incident is None:
                raise ValidationError("incident not found")
            if incident["state"] not in DELIVERABLE_STATES:
                raise ValidationError(f"cannot escalate an {incident['state']} incident")
            return self._schedule_delivery(incident_id, channel, "escalation", due_epoch)

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
        if outcome not in ("sent", "cancelled", "retry"):
            raise ValidationError("delivery outcome must be sent, cancelled, or retry")
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute("SELECT incident_id FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            if row is None:
                raise ValidationError("delivery not found")
            if outcome == "retry":
                self._connection.execute("UPDATE deliveries SET status = 'queued', due_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (now + max(1, retry_after_seconds), (error or "")[-1000:], now, delivery_id))
            else:
                self._connection.execute("UPDATE deliveries SET status = ?, last_error = ?, updated_at = ? WHERE id = ?", (outcome, (error or "")[-1000:] or None, now, delivery_id))
            self._audit(str(row["incident_id"]), f"delivery_{outcome}", "worker", {"delivery_id": delivery_id, "error": error})

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
        return self._transition(incident_id, "acknowledged", actor)

    def resolve(self, incident_id: str, actor: str) -> dict[str, Any]:
        """Resolve an incident and prevent future delivery from its prior state."""
        return self._transition(incident_id, "resolved", actor)

    def snooze(self, incident_id: str, until_epoch: float, actor: str) -> dict[str, Any]:
        """Temporarily defer pending delivery; raises ValidationError for past times."""
        if until_epoch <= time.time():
            raise ValidationError("snooze deadline must be in the future")
        return self._transition(incident_id, "snoozed", actor, until_epoch)

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
        return {"delivery": dict(delivery), "incident": incident}

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
