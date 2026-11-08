"""A definitive original outcome wakes observation, never an UNKNOWN replay."""
import time

import pytest

from notification_center.core import NotificationCenter
from notification_center.delivery_retry import CARD_RECEIPT_PENDING


@pytest.fixture
def cards(tmp_path):
    center = NotificationCenter(tmp_path / 'fixture.db',
        {'p': {'project': 'p', 'max_severity': 'emergency'}}, default_quiet_hours=[])
    def event(key):
        return center.create_event('p', key, dict(project='p', recipient='fixture',
            kind='incident', severity='emergency', title='Fixture', dedup_key=key))
    original = event('owner')
    unrelated = event('unrelated')
    followup = center._schedule_delivery(original['incident_id'], 'telegram.main', 'followup', time.time())
    future = time.time()+3600
    with center._connection:
        center._connection.execute("UPDATE deliveries SET status='sending' WHERE id=?", (original['initial_delivery_id'],))
        center._connection.execute('UPDATE deliveries SET attempt=4000 WHERE id=?', (followup,))
        center._connection.execute('UPDATE deliveries SET due_at=?,last_error=? WHERE id=?',
            (future, CARD_RECEIPT_PENDING, unrelated['initial_delivery_id']))
    claim = center.claim_due_deliveries(delivery_id=followup)[0]
    assert not center.reserve_delivery_send(followup, claim['claimed_at'], claim['attempt'])
    yield center, original, followup, unrelated, future
    center._connection.close()


@pytest.mark.parametrize('outcome', ['failed', 'cancelled', 'superseded'])
def test_definitive_terminal_wakes_only_dependent_card(cards, outcome):
    center, original, followup, unrelated, future = cards
    original_id = original['initial_delivery_id']
    center.complete_delivery(original_id, outcome, schedule_failure_neighbours=False)
    claims = center.claim_due_deliveries(delivery_id=followup)
    assert len(claims) == 1
    assert center.reserve_delivery_send(followup, claims[0]['claimed_at'], claims[0]['attempt'])
    assert center.claim_due_deliveries(delivery_id=original_id, now_epoch=time.time()+3600) == []
    assert center._connection.execute('SELECT due_at FROM deliveries WHERE id=?',
        (unrelated['initial_delivery_id'],)).fetchone()[0] == future


def test_unknown_does_not_wake_or_replay_original(cards):
    center, original, followup, _, _ = cards
    before = center._connection.execute('SELECT due_at FROM deliveries WHERE id=?', (followup,)).fetchone()[0]
    center.complete_delivery(original['initial_delivery_id'], 'uncertain', schedule_failure_neighbours=False)
    assert center._connection.execute('SELECT due_at FROM deliveries WHERE id=?', (followup,)).fetchone()[0] == before
    assert center.claim_due_deliveries(delivery_id=original['initial_delivery_id'], now_epoch=time.time()+3600) == []
    claim = center.claim_due_deliveries(delivery_id=followup, now_epoch=time.time()+3600)[0]
    assert not center.reserve_delivery_send(followup, claim['claimed_at'], claim['attempt'])


def test_stale_completion_cannot_wake_followup(cards):
    center, original, followup, _, _ = cards
    before = center._connection.execute('SELECT due_at FROM deliveries WHERE id=?', (followup,)).fetchone()[0]
    center.complete_delivery(original['initial_delivery_id'], 'failed', attempt=999, schedule_failure_neighbours=False)
    assert center._connection.execute('SELECT due_at FROM deliveries WHERE id=?', (followup,)).fetchone()[0] == before
    assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (original['initial_delivery_id'],)).fetchone()[0] == 'sending'


def test_woken_waiter_still_respects_another_unknown_original(cards):
    center, original, followup, _, _ = cards
    obstruction = center._schedule_delivery(original['incident_id'], 'telegram.main', 'unknown-other', time.time())
    with center._connection:
        center._connection.execute("UPDATE deliveries SET status='uncertain' WHERE id=?", (obstruction,))
    center.complete_delivery(original['initial_delivery_id'], 'failed', schedule_failure_neighbours=False)
    claim = center.claim_due_deliveries(delivery_id=followup)[0]
    assert not center.reserve_delivery_send(followup, claim['claimed_at'], claim['attempt'])
    assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (obstruction,)).fetchone()[0] == 'uncertain'


def test_wake_reaches_same_card_owner_sibling(cards):
    center, original, followup, sibling, _ = cards
    with center._connection:
        center._connection.execute('UPDATE incidents SET telegram_card_owner_id=? WHERE id=?',
            (original['incident_id'], sibling['incident_id']))
        center._connection.execute('UPDATE deliveries SET incident_id=? WHERE id=?', (sibling['incident_id'], followup))
        center._connection.execute("UPDATE deliveries SET status='cancelled' WHERE id=?", (sibling['initial_delivery_id'],))
    center.complete_delivery(original['initial_delivery_id'], 'failed', schedule_failure_neighbours=False)
    claim = center.claim_due_deliveries(delivery_id=followup)[0]
    assert center.reserve_delivery_send(followup, claim['claimed_at'], claim['attempt'])
