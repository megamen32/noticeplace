"""Independent manual-card boundary checks; fixture DBs and adapters only."""
import json
from pathlib import Path
import tempfile
import time
import urllib.parse
import unittest
from unittest.mock import patch
from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec


class ManualCardQuietBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        modes = patch.dict('os.environ', {'TELEGRAM_ACTIVE_MODES_JSON': '["decisions","notify"]'})
        modes.start()
        self.addCleanup(modes.stop)
        self.center = NotificationCenter(Path(self.tmp.name) / 'db', {'fixture': {
            'project': 'fixture', 'max_severity': 'important',
            'agent_jobs': ['health-diagnosis']}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        created = self.center.create_event('fixture', 'create', {
            'schema': 'notify.event.v1', 'project': 'fixture', 'recipient': 'me',
            'kind': 'incident', 'severity': 'important', 'title': 'Проверка старой карточки',
            'body': 'Изолированный тест без отправки.', 'dedup_key': 'legacy'})
        self.incident = created['incident_id']
        self.assertIsNone(created['initial_delivery_id'])
        self.clock = time.time()
        self.card = {'chat_id': '-100123', 'message_id': 42}
        self.click = {'chat': {'id': -100123}, 'message_id': 42, 'text': 'Проверить'}
        with self.center._connection:
            self.center._connection.execute(
                "INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,attempt,target_json,result_json,created_at,updated_at) "
                "VALUES ('legacy-card',?,'telegram.main','legacy-card',0,'sent',1,'{}',?,0,0)",
                (self.incident, json.dumps(self.card)))

    def send_existing(self, delivery_id):
        sender = TelegramSender('fixture', '-100123', active_modes={'decisions', 'notify'},
            action_codec=TelegramActionCodec('x' * 32), center=self.center)
        worker = DeliveryWorker(self.center, sender)
        self.clock += 120
        claimed = self.center.claim_due_deliveries(now_epoch=self.clock,
            channel_group='message', delivery_id=delivery_id)
        self.assertEqual([delivery_id], [row['id'] for row in claimed])
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self): return b'{"ok":true,"result":{"message_id":42,"chat":{"id":-100123}}}'
        def transport(request, **_kwargs):
            requests.append((request.full_url, urllib.parse.parse_qs(request.data.decode())))
            return Response()
        with patch('notification_center.http_api.urllib.request.urlopen', side_effect=transport), \
             patch('notification_center.core.time.time', return_value=self.clock):
            worker.deliver(claimed[0])
        self.assertEqual(1, len(requests), 'requested update must reach the mocked transport once')
        self.assertTrue(requests[0][0].endswith('/editMessageText'))
        self.assertEqual(['42'], requests[0][1]['message_id'])
        self.assertEqual(['-100123'], requests[0][1]['chat_id'])
        row = self.center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
        self.assertEqual('sent', row['status'])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=time.time() + 120,
            channel_group='message', delivery_id=delivery_id))

    def request(self):
        return self.center.apply_telegram_action(self.incident, 'ai', 'fixture-human', self.click)

    def session(self):
        return self.center.record_health_agent_session(self.incident, 'session', '', 'opencode',
            'fixture-native', 'https://agent.bezrabotnyi.com', stage='health-diagnosis')

    def test_manual_session_and_outcome_retain_exact_card(self):
        self.request()
        session = self.session()
        self.send_existing(session['session_delivery_id'])
        outcome = self.center.schedule_health_outcome(self.incident, 'failed', 'failure')
        self.send_existing(outcome)
        for delivery_id in (session['session_delivery_id'], outcome):
            self.assertIsNotNone(delivery_id)
            row = self.center._connection.execute('SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
            self.assertEqual(self.card, self.center.delivery_payload(row)['target']['incident_card'])

    def test_foreign_missing_destinations_and_non_edit_transports_denied(self):
        self.request()
        cases = [
            ('telegram.main', {'health_session': {'session_id': 'native'}}),
            ('telegram.main', {'incident_card': self.card}),
            ('telegram.main', {'incident_card': {**self.card, 'message_id': 43}, 'health_session': {}}),
            ('telegram.main', {'incident_card': {**self.card, 'chat_id': '-999'}, 'health_outcome': {'status': 'interrupted'}}),
            ('telegram.edit', {'ai_card': {**self.card, 'message_id': 43}, 'health_session': {}}),
            ('telegram.edit', {**self.card, 'chat_id': '-999'}),
            ('telegram.edit', {**self.card, 'message_id': 43}),
            ('matrix.message', {'incident_card': self.card, 'health_session': {}}),
            ('android.phone.call', {'incident_card': self.card, 'health_outcome': {'status': 'interrupted'}}),
        ]
        for index, (channel, target) in enumerate(cases):
            with self.subTest(channel=channel, target=target):
                self.assertIsNone(self.center._schedule_delivery(self.incident, channel, 'boundary-' + str(index), time.time(), target))

    def test_existing_card_without_manual_request_does_not_release_session(self):
        self.assertIsNone(self.center._schedule_delivery(self.incident, 'telegram.main', 'no-request', time.time(),
            {'incident_card': self.card, 'health_session': {'session_id': 'native'}}))

    def test_manual_without_stored_destination_cannot_send_new_card(self):
        self.center._connection.execute("UPDATE deliveries SET result_json=NULL WHERE id='legacy-card'")
        self.center._connection.commit()
        self.center.apply_telegram_action(self.incident, 'ai', 'fixture-human')
        self.assertIsNone(self.center._schedule_delivery(self.incident, 'telegram.main', 'no-card', time.time(),
            {'incident_card': self.card, 'health_session': {'session_id': 'native'}}))

    def test_default_modes_allow_requested_existing_card_updates(self):
        self.request()
        with patch.dict('os.environ', {'TELEGRAM_ACTIVE_MODES_JSON': '["decisions","notify"]'}):
            session = self.session()
            outcome = self.center.schedule_health_outcome(self.incident, 'failed', 'failure')
        self.assertIsNotNone(session['session_delivery_id'], 'manual session edit lost to disabled log mode')
        self.assertIsNotNone(outcome, 'manual outcome edit lost to disabled log mode')

    def test_native_progress_refresh_retains_existing_manual_card(self):
        self.request()
        self.session()
        delivery_id = self.center.queue_incident_card_refresh(self.incident)
        self.assertIsNotNone(delivery_id, 'refresh omitted exact stored card from admission target')
        row = self.center._connection.execute('SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
        self.assertEqual(self.card, self.center.delivery_payload(row)['target']['incident_card'])

    def test_clicked_card_without_receipt_cannot_admit_outcome_as_new_send(self):
        self.center._connection.execute("UPDATE deliveries SET result_json=NULL WHERE id='legacy-card'")
        self.center._connection.commit()
        self.request()
        self.assertEqual(self.card['message_id'], self.center.manual_ai_card(self.incident)['message_id'])
        with patch.dict('os.environ', {'TELEGRAM_ACTIVE_MODES_JSON': '["decisions","notify","log"]'}):
            self.assertIsNone(self.center._schedule_delivery(self.incident, 'telegram.main', 'unsafe-ai-outcome', time.time(),
                {'ai_card': self.card, 'health_outcome': {'status': 'interrupted'}}))
            self.assertIsNone(self.center._schedule_delivery(self.incident, 'telegram.main', 'empty-ai-session', time.time(),
                {'ai_card': self.card, 'health_session': {}}))
        outcome = self.center.schedule_health_outcome(self.incident, 'interrupted', 'safe-outcome')
        self.assertIsNotNone(outcome)
        self.send_existing(outcome)

    def test_opt_in_cleanup_uses_existing_card_with_default_modes(self):
        self.center.set_runtime_setting('telegram_delete_resolved:fixture', 'true')
        sender = TelegramSender('fixture', '-100123', active_modes={'decisions', 'notify'}, center=self.center)
        worker = DeliveryWorker(self.center, sender)
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self): return b'{"ok":true,"result":true}'
        def transport(request, **_kwargs):
            requests.append((request.full_url.rsplit('/', 1)[-1], urllib.parse.parse_qs(request.data.decode())))
            return Response()
        with patch('notification_center.http_api.urllib.request.urlopen', side_effect=transport):
            self.center.resolve(self.incident, 'fixture-monitor')
            with patch('notification_center.core.time.time', return_value=time.time() + 60):
                worker.run_once()
        self.assertEqual(1, len(requests))
        self.assertEqual('deleteMessage', requests[0][0])
        self.assertEqual(['42'], requests[0][1]['message_id'])
        self.assertEqual(['-100123'], requests[0][1]['chat_id'])
        self.assertIsNone(self.center.original_telegram_card(self.incident))
        self.assertIsNone(self.center.queue_incident_card_refresh(self.incident))

    def test_claimed_cleanup_is_not_duplicated_by_concurrent_refresh(self):
        self.center.set_runtime_setting('telegram_delete_resolved:fixture', 'true')
        self.center.resolve(self.incident, 'fixture-monitor')
        claimed = self.center.claim_due_deliveries(now_epoch=time.time() + 60, channel_group='message')
        self.assertEqual(1, len(claimed))
        self.assertIsNone(self.center.queue_incident_card_refresh(self.incident))
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=time.time() + 61, channel_group='message'))

    def test_uncertain_cleanup_is_not_replayed_by_late_refresh(self):
        self.center.set_runtime_setting('telegram_delete_resolved:fixture', 'true')
        sender = TelegramSender('fixture', '-100123', center=self.center)
        worker = DeliveryWorker(self.center, sender)
        requests = []
        def transport(request, **_kwargs):
            requests.append(request.full_url.rsplit('/', 1)[-1])
            raise TimeoutError('fixture: delete outcome unknown')
        with patch.dict('os.environ', {'TELEGRAM_ACTIVE_MODES_JSON': '["decisions","notify","log"]'}), \
             patch('notification_center.http_api.urllib.request.urlopen', side_effect=transport):
            self.center.resolve(self.incident, 'fixture-monitor')
            with patch('notification_center.core.time.time', return_value=time.time() + 60):
                worker.run_once()
            self.assertEqual(['deleteMessage'], requests)
            self.assertIsNone(self.center.queue_incident_card_refresh(self.incident))
            with patch('notification_center.core.time.time', return_value=time.time() + 120):
                worker.run_once()
        self.assertEqual(['deleteMessage'], requests)
        self.assertEqual(1, self.center._connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE incident_id=? AND status='uncertain'", (self.incident,)).fetchone()[0])

    def test_unknown_agent_result_does_not_repeat_launch(self):
        calls = []
        class Adapter:
            def send(self, payload, key):
                calls.append(key)
                raise RuntimeError('fixture: unconfirmed result')
        first = self.request()
        worker = DeliveryWorker(self.center, None, agent_jobs={'health-diagnosis': Adapter()})
        claimed = self.center.claim_due_deliveries(channel_group='agent')
        self.assertEqual([first['agent_job_delivery_id']], [row['id'] for row in claimed])
        worker.deliver(claimed[0])
        row = self.center._connection.execute('SELECT status,result_json FROM deliveries WHERE id=?',
            (first['agent_job_delivery_id'],)).fetchone()
        self.assertEqual('uncertain', row['status'])
        self.assertTrue(json.loads(row['result_json'])['uncertain'])
        repeated = self.request()
        self.assertTrue(repeated['idempotent'])
        self.assertEqual(first['agent_job_delivery_id'], repeated['agent_job_delivery_id'])
        self.assertEqual([], self.center.claim_due_deliveries(channel_group='agent'))
        self.assertEqual(1, len(calls))
        messages = self.center.claim_due_deliveries(channel_group='message')
        self.assertEqual(1, len(messages))
        payload = self.center.delivery_payload(messages[0])
        self.assertEqual('interrupted', payload['health_outcome']['status'])
        self.assertEqual(self.card, payload['target']['incident_card'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
