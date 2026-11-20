import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from notification_center import consumer_onboarding as onboarding
from notification_center.core import (
    AuthorizationError, CONNECTION_TEST_EVENT, IdempotencyConflict, NotificationCenter, ValidationError,
)
from notification_center.http_api import DeliveryWorker


def matrix_policy(room="!first:example.test"):
    return [
        {"platform": "matrix", "action": "message", "id": "message",
         "target": {"room_id": room}, "retry_interval_seconds": 5, "max_repeats": 5},
        {"platform": "phone", "action": "call", "id": "call", "previous_step_id": "message",
         "target": {}, "retry_interval_seconds": 30, "max_repeats": 2},
    ]


@pytest.fixture
def center(tmp_path):
    value = NotificationCenter(tmp_path / "notice.sqlite", {}, default_quiet_hours=[])
    yield value
    value._connection.close()


def event():
    return {
        "schema": "notify.event.v1", "kind": "notification", "project": "fixture",
        "recipient": "team", "severity": "notice", "title": "Fixture", "body": "Local test", "dedup_key": "sample",
    }


def test_rotating_has_overlap_and_keeps_identity_policy_and_intake_idempotency(center):
    original = center.create_consumer("fixture", "Project", matrix_policy())
    consumer = original["id"]
    before = center.get_consumer(consumer)["policy"]
    first = center.create_event(original["intake_token"], "original-event", event())
    issued = onboarding.rotate(center, consumer, "issue", original["token_fingerprint"], "fixture-operator")
    replacement = issued["intake_token"]
    fingerprint = issued["replacement"]["fingerprint"]
    assert {center._token_scope(value)["consumer_id"] for value in (original["intake_token"], replacement)} == {consumer}
    assert center.create_event(replacement, "original-event", event())["event_id"] == first["event_id"]
    with pytest.raises(IdempotencyConflict):
        onboarding.rotate(center, consumer, "issue", original["token_fingerprint"], "other-operator")

    onboarding.rotate(center, consumer, "activate", fingerprint, "fixture-operator")
    with pytest.raises(AuthorizationError):
        center._token_scope(original["intake_token"])
    assert center._token_scope(replacement)["consumer_id"] == consumer
    assert center.get_consumer(consumer)["policy"] == before
    assert onboarding.connection_detail(center, consumer)["connection"]["token_fingerprint"] == fingerprint
    with pytest.raises(IdempotencyConflict):
        onboarding.rotate(center, consumer, "issue", original["token_fingerprint"], "stale-operator")

    next_token = onboarding.rotate(center, consumer, "issue", fingerprint, "fixture-operator")
    onboarding.rotate(center, consumer, "cancel", next_token["replacement"]["fingerprint"], "fixture-operator")
    assert center._token_scope(replacement)["consumer_id"] == consumer
    with pytest.raises(AuthorizationError):
        center._token_scope(next_token["intake_token"])
    audit = center._connection.execute(
        "SELECT payload_json FROM audit_events WHERE type LIKE 'consumer_token_replacement_%'",
    ).fetchall()
    assert len(audit) == 4
    public = json.dumps(onboarding.connection_detail(center, consumer)) + str([row[0] for row in audit])
    assert all(secret not in public for secret in (replacement, original["intake_token"], next_token["intake_token"]))


def test_two_connections_cannot_issue_two_tokens_for_the_same_revision(tmp_path):
    path = tmp_path / "shared.sqlite"
    first = NotificationCenter(path, {})
    second = NotificationCenter(path, {}, bootstrap_store=False)
    original = first.create_consumer("fixture", "Project", matrix_policy())

    def issue(center):
        try:
            return onboarding.rotate(center, original["id"], "issue", original["token_fingerprint"], "operator")
        except IdempotencyConflict:
            return None

    try:
        with ThreadPoolExecutor(2) as pool:
            issued = [receipt for receipt in pool.map(issue, (first, second)) if receipt]
        assert len(issued) == 1
        assert second._token_scope(issued[0]["intake_token"])["consumer_id"] == original["id"]
    finally:
        first._connection.close()
        second._connection.close()


@pytest.mark.parametrize("outcome", ["sent", "failed"])
def test_connection_message_uses_one_step_and_exact_receipt(center, outcome):
    source = center.create_consumer("fixture", "Project", matrix_policy())
    other = center.create_consumer("fixture", "Other source", matrix_policy())
    request_id = "a" * 32
    with pytest.raises(AuthorizationError):
        onboarding.queue_test(center, source["id"], other["intake_token"], request_id)
    with pytest.raises(ValidationError):
        center.create_event(source["intake_token"], "forged-test", {**event(), "event_type": CONNECTION_TEST_EVENT})

    receipt = onboarding.queue_test(center, source["id"], source["intake_token"], request_id)["test"]
    assert receipt["state"] == "queued"
    assert receipt == onboarding.queue_test(center, source["id"], source["intake_token"], request_id)["test"]
    assert onboarding.connection_detail(center, other["id"], request_id)["test"] is None
    [delivery] = center.claim_due_deliveries(channel_group="message")
    assert delivery["channel"] == "matrix.message"
    assert json.loads(delivery["target_json"])["room_id"] == "!first:example.test"
    assert center.reserve_delivery_send(delivery["id"], delivery["claimed_at"], delivery["attempt"])
    center.complete_delivery(delivery["id"], outcome, result={"event_id": "$fixture-message"} if outcome == "sent" else None,
                             claimed_at=delivery["claimed_at"], attempt=delivery["attempt"])
    [stored] = center._connection.execute("SELECT channel, status FROM deliveries").fetchall()
    assert (stored["channel"], stored["status"]) == ("matrix.message", outcome)
    status = onboarding.connection_detail(center, source["id"], request_id)["test"]
    assert onboarding.connection_detail(center, source["id"])["test"] == status
    assert status["state"] == outcome
    assert status["provider_event_id"] == ("$fixture-message" if outcome == "sent" else None)


def test_connection_delivery_timeout_does_not_retry_or_escalate(center):
    class TimeoutSender:
        def send(self, payload, key):
            raise TimeoutError("the homeserver outcome is unknown")

    source = center.create_consumer("fixture", "Project", matrix_policy())
    request_id = "b" * 32
    onboarding.queue_test(center, source["id"], source["intake_token"], request_id)
    worker = DeliveryWorker(center, None, message_adapters={"matrix.message": TimeoutSender()})

    [delivery] = worker.claim_due(channel_group="message")
    worker.deliver(delivery)

    assert onboarding.connection_detail(center, source["id"], request_id)["test"]["state"] == "uncertain"
    assert [tuple(row) for row in center._connection.execute("SELECT channel, attempt FROM deliveries")] == [
        ("matrix.message", 1),
    ]
