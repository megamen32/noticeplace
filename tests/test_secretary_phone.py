"""Fast unit: Secretary wording/context regression, expected <1s/max5s; no real calls."""
import json
from pathlib import Path
import tempfile
import unittest
from notification_center.agentcall_phone import AgentCallPhoneAdapter
from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker

class SecretaryPhoneTests(unittest.TestCase):
    def test_secretary_is_ordinary_assistant_and_speaks_once(self):
        requests = []
        def requester(_path, payload, _timeout):
            requests.append(json.loads(payload))
            return b'{"ok":true,"call_id":"secretary-call-1"}'
        adapter = AgentCallPhoneAdapter('/fake/control.sock', requester=requester)
        result = adapter.phone_call({'incident': {
            'id': 'inc-secretary', 'event_type': 'secretary.call', 'severity': 'critical',
            'title': 'INTERNAL_TITLE', 'body': 'Никита, вам передали сообщение.',
            'operator_note': 'Обсудите встречу завтра в десять.'},
            'ai_session': {'session_id': 'foreign-session', 'harness': 'codex'}})
        self.assertEqual(result['call_id'], 'secretary-call-1')
        request = requests[0]
        self.assertTrue(request['message'].startswith('Я помощник Никиты.'))
        self.assertIn('Никита, вам передали сообщение.', request['message'])
        self.assertNotIn('INTERNAL_TITLE', request['message'])
        self.assertNotIn('Сломалось', request['message'])
        self.assertEqual(request['repeat'], 1)
        self.assertIn('Обсудите встречу завтра в десять.', request['context'])
        self.assertNotIn('Уровень critical', request['context'])
        self.assertNotIn('Agent Herder', request['context'])
        self.assertNotIn('foreign-session', json.dumps(request))
        self.assertEqual(request['agent_context'], {'event_type': 'secretary.call'})

    def test_canonical_queue_carries_note_and_retains_receipt_without_repeat(self):
        """Focused integration: canonical queue/adapter; expected1s/max10s."""
        with tempfile.TemporaryDirectory() as directory:
            center = NotificationCenter(Path(directory) / 'queue.sqlite3', {}, default_quiet_hours=[])
            consumer = center.create_consumer('exmanager-phone', 'Secretary', [{
                'id': 'once', 'platform': 'phone', 'action': 'call', 'target': {'recipient_e164': '+79990000001'},
                'retry_interval_seconds': 3600, 'max_repeats': 1}], quiet_hours=[])
            event = {'schema': 'notify.event.v1', 'project': 'exmanager-phone', 'recipient': 'owner',
                     'kind': 'notification', 'severity': 'critical', 'title': 'Сообщение от секретаря Никиты',
                     'body': 'Сообщение о встрече.', 'operator_note': 'Обсудите завтрашнюю встречу.',
                     'event_type': 'secretary.call', 'dedup_key': 'secretary-request-1'}
            created = center.create_event(consumer['intake_token'], 'secretary-request-1', event)
            requests = []
            def requester(_path, payload, _timeout):
                requests.append(json.loads(payload))
                return b'{"ok":true,"call_id":"exact-secretary-call"}'
            phone = AgentCallPhoneAdapter('/fake/control.sock', requester=requester)
            worker = DeliveryWorker(center, None, android_phone=phone,
                android_phone_quiet_start_hour=0, android_phone_quiet_end_hour=0)
            for delivery in worker.claim_due(channel_group='call'):
                worker.deliver(delivery)
            row = center._connection.execute('SELECT status,result_json FROM deliveries WHERE id=?',
                                             (created['initial_delivery_id'],)).fetchone()
            self.assertEqual(row['status'], 'sent')
            self.assertEqual(json.loads(row['result_json'])['call_id'], 'exact-secretary-call')
            self.assertIn(event['operator_note'], requests[0]['context'])
            self.assertTrue(requests[0]['message'].startswith('Я помощник Никиты.'))
            center.create_event(consumer['intake_token'], 'secretary-request-1', event)
            self.assertEqual(worker.claim_due(channel_group='call'), [])
            self.assertEqual(len(requests), 1)
            center._connection.close()

if __name__ == '__main__':
    unittest.main()
