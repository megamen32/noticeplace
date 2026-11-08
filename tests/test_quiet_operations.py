"""Incidents are worked internally; only emergencies and real questions interrupt."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter


class QuietOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict('os.environ', {'TELEGRAM_ACTIVE_MODES_JSON': '["decisions","notify","log"]'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.center = NotificationCenter(Path(self.temp.name) / 'test.sqlite', {
            'source': {'project': 'health-monitor', 'max_severity': 'emergency',
                       'agent_jobs': ['health-diagnosis']},
            'quiet': {'project': 'health-monitor', 'max_severity': 'emergency',
                      'suppress_owner_delivery': True},
        }, default_quiet_hours=[])

    def event(self, key='route', **fields):
        return dict({'schema': 'notify.event.v1', 'kind': 'incident', 'project': 'health-monitor',
                     'recipient': 'me', 'severity': 'critical', 'title': 'Маршрут недоступен',
                     'body': 'Проверяется источником', 'dedup_key': key}, **fields)

    def create(self, key='route', token='source', **fields):
        return self.center.create_event(token, key, self.event(key, **fields))

    def legacy_delivery(self, incident_id, status='queued', channel='telegram.main', target=None):
        now = time.time()
        with self.center._connection:
            self.center._connection.execute(
                'INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,attempt,'
                'claimed_at,target_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                ('old', incident_id, channel, 'old:message', now-1, status, 1, now,
                 json.dumps(target or {}), now, now))
        return now

    def test_untyped_critical_detection_is_internal(self):
        result = self.create()
        self.assertIsNone(result['initial_delivery_id'])
        self.assertEqual('open', self.center.get_incident(result['incident_id'])['state'])

    def test_typed_detection_still_schedules_one_executor(self):
        result = self.create(event_type='health.degraded', source_id='route-probe', host_id='router',
                             signal_type='route', correlation_id='route-episode')
        self.assertIsNone(result['initial_delivery_id'])
        self.assertIsNotNone(result['agent_job_delivery_id'])
        self.assertEqual(1, len(self.center.claim_due_deliveries(channel_group='agent')))

    def test_emergency_bypasses_old_scope_suppression(self):
        result = self.create(token='quiet', severity='emergency')
        self.assertIsNotNone(result['initial_delivery_id'])

    def test_success_digest_is_not_an_owner_notification(self):
        result = self.create(kind='notification', event_type='executor.digest', severity='important')
        self.assertIsNone(result['initial_delivery_id'])

    def test_existing_queued_noise_is_cancelled_at_claim(self):
        incident = self.create()['incident_id']
        with self.center._connection:
            self.center._connection.execute('DELETE FROM deliveries')
        self.legacy_delivery(incident)
        self.assertEqual([], self.center.claim_due_deliveries(channel_group='message'))
        row = self.center._connection.execute("SELECT status FROM deliveries WHERE id='old'").fetchone()
        self.assertEqual('cancelled', row['status'])

    def test_claimed_before_upgrade_cannot_cross_send_boundary(self):
        incident = self.create()['incident_id']
        with self.center._connection:
            self.center._connection.execute('DELETE FROM deliveries')
        claimed = self.legacy_delivery(incident, status='claimed')
        self.assertFalse(self.center.reserve_delivery_send('old', claimed, 1))
        self.assertEqual('cancelled', self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id='old'").fetchone()['status'])

    def test_unknown_delivery_is_not_requeued_or_cancelled(self):
        incident = self.create()['incident_id']
        with self.center._connection:
            self.center._connection.execute('DELETE FROM deliveries')
        self.legacy_delivery(incident, status='uncertain')
        self.assertEqual([], self.center.claim_due_deliveries(channel_group='message'))
        self.assertEqual('uncertain', self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id='old'").fetchone()['status'])

    def test_actual_pending_question_can_be_delivered(self):
        incident = self.create()['incident_id']
        self.center.create_human_request('source', {
            'schema': 'ask_human.request.v1', 'request_id': 'real-question',
            'project': 'health-monitor', 'recipient': 'me', 'mode': 'question',
            'message': 'Нужно выбрать разрешённый резервный маршрут.', 'incident_id': incident,
            'allowed_actors': ['qa-operator'],
            'native_target': {'harness': 'opencode', 'session_id': 'repair-session', 'cwd': '/tmp/repair'},
        })
        self.assertIsNotNone(self.center._schedule_delivery(incident, 'telegram.main', 'question', time.time()))

    def test_manual_ai_only_updates_the_clicked_card_and_cannot_admit_a_new_message(self):
        incident = self.create()['incident_id']
        self.legacy_delivery(incident, status='sent')
        with self.center._connection:
            self.center._connection.execute('UPDATE deliveries SET result_json=? WHERE id=?',
                (json.dumps({'chat_id':'-1001','message_id':17}), 'old'))
        self.center.apply_telegram_action(incident, 'ai', 'fixture:operator',
            {'message_id':17,'chat':{'id':-1001},'text':'Existing card'})
        target = {'health_outcome':{'status':'interrupted'},
                  'incident_card':{'chat_id':'-1001','message_id':17}}
        admitted = self.center._schedule_delivery(incident, 'telegram.main', 'manual-result', time.time(), target)
        self.assertIsNotNone(admitted)
        self.assertIsNone(self.center._schedule_delivery(incident, 'telegram.main', 'new-message', time.time()))
        self.assertIsNone(self.center._schedule_delivery(incident, 'telegram.main', 'wrong-card', time.time(),
            {**target,'incident_card':{'chat_id':'-1001','message_id':18}}))
        self.assertIsNone(self.center._schedule_delivery(incident, 'android.phone.call', 'call', time.time(), target))

    def test_business_notification_keeps_its_delivery_contract(self):
        self.assertIsNotNone(self.create(kind='notification', event_type='lead.created')['initial_delivery_id'])

    def test_many_failed_jobs_produce_one_platform_emergency(self):
        for number in range(3):
            self.create(key='post-'+str(number), event_type='health.degraded', source_id='route-probe',
                        host_id='router', signal_type='route', correlation_id='episode-'+str(number))
            delivery = self.center.claim_due_deliveries(channel_group='agent')[0]
            self.center.complete_delivery(delivery['id'], 'failed', error='agent job terminal failure')
        rows = self.center._connection.execute(
            "SELECT id,severity,occurrences FROM incidents WHERE event_type='executor.launch_failed'").fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual('emergency', rows[0]['severity'])
        self.assertEqual(3, rows[0]['occurrences'])
        self.assertEqual(1, self.center._connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE incident_id=? AND channel='telegram.main'",
            (rows[0]['id'],)).fetchone()[0])

    def test_scoped_executor_failure_cannot_escape_to_owner(self):
        consumer = self.center.create_consumer(project='health-monitor', name='Isolated Matrix route',
            policy=[{'id': 'root', 'platform': 'matrix', 'action': 'message',
                     'target': {'room_id': '!isolated:test'}, 'retry_interval_seconds': 30, 'max_repeats': 1}])
        incident = self.center.create_event(consumer['intake_token'], 'scoped-source',
                                            self.event('scoped-source'))['incident_id']
        job = self.center._schedule_delivery(incident, 'gptadmin.agent:health-diagnosis', 'job', time.time())
        self.center.complete_delivery(job, 'failed', error='executor unavailable')
        self.assertEqual(0, self.center._connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE channel LIKE 'telegram.%'").fetchone()[0])
        self.assertEqual(1, self.center._connection.execute(
            "SELECT COUNT(*) FROM deliveries d JOIN incidents i ON i.id=d.incident_id "
            "WHERE i.event_type='executor.launch_failed' AND d.channel='matrix.message'").fetchone()[0])

    def test_watchdog_probe_preserves_uncertain_without_history_audit(self):
        incident = self.create()['incident_id']
        self.legacy_delivery(incident, status='uncertain')
        with patch.object(self.center, 'latest_health_plans', side_effect=AssertionError('history scan')):
            health = self.center.service_health()
        self.assertTrue(health['service_ready'])
        self.assertEqual('degraded', health['status'])
        self.assertEqual(1, health['uncertain_deliveries'])
        self.assertIsNone(health['reconciliation_required'])
        self.assertEqual('uncertain', self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id='old'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
