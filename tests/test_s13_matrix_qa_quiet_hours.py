"""S13: exercise the actual delivery guard, with no external transports."""
import copy
import json
import uuid
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker


CONSUMER = 'consumer_7ad31d4110d744259bde4ee7580890e4'
PROJECT = 'noticeplace-qa-stop-20261023b'
A = '@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com'
B = '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com'
ROUTE = {'account_ref': 'matrix:ab023a689e926bf99d924e933ebd',
         'room_id': '!vmxuOOunnmTUeuEdvi:chat.bezrabotnyi.com',
         'target': B, 'participants': [A, B]}
NIGHT = datetime(2030, 1, 2, 2, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def center(tmp_path):
    value = NotificationCenter(tmp_path / 'notice.sqlite3', {})
    yield value
    value._connection.close()


def arrange(center, *, authorized=True, route=None, fallback=False, project=PROJECT,
            quiet_hours=(), telemetry=None):
    policy = [{'id': 'step-1', 'platform': 'matrix', 'action': 'call',
               'target': copy.deepcopy(ROUTE if route is None else route),
               'retry_interval_seconds': 60, 'max_repeats': 1}]
    if fallback:
        policy.append({'id': 'phone', 'previous_step_id': 'step-1',
                       'platform': 'phone', 'action': 'call', 'target': {},
                       'retry_interval_seconds': 60, 'max_repeats': 1})
    # Only this disposable SQLite fixture receives the production grant's ID.
    identity = uuid.UUID(CONSUMER.removeprefix('consumer_')) if authorized else uuid.uuid4()
    with patch('notification_center.core.uuid.uuid4', return_value=identity):
        consumer = center.create_consumer(project, 'QA fixture', policy,
                                          quiet_hours=None if quiet_hours is None else list(quiet_hours))
    event = {'schema': 'notify.event.v1', 'kind': 'notification', 'severity': 'important',
             'project': project, 'recipient': 'matrix-qa-bots', 'title': 'QA',
             'body': 'Isolated fixture', 'dedup_key': 's13-night', **(telemetry or {})}
    created = center.create_event(consumer['intake_token'], 's13-night', event)
    claims = center.claim_due_deliveries(now_epoch=NIGHT.timestamp(), limit=1)
    return consumer, created, claims


def deliver(center, claim, result=None):
    matrix = Mock()
    matrix.send.return_value = result or {'answered': True, 'actor': 'matrix:' + B,
                                         'call_id': 'offline-receipt', 'status': 'completed'}
    worker = DeliveryWorker(center, Mock(), matrix_call=matrix,
                            android_phone_quiet_start_hour=0, android_phone_quiet_end_hour=12)
    with patch('notification_center.http_api.datetime') as clock:
        clock.now.return_value = NIGHT
        worker.deliver(claim)
    row = dict(center._connection.execute('SELECT * FROM deliveries WHERE id=?', (claim['id'],)).fetchone())
    return matrix, row


def test_explicit_empty_quiet_hours_admits_only_authorized_matrix_route(center):
    consumer, created, claims = arrange(center)
    assert center.get_consumer(consumer['id'])['quiet_hours'] == []
    assert len(claims) == 1
    matrix, row = deliver(center, claims[0])
    assert row['status'] == 'sent', row['last_error']
    matrix.send.assert_called_once()
    assert matrix.send.call_args.args[0]['destination'] == ROUTE
    assert json.loads(row['result_json'])['call_id'] == 'offline-receipt'
    assert center.get_incident(created['incident_id'])['acknowledged_at'] is not None


def test_none_inherits_consumer_quiet_hours_and_defers_call(center):
    consumer, created, claims = arrange(center, quiet_hours=None)
    assert center.get_consumer(consumer['id'])['quiet_hours']
    assert claims == []
    row = center._connection.execute('SELECT status,due_at FROM deliveries WHERE id=?',
                                     (created['initial_delivery_id'],)).fetchone()
    assert row['status'] == 'queued' and row['due_at'] > NIGHT.timestamp()


@pytest.mark.parametrize('change', [
    {'authorized': False},
    {'project': 'ordinary-human-service'},
    {'route': {**ROUTE, 'target': '@human:example.org', 'participants': [A, '@human:example.org']}},
    {'route': {**ROUTE, 'participants': [A, B, '@human:example.org']}},
    {'route': {**ROUTE, 'room_id': '!unverified:example.org'}},
    {'route': {**ROUTE, 'account_ref': 'matrix:other'}},
    {'route': {**ROUTE, 'target': A}},
    {'fallback': True},
    {'route': {**ROUTE, 'phone_fallback': True}},
], ids=['ungranted-consumer', 'other-project', 'human', 'third-member', 'unknown-room',
        'other-account', 'reverse-direction', 'phone-stage', 'extra-route-fallback'])
def test_unverified_or_human_routes_keep_global_night_guard(center, change):
    _, _, claims = arrange(center, **change,
                           telemetry={'qa': True, 'isolated': True, 'event_type': 'qa.matrix.voice',
                                      'telemetry': {'isolated_qa': True, 'skip_quiet_hours': True}})
    assert len(claims) == 1
    matrix, row = deliver(center, claims[0])
    matrix.send.assert_not_called()
    assert row['status'] == 'cancelled' and 'тихие часы' in row['last_error']


def test_current_policy_revocation_after_claim_blocks_old_snapshot(center):
    consumer, _, claims = arrange(center)
    center._connection.execute('UPDATE consumer_policy_stages SET enabled=0 WHERE consumer_id=?',
                               (consumer['id'],))
    matrix, row = deliver(center, claims[0])
    matrix.send.assert_not_called()
    assert row['status'] == 'cancelled'


def test_stale_delivery_target_cannot_borrow_current_route_grant(center):
    _, _, claims = arrange(center)
    claims[0]['target_json'] = json.dumps({**ROUTE, 'room_id': '!unverified:example.org'})
    matrix, row = deliver(center, claims[0])
    matrix.send.assert_not_called()
    assert row['status'] == 'cancelled' and 'тихие часы' in row['last_error']


def test_new_consumer_quiet_window_after_claim_is_not_bypassed(center):
    consumer, _, claims = arrange(center)
    rules = [{'start': '00:00', 'end': '12:00', 'timezone': 'Europe/Moscow', 'suppress': ['call']}]
    center._connection.execute('UPDATE consumers SET quiet_hours_json=? WHERE id=?',
                               (json.dumps(rules), consumer['id']))
    matrix, row = deliver(center, claims[0])
    matrix.send.assert_not_called()
    assert row['status'] == 'cancelled'


def test_operator_kill_switch_still_blocks_authorized_qa(center):
    _, _, claims = arrange(center)
    center.set_runtime_setting('automatic_calls_enabled', '0')
    matrix, row = deliver(center, claims[0])
    matrix.send.assert_not_called()
    assert row['status'] == 'cancelled'
    assert row['last_error'] == 'automatic calls disabled by operator'


def test_unknown_matrix_result_does_not_schedule_phone_or_retry(center):
    _, created, claims = arrange(center)
    matrix, row = deliver(center, claims[0], {'answered': None, 'uncertain': True})
    matrix.send.assert_called_once()
    assert row['status'] == 'uncertain'
    rows = center._connection.execute('SELECT channel,attempt FROM deliveries WHERE incident_id=?',
                                      (created['incident_id'],)).fetchall()
    assert [(r['channel'], r['attempt']) for r in rows] == [('matrix.call', 1)]
    assert center.get_incident(created['incident_id'])['acknowledged_at'] is None
