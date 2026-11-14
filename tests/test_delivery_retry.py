"""Uncertain card dependencies must wait without a hot claim loop or replay."""
import tempfile
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker


class CardDependencyRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.center = NotificationCenter(Path(temporary.name) / 'notice.db',
            {'source': {'project': 'dependency-test', 'max_severity': 'emergency'}},
            default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        created = self.center.create_event('source', 'event', {
            'schema': 'notify.event.v1', 'kind': 'incident', 'project': 'dependency-test',
            'recipient': 'me', 'severity': 'emergency', 'title': 'Dependency fixture',
            'dedup_key': 'dependency'})
        self.incident = created['incident_id']
        self.original = created['initial_delivery_id']
        self.followup = self.center._schedule_delivery(
            self.incident, 'telegram.main', 'follow-up', time.time())
        with self.center._connection:
            self.center._connection.execute(
                "UPDATE deliveries SET status='uncertain' WHERE id=?", (self.original,))
            self.center._connection.execute(
                'UPDATE deliveries SET attempt=4000 WHERE id=?', (self.followup,))

    def test_old_unknown_dependency_backs_off_and_recovers_after_receipt(self):
        now = time.time()
        with patch('notification_center.core.time.time', return_value=now):
            claim = self.center.claim_due_deliveries(delivery_id=self.followup)[0]
            self.assertFalse(self.center.reserve_delivery_send(
                claim['id'], claim['claimed_at'], claim['attempt']))
        waiting = dict(self.center._connection.execute(
            'SELECT * FROM deliveries WHERE id=?', (self.followup,)).fetchone())
        self.assertGreaterEqual(waiting['due_at'], now + 900)
        self.assertLessEqual(waiting['due_at'], now + 1800)
        self.assertEqual([], self.center.claim_due_deliveries(
            now_epoch=now + 16, delivery_id=self.followup))
        original = self.center._connection.execute(
            'SELECT status,attempt FROM deliveries WHERE id=?', (self.original,)).fetchone()
        self.assertEqual(('uncertain', 0), tuple(original))

        # Reconciliation supplies the original receipt, never a second send.
        self.center.complete_delivery(self.original, 'sent',
            result={'chat_id': '-100', 'message_id': 42})
        with patch('notification_center.core.time.time', return_value=time.time() + 16):
            claim = self.center.claim_due_deliveries(delivery_id=self.followup)[0]
            self.assertTrue(self.center.reserve_delivery_send(
                claim['id'], claim['claimed_at'], claim['attempt']))
        self.assertEqual(42, self.center.original_telegram_card(self.incident)['message_id'])


class InvalidRouteRetryTests(unittest.TestCase):
    def test_invalid_immutable_route_is_terminal_before_external_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            center = NotificationCenter(Path(temporary) / 'notice.db',
                {'source': {'project': 'route-test', 'max_severity': 'critical',
                            'agent_jobs': ['health-diagnosis']}}, default_quiet_hours=[])
            self.addCleanup(center._connection.close)
            created = center.create_event('source', 'event', {
                'schema': 'notify.event.v1', 'kind': 'incident', 'project': 'route-test',
                'recipient': 'me', 'severity': 'critical', 'title': 'Route fixture',
                'dedup_key': 'route', 'event_type': 'health.degraded',
                'source_id': 'probe', 'host_id': 'fixture', 'signal_type': 'unhealthy',
                'correlation_id': 'episode'})
            delivery_id = created['agent_job_delivery_id']
            with center._connection:
                center._connection.execute('UPDATE deliveries SET target_json=? WHERE id=?',
                    (json.dumps({'incident_model': 'unsupported'}), delivery_id))
            adapter = Mock()
            worker = DeliveryWorker(center, Mock(), agent_jobs={'health-diagnosis': adapter})
            worker.deliver(center.claim_due_deliveries(delivery_id=delivery_id)[0])
            row = center._connection.execute('SELECT status,result_json FROM deliveries WHERE id=?',
                                            (delivery_id,)).fetchone()
            self.assertEqual('failed', row['status'])
            self.assertEqual('not_admitted', json.loads(row['result_json'])['admission'])
            self.assertEqual([], center.claim_due_deliveries(
                now_epoch=time.time() + 3600, delivery_id=delivery_id))
            adapter.send.assert_not_called()
            adapter.send_with_progress.assert_not_called()
