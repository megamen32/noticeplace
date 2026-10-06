"""Real queue/adapter boundary for call budgets and terminal health updates."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.health_workflow import HealthWorkflow
from notification_center.http_api import DeliveryWorker, TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec


class IncidentOutcomesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.tokens = {'producer': {'project': 'fleet', 'max_severity': 'emergency'}}
        self.center = NotificationCenter(self.path, self.tokens, default_quiet_hours=[])
        self.now = time.time()
        self.serial = 0

    def tearDown(self):
        self.center._connection.close()
        self.tmp.cleanup()

    def episode(self, host='server-44', signal='load', recipient='me'):
        self.serial += 1
        event = {
            'schema': 'notify.event.v1', 'project': 'fleet', 'recipient': recipient,
            'kind': 'incident', 'severity': 'critical', 'title': 'Повышена нагрузка',
            'body': 'Мониторинг проверяет сервер.', 'dedup_key': f'fleet:{host}:{signal}',
            'event_type': 'health.degraded', 'source_id': f'fleet:{host}',
            'host_id': host, 'signal_type': signal, 'correlation_id': f'episode:{self.serial}',
        }
        created = self.center.create_event('producer', f'intake:{self.serial}', event)
        return created, event

    def call(self, created, channel='android.phone.call'):
        return self.center.schedule_escalation(created['incident_id'], channel, 0)

    def reserve(self, center, delivery, now):
        with patch('notification_center.core.time.time', return_value=now):
            return center.reserve_delivery_send(delivery['id'], delivery['claimed_at'], delivery['attempt'])

    def claim_calls(self, center=None):
        return (center or self.center).claim_due_deliveries(now_epoch=self.now + 1, channel_group='call')

    def resolve(self, event):
        return self.center.resolve_event('producer', f'recovery:{event["correlation_id"]}', {
            **{k: event[k] for k in ('project', 'recipient', 'dedup_key', 'source_id', 'host_id', 'signal_type', 'correlation_id')},
            'schema': 'notify.event.v1', 'action': 'resolve', 'event_type': 'health.recovered',
        })

    def outcomes(self, incident):
        return [dict(r) for r in self.center._connection.execute(
            "SELECT * FROM deliveries WHERE incident_id=? AND json_type(target_json,'$.health_outcome')='object'", (incident,))]

    def test_new_episode_cpu_load_and_channels_share_one_provider_call(self):
        calls = []
        class Phone:
            can_phone_call = True
            def phone_call(self, payload):
                calls.append(payload['incident']['id'])
        class Telegram:
            def send(self, payload):
                raise AssertionError('a health call must not send pre-call noise')
        worker = DeliveryWorker(self.center, Telegram(), android_phone=Phone(), android_phone_quiet_start_hour=0, android_phone_quiet_end_hour=0)
        first, event = self.episode()
        self.call(first)
        worker.deliver(self.claim_calls()[0])
        self.resolve(event)
        second, _ = self.episode(signal='cpu')
        delivery_id = self.call(second)
        worker.deliver(self.claim_calls()[0])
        self.assertEqual([first['incident_id']], calls)
        self.assertEqual('cancelled', self.center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery_id,)).fetchone()[0])

    def test_restart_preserves_exact_hour_boundary_and_other_host_is_independent(self):
        first, _ = self.episode()
        self.call(first)
        self.assertTrue(self.reserve(self.center, self.claim_calls()[0], self.now))
        reopened = NotificationCenter(self.path, self.tokens, default_quiet_hours=[])
        try:
            second, _ = self.episode(signal='cpu')
            self.call(second, 'matrix.call')
            self.assertFalse(self.reserve(reopened, self.claim_calls(reopened)[0], self.now+3599))
            third, _ = self.episode()
            self.call(third)
            self.assertTrue(self.reserve(reopened, self.claim_calls(reopened)[0], self.now+3600))
            other, _ = self.episode(host='server-100')
            self.call(other)
            self.assertTrue(self.reserve(reopened, self.claim_calls(reopened)[0], self.now+3601))
        finally:
            reopened._connection.close()

    def test_cross_connection_concurrent_calls_have_only_one_winner(self):
        a, _ = self.episode()
        b, _ = self.episode(signal='cpu')
        self.call(a)
        self.call(b, 'matrix.call')
        claims = self.claim_calls()
        other = NotificationCenter(self.path, self.tokens, default_quiet_hours=[])
        try:
            with patch('notification_center.core.time.time', return_value=self.now), ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: pair[0].reserve_delivery_send(pair[1]['id'], pair[1]['claimed_at'], pair[1]['attempt']), [(self.center, claims[0]), (other, claims[1])]))
            self.assertEqual([False, True], sorted(results))
        finally:
            other._connection.close()

    def test_other_signal_and_recipient_are_independent(self):
        for kwargs in ({}, {'signal': 'disk'}, {'recipient': 'another'}):
            created, _ = self.episode(**kwargs)
            self.call(created)
            self.assertTrue(self.reserve(self.center, self.claim_calls()[0], self.now))

    def test_existing_attempt_bootstraps_budget_without_new_call(self):
        old, _ = self.episode()
        delivery_id = self.call(old)
        self.center._connection.execute("UPDATE deliveries SET status='uncertain',claimed_at=?,updated_at=? WHERE id=?", (self.now-10, self.now-5, delivery_id))
        self.center._connection.commit()
        new, _ = self.episode(signal='cpu')
        self.call(new)
        self.assertFalse(self.reserve(self.center, self.claim_calls()[0], self.now))

    def test_stale_claim_and_quiet_cancel_do_not_consume_budget(self):
        first, _ = self.episode()
        self.call(first)
        claim = self.claim_calls()[0]
        self.assertFalse(self.center.reserve_delivery_send(claim['id'], claim['claimed_at'], claim['attempt']+1))
        self.center.complete_delivery(claim['id'], 'cancelled', 'quiet hours')
        second, _ = self.episode(signal='cpu')
        self.call(second)
        self.assertTrue(self.reserve(self.center, self.claim_calls()[0], self.now))

    def test_live_setting_changes_interval_without_restart(self):
        a, _ = self.episode()
        self.call(a)
        self.assertTrue(self.reserve(self.center, self.claim_calls()[0], self.now))
        self.center.set_runtime_setting('call_repeat_min_interval_seconds', '7200')
        b, _ = self.episode()
        self.call(b)
        self.assertFalse(self.reserve(self.center, self.claim_calls()[0], self.now+3600))

    def test_recovery_is_deliverable_after_resolve_once_and_without_calls(self):
        created, event = self.episode()
        self.resolve(event)
        self.resolve(event)
        incident = created['incident_id']
        self.assertEqual(1, len(self.outcomes(incident)))
        delivered = []
        class Telegram:
            def send(self, payload):
                delivered.append(payload)
                return {'message_id': 42, 'chat_id': '-1001'}
        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        self.assertEqual('recovered', delivered[0]['health_outcome']['status'])
        self.assertEqual('resolved', delivered[0]['incident']['state'])
        self.assertEqual([], self.claim_calls())
        self.assertEqual('sent', self.outcomes(incident)[0]['status'])

    def test_failed_job_after_ack_has_terminal_outcome_and_copyable_session(self):
        created, _ = self.episode()
        incident = created['incident_id']
        self.center.record_health_agent_session(incident, 'session-test', '', 'codex', 'session-test-id', 'https://agent.bezrabotnyi.com', actor='worker', stage='health-diagnosis', model='gpt-5.6-sol')
        self.center.acknowledge(incident, 'user')
        for _ in range(2):
            self.center.record_agent_job_result(incident, 'job-delivery', 'health-diagnosis', {'status': 'failed'})
        self.assertEqual(1, len(self.outcomes(incident)))
        claim = next(d for d in self.center.claim_due_deliveries(now_epoch=self.now+10, channel_group='message') if d['id']==self.outcomes(incident)[0]['id'])
        payload = self.center.delivery_payload(claim)
        self.assertEqual('failed', payload['health_outcome']['status'])
        self.assertIn('codex%3Asession-test-id', payload['health_outcome']['session_url'])
        text, markup = self.render(payload)
        self.assertIn('Работа остановилась', text)
        self.assertIn(payload['health_outcome']['session_url'], text)
        self.assertEqual(payload['health_outcome']['session_url'], markup['inline_keyboard'][0][0]['url'])

    def test_recovery_cancels_an_unsent_outdated_failure_conclusion(self):
        created, event = self.episode()
        self.center.record_agent_job_result(created['incident_id'], 'job', 'health-diagnosis', {'status': 'failed'})
        self.resolve(event)
        outcomes = self.outcomes(created['incident_id'])
        self.assertEqual(['cancelled', 'queued'], [d['status'] for d in outcomes])
        claims = self.center.claim_due_deliveries(now_epoch=self.now+10, channel_group='message')
        self.assertEqual(['recovered'], [self.center.delivery_payload(d)['health_outcome']['status'] for d in claims])

    def test_late_failed_result_does_not_override_recovery(self):
        created, event = self.episode()
        self.resolve(event)
        self.center.record_agent_job_result(created['incident_id'], 'job', 'health-diagnosis', {'status': 'failed'})
        self.assertEqual(['recovered'], [json.loads(d['target_json'])['health_outcome']['status'] for d in self.outcomes(created['incident_id'])])

    def test_plans_ready_has_real_choices_and_does_not_claim_fixed(self):
        created, _ = self.episode()
        incident = created['incident_id']
        workflow = HealthWorkflow(self.center, callback_secret='x'*32)
        workflow.attach_plans(incident, 'plans', [
            {'plan_id': p, 'title': p, 'summary': 'Проверить источник', 'step': p} for p in ('observe','repair','verify')], actor='worker')
        self.center.record_agent_job_result(incident, 'job', 'health-diagnosis', {'status':'completed'})
        delivery = self.outcomes(incident)[0]
        payload = self.center.delivery_payload(delivery)
        text, markup = self.render(payload)
        self.assertIn('Исправление ещё не выполнено', text)
        self.assertEqual(3, len(payload['health_plans']))
        self.assertTrue(any('callback_data' in button for row in markup['inline_keyboard'] for button in row))
        self.assertEqual('open', self.center.get_incident(incident)['state'])

    def test_session_cards_are_not_duplicate_alarm_cards_for_plan_migration(self):
        created, _ = self.episode()
        incident = created['incident_id']
        self.center.complete_delivery(created['initial_delivery_id'], 'sent', result={'message_id': 901, 'chat_id': '-1001'})
        for index, stage in enumerate(('health-diagnosis', 'health-orchestrator'), 902):
            self.center.record_health_agent_session(incident, f'session:{index}', '', 'codex', f'session-{index}', 'https://agent.bezrabotnyi.com', actor='worker', stage=stage)
            row = self.center._connection.execute("SELECT id FROM deliveries WHERE incident_id=? AND json_type(target_json,'$.health_session')='object' ORDER BY rowid DESC LIMIT 1", (incident,)).fetchone()
            self.center.complete_delivery(row['id'], 'sent', result={'message_id': index, 'chat_id': '-1001'})
        self.center.schedule_health_outcome(incident, 'interrupted', 'earlier-check')
        plans = [{'plan_id':p,'title':p,'summary':'Проверить источник','step':p} for p in ('observe','repair','verify')]
        HealthWorkflow(self.center, callback_secret='x'*32).attach_plans(incident, 'plans-after-links', plans, actor='worker')
        edits = list(self.center._connection.execute("SELECT * FROM deliveries WHERE incident_id=? AND channel='telegram.edit'", (incident,)))
        self.assertEqual(1, len(edits))
        self.assertEqual('queued', self.outcomes(incident)[0]['status'])
        self.center.record_health_delivery_migrated(created['initial_delivery_id'], {'message_id':901,'chat_id':'-1001','health_button_count':3,'health_signed_callback_count':3,'health_plan_ids':['observe','repair','verify']})
        self.center.complete_delivery(edits[0]['id'], 'sent', result={'message_id':901,'chat_id':'-1001','health_button_count':3,'health_signed_callback_count':3,'health_plan_ids':['observe','repair','verify']})
        self.assertEqual(0, self.center.health()['reconciliation_required'])

    def test_transport_timeout_reports_unknown_once_never_fixed(self):
        created, _ = self.episode()
        with self.center._lock, self.center._connection:
            delivery_id = self.center._schedule_delivery(created['incident_id'], 'gptadmin.agent:health-diagnosis', 'test-job', 0)
        class Agent:
            def send(self, payload, key):
                raise RuntimeError('timeout')
        worker = DeliveryWorker(self.center, object(), agent_jobs={'health-diagnosis':Agent()})
        for now in (self.now+1, self.now+100):
            worker.deliver(self.center.claim_due_deliveries(now_epoch=now, channel_group='agent')[0])
        outcome = self.outcomes(created['incident_id'])
        self.assertEqual(1, len(outcome))
        self.assertEqual('interrupted', json.loads(outcome[0]['target_json'])['health_outcome']['status'])
        self.assertEqual('open', self.center.get_incident(created['incident_id'])['state'])

    @staticmethod
    def render(payload):
        sent = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok":true,"result":{"message_id":42,"chat":{"id":-1001}}}'
        def open_request(request, **kwargs):
            sent.append(urllib.parse.parse_qs(request.data.decode()))
            return Response()
        sender = TelegramSender('fake', '-1001', action_codec=TelegramActionCodec('x'*32), severity_routes={'health':{'chat_id':'-1001','message_thread_id':1,'enabled':True}})
        with patch('notification_center.http_api.urllib.request.urlopen', side_effect=open_request):
            sender.send(payload)
        return sent[0]['text'][0], json.loads(sent[0].get('reply_markup',['{}'])[0])
