import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, TelegramSender

@pytest.mark.parametrize('channel', ['telegram.main', 'telegram.message', 'telegram.consumer:custom'])
def test_inactive_mode_cancels_before_network(channel):
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'test.sqlite', {'producer': {'project': 'test', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        created = center.create_event('producer', 'one', {'schema': 'notify.event.v1', 'project': 'test', 'recipient': 'me', 'kind': 'incident', 'severity': 'emergency', 'title': 'test', 'body': 'test', 'dedup_key': 'one'})
        delivery_id = created['initial_delivery_id']
        if not delivery_id:
            delivery_id = center._schedule_delivery(created['incident_id'], channel, 'initial', 0)
        else:
            center._connection.execute('UPDATE deliveries SET channel=? WHERE id=?', (channel, delivery_id))
        worker = DeliveryWorker(center, TelegramSender('fixture', 'fixture', active_modes={'notify'}))
        claim = next(d for d in center.claim_due_deliveries(now_epoch=10**12) if d['id'] == delivery_id)
        real_payload = center.delivery_payload
        def log_payload(delivery):
            payload = real_payload(delivery)
            payload['incident']['severity'] = 'warning'
            return payload
        with patch.object(center, 'delivery_payload', side_effect=log_payload), patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as network:
            worker.deliver(claim)
        assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()['status'] == 'cancelled'
        network.assert_not_called()
        center._connection.close()


def test_timeout_in_legacy_edit_remains_uncertain_and_not_reclaimed():
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'test.sqlite', {'producer': {'project': 'test', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        created = center.create_event('producer', 'edit-timeout', {'schema': 'notify.event.v1', 'project': 'test', 'recipient': 'me', 'kind': 'incident', 'severity': 'emergency', 'title': 'test', 'body': 'test', 'dedup_key': 'edit-timeout'})
        delivery_id = created['initial_delivery_id']
        center._connection.execute("UPDATE deliveries SET channel='telegram.edit' WHERE id=?", (delivery_id,))
        claim = next(d for d in center.claim_due_deliveries(now_epoch=10**12) if d['id'] == delivery_id)
        real_payload = center.delivery_payload
        def edit_payload(delivery):
            payload = real_payload(delivery)
            payload['target'] = {'chat_id': 'fixture', 'message_id': 123, 'source_delivery_id': 'fixture'}
            return payload
        worker = DeliveryWorker(center, TelegramSender('fixture', 'fixture', active_modes={'notify'}))
        with patch.object(center, 'delivery_payload', side_effect=edit_payload), patch('urllib.request.urlopen', side_effect=TimeoutError('lost response')):
            worker.deliver(claim)
        status = center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()['status']
        assert status == 'uncertain'
        assert not any(d['id'] == delivery_id for d in center.claim_due_deliveries(now_epoch=10**12 + 1000))
        center._connection.close()


def test_timeout_remains_uncertain_and_is_not_reclaimed():
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'test.sqlite', {'producer': {'project': 'test', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        created = center.create_event('producer', 'two', {'schema': 'notify.event.v1', 'project': 'test', 'recipient': 'me', 'kind': 'incident', 'severity': 'emergency', 'title': 'test', 'body': 'test', 'dedup_key': 'two'})
        worker = DeliveryWorker(center, TelegramSender('fixture', 'fixture', active_modes={'notify'}))
        claim = next(d for d in center.claim_due_deliveries(now_epoch=10**12) if d['id'] == created['initial_delivery_id'])
        with patch('urllib.request.urlopen', side_effect=TimeoutError('lost response')):
            worker.deliver(claim)
        assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (claim['id'],)).fetchone()['status'] == 'uncertain'
        assert not any(d['id'] == claim['id'] for d in center.claim_due_deliveries(now_epoch=10**12 + 1000))
        center._connection.close()

def test_inactive_legacy_edit_branch_is_definite_without_network():
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'test.sqlite', {'producer': {'project': 'test', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        created = center.create_event('producer', 'edit', {'schema': 'notify.event.v1', 'project': 'test', 'recipient': 'me', 'kind': 'incident', 'severity': 'emergency', 'title': 'test', 'body': 'test', 'dedup_key': 'edit'})
        delivery_id = created['initial_delivery_id']
        center._connection.execute("UPDATE deliveries SET channel='telegram.edit' WHERE id=?", (delivery_id,))
        claim = next(d for d in center.claim_due_deliveries(now_epoch=10**12) if d['id'] == delivery_id)
        real_payload = center.delivery_payload
        def edit_payload(delivery):
            payload = real_payload(delivery)
            payload['incident']['severity'] = 'warning'
            payload['target'] = {'chat_id': 'fixture', 'message_id': 123, 'source_delivery_id': 'fixture'}
            return payload
        worker = DeliveryWorker(center, TelegramSender('fixture', 'fixture', active_modes={'notify'}))
        with patch.object(center, 'delivery_payload', side_effect=edit_payload), patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as network:
            worker.deliver(claim)
        assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()['status'] == 'cancelled'
        network.assert_not_called()
        center._connection.close()
