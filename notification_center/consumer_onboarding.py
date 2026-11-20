"""Project connection checks and overlapping producer credential rotation."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from .core import CONNECTION_TEST_EVENT, IdempotencyConflict, ValidationError


TEST_STEP = "connection-test"
_FINGERPRINT = re.compile(r"[0-9a-f]{12}")
_REQUEST = re.compile(r"[0-9a-f]{32}")


class ConsumerNotFound(ValidationError):
    pass


def _consumer(center, consumer_id):
    row = center._connection.execute(
        "SELECT id, project, name, token_fingerprint, max_severity, policy_revision FROM consumers "
        "WHERE id=? AND profile_type='custom'", (consumer_id,),
    ).fetchone()
    if row is None:
        raise ConsumerNotFound("Профиль источника не найден.")
    return dict(row)


def _pending(center, consumer_id):
    return center._connection.execute(
        "SELECT fingerprint, created_at FROM consumer_token_replacements WHERE consumer_id=?",
        (consumer_id,),
    ).fetchone()


def _request_key(consumer_id, request_id):
    if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
        raise ValidationError("Неверный номер тестовой доставки.")
    return f"connection-test:{consumer_id}:{request_id}"


def _test_route(center, consumer_id, revision):
    row = center._connection.execute(
        "SELECT target_json FROM versioned_consumer_policy_stages "
        "WHERE consumer_id=? AND revision=? AND enabled=1 AND platform='matrix' AND action='message' "
        "AND previous_step_id IS NULL "
        "ORDER BY stage LIMIT 1", (consumer_id, revision),
    ).fetchone()
    if row is None:
        return None
    target = json.loads(row["target_json"])
    room = target.get("room_id")
    return target if isinstance(room, str) and room.startswith("!") else None


def list_connections(center, query=""):
    if not isinstance(query, str) or len(query) > 128:
        raise ValidationError("Укажите имя или проект источника длиной до 128 символов.")
    with center._lock:
        rows = center._connection.execute(
            "SELECT id, project, name FROM consumers WHERE profile_type='custom' "
            "AND (?='' OR instr(lower(project), lower(?))>0 OR instr(lower(name), lower(?))>0) "
            "ORDER BY created_at DESC, id DESC LIMIT 200", (query, query, query),
        ).fetchall()
    return {"connections": [dict(row) for row in rows]}


def _test_receipt(center, consumer_id, request_id):
    if request_id is not None:
        incident_query = "SELECT incident_id FROM events WHERE idempotency_key=? AND event_type=?"
        incident_params = (_request_key(consumer_id, request_id), CONNECTION_TEST_EVENT)
    else:
        incident_query = ("SELECT id FROM incidents WHERE consumer_id=? "
                          f"AND event_type='{CONNECTION_TEST_EVENT}' ORDER BY created_at DESC, id DESC LIMIT 1")
        incident_params = (consumer_id,)
    row = center._connection.execute(
        "SELECT i.id AS incident_id, d.status, d.result_json, d.updated_at, "
        "CASE WHEN d.last_error='consumer_quiet_hours' THEN d.due_at END AS quiet_until "
        "FROM incidents i LEFT JOIN deliveries d ON d.delivery_key=i.id || ? "
        f"WHERE i.id=({incident_query}) AND i.consumer_id=?",
        (f":matrix.message:{TEST_STEP}", *incident_params, consumer_id),
    ).fetchone()
    if row is None:
        return None
    receipt = json.loads(row["result_json"] or "{}")
    event_id = receipt.get("event_id") if isinstance(receipt, dict) else None
    state = row["status"] or "blocked"
    if state == "sent" and not (isinstance(event_id, str) and 1 < len(event_id) <= 512 and event_id.startswith("$")):
        state = "sent_unconfirmed"
    return {
        "incident_id": row["incident_id"], "state": state, "updated_at": row["updated_at"],
        "provider_event_id": event_id if state == "sent" else None,
        "quiet_until": row["quiet_until"],
    }


def connection_detail(center, consumer_id, request_id=None):
    with center._lock:
        consumer = _consumer(center, consumer_id)
        pending = _pending(center, consumer_id)
        target = _test_route(center, consumer_id, consumer["policy_revision"])
        return {
            "connection": consumer,
            "replacement": dict(pending) if pending else None,
            "test_room": target["room_id"] if target else None,
            "test": _test_receipt(center, consumer_id, request_id),
        }


def queue_test(center, consumer_id, token, request_id):
    key = _request_key(consumer_id, request_id)
    consumer = _consumer(center, consumer_id)
    center.create_event(token, key, {
        "schema": "notify.event.v1", "kind": "notification", "severity": "debug",
        "project": consumer["project"], "recipient": "connection-check",
        "event_type": CONNECTION_TEST_EVENT, "dedup_key": key,
        "title": "Тестовое сообщение Notice Place",
        "body": "Токен проекта принят. Это проверка доставки при подключении.",
    }, _onboarding_consumer_id=consumer_id)
    return connection_detail(center, consumer_id, request_id)


def schedule_test(center, incident_id, consumer_id, now):
    revision = center._connection.execute(
        "SELECT consumer_policy_revision FROM incidents WHERE id=?", (incident_id,),
    ).fetchone()["consumer_policy_revision"]
    target = _test_route(center, consumer_id, revision)
    if not target:
        raise ValidationError("Первым шагом цепочки должно быть сообщение в комнату Matrix.")
    # An independent one-step message has no policy successor.
    return center._schedule_delivery(incident_id, "matrix.message", TEST_STEP, now, target)


def rotate(center, consumer_id, operation, expected, actor):
    if not isinstance(expected, str) or not _FINGERPRINT.fullmatch(expected):
        raise ValidationError("Обновите карточку токена перед изменением.")
    issued = {}
    with center._lock, center._connection:
        consumer = _consumer(center, consumer_id)
        if operation == "issue":
            token = "nct_" + secrets.token_hex(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            created = center._connection.execute(
                "INSERT INTO consumer_token_replacements(consumer_id, base_fingerprint, token_hash, fingerprint, created_at) "
                "SELECT id, token_fingerprint, ?, ?, ? FROM consumers WHERE id=? AND token_fingerprint=? "
                "ON CONFLICT(consumer_id) DO NOTHING",
                (digest, digest[:12], time.time(), consumer_id, expected),
            )
            if created.rowcount != 1:
                raise IdempotencyConflict("Карточка изменилась. Обновите состояние токенов.")
            issued = {"intake_token": token}
        elif operation in ("activate", "cancel"):
            pending = center._connection.execute(
                "SELECT base_fingerprint, token_hash FROM consumer_token_replacements "
                "WHERE consumer_id=? AND fingerprint=?", (consumer_id, expected),
            ).fetchone()
            if pending is None or pending["base_fingerprint"] != consumer["token_fingerprint"]:
                raise IdempotencyConflict("Карточка изменилась. Обновите состояние токенов.")
            if operation == "activate":
                updated = center._connection.execute(
                    "UPDATE consumers SET token_hash=?, token_fingerprint=?, updated_at=? "
                    "WHERE id=? AND token_fingerprint=? AND EXISTS (SELECT 1 FROM consumer_token_replacements "
                    "WHERE consumer_id=? AND fingerprint=?)",
                    (pending["token_hash"], expected, time.time(), consumer_id,
                     pending["base_fingerprint"], consumer_id, expected),
                )
                if updated.rowcount != 1:
                    raise IdempotencyConflict("Карточка изменилась. Обновите состояние токенов.")
            deleted = center._connection.execute(
                "DELETE FROM consumer_token_replacements WHERE consumer_id=? AND fingerprint=?",
                (consumer_id, expected),
            )
            if deleted.rowcount != 1:
                raise IdempotencyConflict("Карточка изменилась. Обновите состояние токенов.")
        else:
            raise ValidationError("Неизвестная операция с токеном.")
        center._audit(None, f"consumer_token_replacement_{operation}", actor, {
            "consumer_id": consumer_id, "project": consumer["project"],
        })
        return {**connection_detail(center, consumer_id), **issued}
