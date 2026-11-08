"""Real adapter + worker, SQLite only; no external transport or native launch."""
import io
import time
import urllib.error
from unittest.mock import Mock

import pytest

from notification_center.core import NotificationCenter
from notification_center.gptadmin_agent import GptAdminAgentJobAdapter, DurableHealthRemediationAdapter
from notification_center.http_api import DeliveryWorker


class Response(io.BytesIO):
    status = 200


@pytest.fixture
def delivery(tmp_path):
    center = NotificationCenter(tmp_path / 'fixture.db', {'p': {
        'project': 'p', 'max_severity': 'critical', 'agent_jobs': ['health-diagnosis']}},
        default_quiet_hours=[])
    event = dict(project='p', recipient='fixture', kind='incident', severity='critical',
        title='Fixture', dedup_key='agent', event_type='health.degraded', source_id='probe',
        host_id='fixture', signal_type='failure', correlation_id='episode')
    created = center.create_event('p', 'event', event)
    yield center, created['agent_job_delivery_id']
    center._connection.close()


def adapter(runner):
    return GptAdminAgentJobAdapter('health-diagnosis',
        'https://fixture.invalid/webhooks/v1/fixture', 'unit-fixture-only', runner=runner)


@pytest.mark.parametrize('reply', [
    b'{"job_id":"accepted-fixture",', b'{"status":"accepted"}',
    b'{"job_id":"job"}', b'{"job_id":true,"status":"accepted"}',
    b'[]', b'x'*65537,
    urllib.error.HTTPError('https://fixture.invalid', 503, 'unknown', {}, None),
    urllib.error.URLError(TimeoutError('receipt lost')),
], ids=['truncated', 'missing-id', 'missing-status', 'invalid-id', 'array', 'oversize', 'http503', 'timeout'])
def test_ambiguous_real_admission_is_uncertain_without_retry_or_fallback(delivery, reply):
    center, delivery_id = delivery
    calls = []
    def runner(request, **kwargs):
        calls.append(request.get_method())
        if request.get_method() != 'POST':
            raise AssertionError('Malformed admission must not start polling')
        if isinstance(reply, Exception):
            raise reply
        return Response(reply)
    fallback = Mock()
    transport = DurableHealthRemediationAdapter(adapter(runner), fallback)
    worker = DeliveryWorker(center, Mock(), agent_jobs={'health-diagnosis': transport})
    worker.deliver(center.claim_due_deliveries(delivery_id=delivery_id)[0])
    row = center._connection.execute('SELECT status,result_json FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
    assert row['status'] == 'uncertain'
    assert 'agent_receipt_unconfirmed' in row['result_json']
    assert center.claim_due_deliveries(delivery_id=delivery_id, now_epoch=time.time()+3600) == []
    assert calls == ['POST']
    fallback.send_with_progress.assert_not_called()


@pytest.mark.parametrize('error', [
    urllib.error.HTTPError('https://fixture.invalid', 401, 'rejected', {}, None),
    urllib.error.URLError(ConnectionRefusedError('not connected')),
    urllib.error.HTTPError('https://fixture.invalid', 503, 'draining', {},
        io.BytesIO(b'{"executed":false,"retryable":true}')),
])
def test_proven_pre_admission_failure_still_allows_retry(delivery, error):
    center, delivery_id = delivery
    runner = Mock(side_effect=error)
    worker = DeliveryWorker(center, Mock(), agent_jobs={'health-diagnosis': adapter(runner)})
    worker.deliver(center.claim_due_deliveries(delivery_id=delivery_id)[0])
    assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()[0] == 'queued'
    assert len(center.claim_due_deliveries(delivery_id=delivery_id, now_epoch=time.time()+3600)) == 1
    runner.assert_called_once()


def test_explicit_refusal_can_use_existing_fallback():
    runner = Mock(side_effect=urllib.error.URLError(ConnectionRefusedError('not connected')))
    fallback = Mock()
    fallback.send_with_progress.return_value = {'job_id': 'fixture', 'status': 'completed'}
    transport = DurableHealthRemediationAdapter(adapter(runner), fallback)
    assert transport.send({'incident': {'id': 'fixture'}}, 'fixture-key')['status'] == 'completed'
    runner.assert_called_once()
    fallback.send_with_progress.assert_called_once()


def test_valid_real_receipt_keeps_normal_completion(delivery):
    center, delivery_id = delivery
    runner = Mock(side_effect=lambda *a, **k: Response(b'{"job_id":"fixture","status":"completed"}'))
    worker = DeliveryWorker(center, Mock(), agent_jobs={'health-diagnosis': adapter(runner)})
    worker.deliver(center.claim_due_deliveries(delivery_id=delivery_id)[0])
    assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()[0] == 'sent'
    runner.assert_called_once()
