"""Exercise policy edits through intake and the durable delivery queue."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from notification_center.core import IdempotencyConflict, NotificationCenter, ValidationError
from notification_center.operator_consumer_policy import edit_policy


@pytest.fixture
def open_center(tmp_path):
    centers = []

    def open_db():
        center = NotificationCenter(tmp_path / "notify.sqlite3", {})
        centers.append(center)
        return center

    yield open_db
    for center in centers:
        center._connection.close()


def step(step_id, target, *, platform="matrix", action="message", previous=None, interval=60, repeats=1):
    return {
        "id": step_id, "platform": platform, "action": action, "target": target,
        "previous_step_id": previous, "retry_interval_seconds": interval, "max_repeats": repeats,
    }


def intake(center, consumer, key, *, dedup_key=None):
    return center.create_event(consumer["intake_token"], key, {
        "schema": "notify.event.v1", "kind": "notification", "severity": "important",
        "project": "billing", "recipient": "operator", "title": "Invoice due",
        "dedup_key": dedup_key or key,
    })


def queued(center, incident_id):
    return center._connection.execute(
        "SELECT * FROM deliveries WHERE incident_id = ? AND status = 'queued' ORDER BY due_at, delivery_key",
        (incident_id,),
    ).fetchall()


def test_edit_keeps_old_claim_repeat_successor_and_failure_neighbours(open_center, monkeypatch):
    now = datetime(2030, 1, 2, 2, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr("notification_center.core.time.time", lambda: now)
    writer = open_center()
    old_route = {"room_id": "!old:example.org", "senders": ["@old:example.org"]}
    new_route = {"room_id": "!new:example.org", "senders": ["@new:example.org"]}
    quiet_hours = [{"start": "01:00", "end": "04:00", "timezone": "UTC", "suppress": ["call"]}]
    consumer = writer.create_consumer("billing", "Invoice alerts", [
        step("start", old_route, interval=90, repeats=2),
        step("old-call", old_route, action="call", previous="start"),
        step("old-final", {**old_route, "template": "old"}, previous="old-call"),
    ], quiet_hours=quiet_hours)
    consumer_id = consumer["id"]
    existing = intake(writer, consumer, "existing")
    existing_id = existing["incident_id"]
    initial = existing["initial_delivery_id"]

    saved = writer.replace_consumer_policy(consumer_id, [
        step("start", new_route, interval=5),
        step("new-final", {"template": "new"}, platform="whatsapp", previous="start"),
    ], "editor", expected_revision=0)
    reader = open_center()
    assert reader.get_consumer(consumer_id) == saved
    assert saved["revision"] == 1
    assert saved["token_fingerprint"] == consumer["token_fingerprint"]
    assert saved["quiet_hours"] == quiet_hours
    assert reader.consumer_matrix_scope(consumer_id)["room_id"] == new_route["room_id"]
    assert not reader.consumer_policy_is_matrix_only(consumer_id)
    assert reader.incident_is_matrix_only(existing_id)

    duplicate = intake(reader, consumer, "dedup", dedup_key="existing")
    assert duplicate["incident_id"] == existing_id
    assert duplicate["initial_delivery_id"] == initial
    assert len(queued(reader, existing_id)) == 1
    [claim] = reader.claim_due_deliveries(now_epoch=now, delivery_id=initial)
    assert reader.delivery_attempt_ceiling(claim) == 2
    assert reader.delivery_payload(claim)["target"] == old_route
    reader.complete_delivery(initial, "sent", result={"event_id": "$old-notice"})
    [repeat] = queued(reader, existing_id)
    assert (repeat["repeat_number"], repeat["due_at"], json.loads(repeat["target_json"])) == (2, now + 90, old_route)

    now += 90
    [claim] = reader.claim_due_deliveries(now_epoch=now, delivery_id=repeat["id"])
    reader.complete_delivery(claim["id"], "sent")
    [call] = queued(reader, existing_id)
    assert (call["channel"], call["policy_step_id"]) == ("matrix.call", "old-call")
    assert reader.claim_due_deliveries(now_epoch=now, delivery_id=call["id"]) == []
    [deferred] = queued(reader, existing_id)
    assert deferred["due_at"] == datetime(2030, 1, 2, 4, tzinfo=timezone.utc).timestamp()
    now = deferred["due_at"]
    [claim] = reader.claim_due_deliveries(now_epoch=now, delivery_id=call["id"])
    assert reader.delivery_payload(claim)["destination"]["room_id"] == old_route["room_id"]
    reader.complete_delivery(claim["id"], "failed", "local test failure")
    assert {
        row["delivery_key"].rsplit(":", 1)[-1]: json.loads(row["target_json"])
        for row in queued(reader, existing_id)
    } == {"previous": old_route, "next": {**old_route, "template": "old"}}

    with pytest.raises(ValidationError, match="outside the explicit route"):
        reader.bind_matrix_reply("$wrong-reply", "$old-notice", new_route["senders"][0], new_route["room_id"])
    assert reader.bind_matrix_reply("$old-reply", "$old-notice", old_route["senders"][0], old_route["room_id"])["bound"]

    fresh = intake(reader, consumer, "fresh")
    fresh_id = fresh["incident_id"]
    assert fresh_id != existing_id
    assert reader.get_incident(fresh_id)["consumer_policy_revision"] == 1
    [claim] = reader.claim_due_deliveries(now_epoch=now, delivery_id=fresh["initial_delivery_id"])
    assert reader.delivery_attempt_ceiling(claim) == 1
    assert json.loads(claim["target_json"]) == new_route
    reader.complete_delivery(claim["id"], "sent")
    [successor] = queued(reader, fresh_id)
    assert (successor["channel"], successor["policy_step_id"]) == ("whatsapp.message", "new-final")
    latest_route = {"room_id": "!latest:example.org"}
    latest = writer.replace_consumer_policy(consumer_id, [step("start", latest_route)], "editor", expected_revision=1)
    assert latest["revision"] == 2
    assert reader.incident_matrix_scope(existing_id)["room_id"] == old_route["room_id"]
    assert reader.incident_matrix_scope(fresh_id)["room_id"] == new_route["room_id"]
    assert reader.consumer_matrix_scope(consumer_id)["room_id"] == latest_route["room_id"]


def test_concurrent_saves_conflict_across_connections(open_center):
    first, second = open_center(), open_center()
    consumer = first.create_consumer("billing", "Invoice alerts", [step("original", {})])
    consumer_id = consumer["id"]
    ready = Barrier(2, timeout=5)

    def save(center, step_id):
        editor_revision = center.get_consumer(consumer_id)["revision"]
        ready.wait()
        try:
            return center.replace_consumer_policy(consumer_id, [step(step_id, {})], step_id,
                                                  expected_revision=editor_revision)
        except IdempotencyConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(save, center, step_id) for center, step_id in ((first, "one"), (second, "two"))]
        [winner] = [result for attempt in attempts if (result := attempt.result()) is not None]
    reader = open_center()
    assert winner["revision"] == 1
    assert reader.get_consumer(consumer_id) == winner
    with pytest.raises(ValidationError, match="builtin profile"):
        reader.replace_consumer_policy("profile_emergency", [step("blocked", {})], "editor", expected_revision=0)
    assert reader.get_consumer("profile_emergency")["revision"] == 0


def test_read_keeps_captured_revision_and_raw_targets_when_save_interleaves(open_center):
    writer, reader = open_center(), open_center()
    raw_target = {"stage": "route detail", "enabled": {"raw": True}, "extra": [1, {"x": 2}]}
    consumer = writer.create_consumer("billing", "Invoice alerts", [step("old", raw_target)])
    consumer_id = consumer["id"]
    saved = []

    def save_before_stages(sql):
        if "FROM versioned_consumer_policy_stages" in sql and not saved:
            saved.append(writer.replace_consumer_policy(consumer_id, [step("new", {})], "editor"))

    reader._connection.set_trace_callback(save_before_stages)
    old = reader.get_consumer(consumer_id, nested_targets=True)
    reader._connection.set_trace_callback(None)
    assert len(saved) == 1 and saved[0]["revision"] == 1
    assert old["revision"] == 0
    assert old["policy"] == [{
        "stage": 1, "kind": "matrix.message", "enabled": True, "target": raw_target,
        "id": "old", "step_id": "old", "platform": "matrix", "action": "message",
        "retry_interval_seconds": 60.0, "max_repeats": 1,
    }]
    assert reader.get_consumer(consumer_id) == saved[0]


def test_pre_revision_incident_and_legacy_stages_survive_first_edit(open_center):
    original = open_center()
    consumer = original.create_consumer("billing", "Invoice alerts", [
        {"kind": "telegram", "chat_id": -123, "topic_id": 42},
        {"kind": "phone", "delay_seconds": 600},
    ], quiet_hours=[])
    existing = intake(original, consumer, "before-upgrade")
    consumer_id = consumer["id"]
    incident_id = existing["incident_id"]
    # Remove the additive schema to recreate a pre-revision database with queued work.
    with original._connection:
        original._connection.execute("DROP VIEW versioned_consumer_policy_stages")
        original._connection.execute("DROP TABLE consumer_policy_history")
        original._connection.execute("ALTER TABLE incidents DROP COLUMN consumer_policy_revision")
        original._connection.execute("ALTER TABLE consumers DROP COLUMN policy_revision")

    upgraded = open_center()
    assert upgraded.get_incident(incident_id)["consumer_policy_revision"] == 0
    metadata = upgraded.get_consumer(consumer_id, nested_targets=True)
    assert metadata["revision"] == 0
    assert metadata["policy"] == [
        {"stage": 1, "kind": "telegram", "enabled": True, "target": {"chat_id": -123, "topic_id": 42}},
        {"stage": 2, "kind": "phone", "enabled": True, "target": {"delay_seconds": 600.0}},
    ]
    assert upgraded.get_consumer(consumer_id)["policy"][0]["chat_id"] == -123
    upgraded.replace_consumer_policy(consumer_id, [step("matrix-only", {"room_id": "!new:example.org"})],
                                     "editor", expected_revision=0)
    reader = open_center()
    assert reader.incident_matrix_scope(incident_id) is None
    assert reader.consumer_matrix_scope(consumer_id)["room_id"] == "!new:example.org"
    assert not reader.incident_is_matrix_only(incident_id)
    duplicate = intake(reader, consumer, "after-upgrade", dedup_key="before-upgrade")
    assert duplicate["initial_delivery_id"] == existing["initial_delivery_id"]
    assert [row["channel"] for row in queued(reader, incident_id)] == [
        f"telegram.consumer:{consumer_id}", "android.phone.call",
    ]


def test_operator_preview_keeps_connector_bindings_private_and_does_not_publish(open_center):
    center = open_center()
    route = {"room_id": "!billing:example.org", "account_ref": "private-fixture",
             "senders": ["@operator:example.org"]}
    consumer_id = center.create_consumer("billing", "Invoice alerts", [step("start", route)])["id"]
    saved = edit_policy(center, consumer_id)
    assert saved["policy"][0]["target"] == {"room_id": route["room_id"]}
    assert "intake_token" not in saved and "token_fingerprint" not in saved
    draft = {"revision": saved["revision"], "policy": [{**saved["policy"][0], "max_repeats": 2}]}
    preview = edit_policy(center, consumer_id, draft, preview=True)
    assert preview["policy"][0]["max_repeats"] == 2
    assert edit_policy(center, consumer_id) == saved
    changed_binding = {"revision": draft["revision"], "policy": [
        {**draft["policy"][0], "target": {**route, "account_ref": "replacement-fixture"}},
    ]}
    with pytest.raises(ValidationError):
        edit_policy(center, consumer_id, changed_binding)
    changed_room = {"revision": draft["revision"], "policy": [
        {**draft["policy"][0], "target": {"room_id": "!new:example.org"}},
    ]}
    with pytest.raises(ValidationError, match="настройками коннектора"):
        edit_policy(center, consumer_id, changed_room, preview=True)
    updated = edit_policy(center, consumer_id, draft)
    assert updated["revision"] == 1
    assert center.get_consumer(consumer_id, nested_targets=True)["policy"][0]["target"] == route
    with pytest.raises(IdempotencyConflict):
        edit_policy(center, consumer_id, draft)


def test_operator_preserves_legacy_timers_without_converting_policy(open_center):
    center = open_center()
    consumer_id = center.create_consumer("billing", "Legacy alerts", [
        {"kind": "telegram", "chat_id": -123, "topic_id": 42},
        {"kind": "phone", "delay_seconds": 600},
    ])["id"]
    saved = edit_policy(center, consumer_id)
    draft = {"revision": saved["revision"], "policy": [
        saved["policy"][0], {**saved["policy"][1], "delay_seconds": 900},
    ]}
    updated = edit_policy(center, consumer_id, draft)
    assert updated["revision"] == 1
    assert updated["policy"] == draft["policy"]


def test_failed_publication_rolls_back_the_revision_and_stages(open_center, monkeypatch):
    writer, reader = open_center(), open_center()
    consumer = writer.create_consumer("billing", "Invoice alerts", [step("original", {})])
    consumer_id = consumer["id"]
    original = reader.get_consumer(consumer_id)

    def fail_audit(*args):
        raise RuntimeError("injected write failure")

    with monkeypatch.context() as fault:
        fault.setattr(writer, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match="injected write failure"):
            writer.replace_consumer_policy(consumer_id, [step("failed", {})], "editor", expected_revision=0)

    assert reader.get_consumer(consumer_id) == original
    saved = writer.replace_consumer_policy(consumer_id, [step("next", {})], "editor", expected_revision=0)
    assert saved["revision"] == 1
    assert reader.get_consumer(consumer_id) == saved
