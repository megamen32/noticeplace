"""Quiet health work retains decisions; only emergency or a real question interrupts."""
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center import agent_job_helper
from notification_center.agent_job_helper import _extract_health_diagnosis
from notification_center.gptadmin_agent import _agent_job_event
from notification_center.gptadmin_agent import GptAdminAgentJobAdapter
from notification_center.http_api import DeliveryWorker, send_human_request_telegram
from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT


class HealthNotificationDecisionTests(unittest.TestCase):
    def setUp(self):
        # Card flows need a scheduled delivery; «Логи» ships disabled.
        _modes = unittest.mock.patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify","log"]'})
        _modes.start()
        self.addCleanup(_modes.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name) / 'db', {'token': {'project': 'fleet',
            'max_severity': 'emergency', 'agent_jobs': ['health-diagnosis']}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        # Exercise the legacy review audit as well as the unconditional quiet gate.
        self.center.set_runtime_setting('telegram_all_notifications_enabled','0')

    def create(self, severity='important', **changes):
        self.event = {'schema': 'notify.event.v1', 'project': 'fleet', 'recipient': 'me', 'kind': 'incident',
            'severity': severity, 'title': 'Кратковременно выросла нагрузка', 'body': 'Сервис отвечает.',
            'dedup_key': severity, 'event_type': 'health.degraded', 'source_id': 'fleet:100',
            'host_id': '100', 'signal_type': 'load', 'correlation_id': 'episode:' + severity}
        self.event.update(changes)
        return self.center.create_event('token', severity + str(changes), self.event)

    def result(self, created, notify):
        self.center.record_agent_job_result(created['incident_id'], created['agent_job_delivery_id'],
            'health-diagnosis', {'status': 'completed', 'agent_receipt': {'session_id': 'native', 'harness': 'codex',
                'notify_user': notify, 'notification_reason': 'Сервис работает; действия человека не нужны.'}})

    def queued_messages(self, incident):
        return self.center._connection.execute("SELECT * FROM deliveries WHERE incident_id=? AND channel LIKE 'telegram.%' AND status='queued'", (incident,)).fetchall()

    def test_quiet_decision_preserves_incident_session_and_audit_without_cards(self):
        created = self.create()
        self.assertIsNone(created['initial_delivery_id'])
        self.assertIsNotNone(created['agent_job_delivery_id'])
        self.result(created, False)
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])
        self.assertEqual(False, self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.center.resolve_event('token', 'recovery', {**self.event, 'action': 'resolve', 'event_type': 'health.recovered'})
        self.assertEqual([], self.queued_messages(created['incident_id']))

    def pending_question(self, created):
        return self.center.create_human_request('token', {
            'schema': 'ask_human.request.v1', 'request_id': 'fixture-question',
            'project': 'fleet', 'recipient': 'me', 'mode': 'question',
            'message': 'Для восстановления нужен ваш выбор.',
            'allowed_actors': ['telegram:42'], 'incident_id': created['incident_id'],
        })

    def test_positive_decision_without_question_stays_internal(self):
        created = self.create()
        self.result(created, True)
        self.result(created, True)
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.assertTrue(self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])
        self.assertEqual('open', self.center.get_incident(created['incident_id'])['state'])

    def test_positive_decision_with_pending_question_queues_one_original_card_once(self):
        created = self.create()
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.pending_question(created)
        self.result(created, True)
        self.result(created, True)
        self.assertEqual(1, len(self.queued_messages(created['incident_id'])))
        payload = self.center.delivery_payload(dict(self.queued_messages(created['incident_id'])[0]))
        self.assertTrue(payload['notification_decision']['notify_user'])
        self.assertEqual('native', payload['ai_session']['session_id'])

    def test_accepted_executor_observer_is_not_a_failed_notification_decision(self):
        created = self.create()
        outcome = self.center.record_agent_job_result(created['incident_id'], created['agent_job_delivery_id'],
            'health-diagnosis', {'status':'completed','agent_receipt':{
                'session_id':'native','harness':'opencode','model':'minimax-coding-plan/MiniMax-M3.1-Flash',
                'path':'repair-first','observer_only':True,'awaiting_executor':True}})
        self.assertIsNone(self.center.health_notification_decision(created['incident_id']))
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.assertTrue(outcome['awaiting_executor'])

    def test_failed_observer_keeps_decision_without_unsolicited_card(self):
        created = self.create()
        self.center.record_agent_job_result(created['incident_id'], created['agent_job_delivery_id'],
            'health-diagnosis', {'status':'failed','agent_receipt':{
                'session_id':'native','harness':'opencode','path':'repair-first','observer_only':True,'awaiting_executor':True}})
        self.assertTrue(self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])
        self.assertEqual('open', self.center.get_incident(created['incident_id'])['state'])

    def test_progress_refresh_cannot_create_the_first_quiet_card(self):
        created = self.create()
        self.center.record_health_agent_session(created['incident_id'],'session','', 'opencode', 'native',
            'https://agent.bezrabotnyi.com',stage='health-diagnosis')
        self.assertIsNone(self.center.queue_incident_card_refresh(created['incident_id']))
        self.assertEqual([],self.queued_messages(created['incident_id']))
        self.result(created, True)
        self.assertIsNone(self.center.queue_incident_card_refresh(created['incident_id']))
        self.assertEqual([], self.queued_messages(created['incident_id']))

    def test_operator_question_is_not_suppressed_as_pending_observer(self):
        created = self.create()
        receipt = {'status':'completed','agent_receipt':{'session_id':'native','harness':'opencode',
            'path':'repair-first','observer_only':True,'awaiting_human':True,'question_ref':'fixture-question'}}
        self.center.record_agent_job_result(created['incident_id'], created['agent_job_delivery_id'],
            'health-diagnosis', receipt)
        # An unbound question_ref is not evidence of an actionable pending question.
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.pending_question(created)
        outcome = self.center.record_agent_job_result(created['incident_id'], created['agent_job_delivery_id'],
            'health-diagnosis', receipt)
        self.assertTrue(outcome['awaiting_human'])
        self.assertTrue(self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.assertEqual(1, len(self.queued_messages(created['incident_id'])))
        self.assertEqual('fixture-question', self.center.incident_human_question(created['incident_id'])['request_id'])
        self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])

    def test_pending_incident_question_creates_card_before_executor_receipt(self):
        created = self.create()
        request = self.center.create_human_request('token', {
            'schema': 'ask_human.request.v1', 'request_id': 'operator-decision',
            'project': 'fleet', 'recipient': 'me', 'mode': 'question',
            'message': 'Для восстановления нужен ваш выбор.',
            'allowed_actors': ['telegram:42'], 'incident_id': created['incident_id'],
        })
        self.assertIsNone(self.center.health_notification_decision(created['incident_id']))
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_CHAT_ID': '42'}):
            result = send_human_request_telegram(self.center, 'token', request)
        self.assertEqual('queued', result['status'])
        self.assertEqual(1, len(self.queued_messages(created['incident_id'])))
        self.assertEqual('pending', self.center.incident_human_question(created['incident_id'])['state'])

    def test_answered_incident_question_does_not_release_quiet_progress(self):
        created = self.create()
        self.center.create_human_request('token', {
            'schema': 'ask_human.request.v1', 'request_id': 'answered-decision',
            'project': 'fleet', 'recipient': 'me', 'mode': 'question',
            'message': 'Нужен ваш выбор.', 'allowed_actors': ['telegram:42'],
            'incident_id': created['incident_id'],
        })
        self.center.resolve_human_request('token', 'answered-decision', 'telegram:42', 'Продолжить')
        self.assertIsNone(self.center.queue_incident_card_refresh(created['incident_id']))
        self.assertEqual([], self.queued_messages(created['incident_id']))

    def test_phase_closure_of_quiet_warning_does_not_create_recovery_card(self):
        created = self.create()
        self.result(created, False)
        self.center.resolve_event('token', 'critical-transition', {**self.event, 'action': 'resolve',
            'event_type': 'health.phase_changed', 'next_severity': 'critical'})
        self.assertEqual([], self.queued_messages(created['incident_id']))
        critical = self.create('critical')
        self.assertIsNone(critical['initial_delivery_id'])
        self.assertIsNotNone(critical['agent_job_delivery_id'])
        self.assertEqual('open', self.center.get_incident(critical['incident_id'])['state'])

    def test_real_hub_stdout_envelope_keeps_quiet_decision_through_worker(self):
        created = self.create()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args):
                return json.dumps({'job_id': 'durable-hub-job', 'status': 'completed', 'result': {
                    'response': {'stdout': json.dumps({'status': 'completed', 'harness': 'codex', 'session_id': 'native',
                        'notify_user': False, 'notification_reason': 'Сервис исправен, действий не требуется.'})}}}).encode()
        adapter = GptAdminAgentJobAdapter('health-diagnosis', 'http://127.0.0.1:9999/webhooks/v1/health', 'test-key',
            runner=lambda *_a, **_kw: Response())
        worker = DeliveryWorker(self.center, object(), agent_jobs={'health-diagnosis': adapter})
        delivery = self.center.claim_due_deliveries(channel_group='agent')[0]
        worker.deliver(delivery)
        self.assertFalse(self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.assertEqual([], self.queued_messages(created['incident_id']))

    def test_terminal_parser_preserves_only_real_booleans_and_redacts_reason(self):
        for value in (False, True, 'false', 0, None):
            raw = {'result': {'stdout': json.dumps({'session_id': 'native', 'harness': 'codex',
                'notify_user': value, 'notification_reason': 'secret=hidden ' + 'я' * 600})}}
            receipt = GptAdminAgentJobAdapter._bounded_agent_receipt(raw)
            if type(value) is bool:
                self.assertIs(value, receipt['notify_user'])
                self.assertNotIn('hidden', receipt['notification_reason'])
                self.assertLessEqual(len(receipt['notification_reason']), 500)
            else:
                self.assertNotIn('notify_user', receipt)

    def test_only_emergency_bypasses_quiet_ai_decision(self):
        for severity in ('critical', 'emergency'):
            with self.subTest(severity=severity):
                created = self.create(severity)
                self.assertIsNotNone(created['agent_job_delivery_id'])
                if severity == 'emergency':
                    self.assertIsNotNone(created['initial_delivery_id'])
                else:
                    self.assertIsNone(created['initial_delivery_id'])
                self.result(created, False)
                messages = self.queued_messages(created['incident_id'])
                if severity == 'emergency':
                    self.assertIn(created['initial_delivery_id'], [row['id'] for row in messages])
                else:
                    self.assertEqual([], messages)
                self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])

    def test_manual_ai_request_preserves_executor_without_unsolicited_card(self):
        created = self.create()
        self.assertIsNone(self.center.original_telegram_card(created['incident_id']))
        action = self.center.apply_telegram_action(created['incident_id'], 'ai', 'operator')
        self.assertEqual(created['agent_job_delivery_id'], action['agent_job_delivery_id'])
        self.assertTrue(self.center.manual_ai_requested(created['incident_id']))
        self.result(created, False)
        self.assertEqual([], self.queued_messages(created['incident_id']))
        self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])

    def test_missing_or_nonboolean_decision_retains_audit_without_card(self):
        for value in (None, 'false'):
            with self.subTest(value=value):
                created = self.create(dedup_key='invalid-' + str(value))
                self.result(created, value)
                self.assertTrue(self.center.health_notification_decision(created['incident_id'])['notify_user'])
                self.assertEqual([], self.queued_messages(created['incident_id']))
                self.assertEqual('open', self.center.get_incident(created['incident_id'])['state'])
                self.assertEqual('native', self.center.latest_agent_session(created['incident_id'])['session_id'])

    def test_failed_diagnosis_retains_fallback_decision_without_card(self):
        created = self.create()
        self.assertIsNone(self.center.schedule_health_outcome(created['incident_id'], 'interrupted', 'timeout'))
        self.assertIsNone(self.center.schedule_health_outcome(created['incident_id'], 'interrupted', 'timeout-again'))
        self.assertTrue(self.center.health_notification_decision(created['incident_id'])['notify_user'])
        self.assertEqual('open', self.center.get_incident(created['incident_id'])['state'])
        self.assertEqual([], self.queued_messages(created['incident_id']))

    def test_quiet_native_receipt_precedes_decision_without_a_planner_launch(self):
        created = self.create()
        callbacks, requests = [], []
        def callback(receipt):
            callbacks.append(receipt)
            self.center.record_health_agent_session(created['incident_id'], 'accepted', '', receipt['harness'],
                receipt['session_id'], 'https://agent.bezrabotnyi.com', stage='health-diagnosis')
            self.assertEqual([], self.queued_messages(created['incident_id']))
        class Response:
            status = 200
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args): return json.dumps(self.payload).encode()
        def runner(request, **_kwargs):
            url = str(getattr(request, 'full_url', ''))
            if url.endswith('/api/automation/launch-policy'):
                return Response({'version': 1, 'incidentExecution': dict(agent_job_helper.HEALTH_EXECUTION_PROFILE),
                                 'allowedHarnesses': ['opencode'], 'preferredHarness': 'opencode',
                                 'models': {'opencode': 'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            if '/api/models?' in url:
                return Response({'models': ['minimax-coding-plan/MiniMax-M3.1-Flash'], 'stale': False})
            if '/api/autopilot/sessions/' in url:
                return Response({'enabled': True, 'sessionId': 'real-native-id'})
            if '/api/settings/automation/disarm' in url:
                # Since the fail-closed owner gate, the launch also confirms
                # the fleet-wide Herder lever is open and every required node
                # is uniquely confirmed before admitting.
                return Response({'ok': True,
                                 'state': {'disarmed': False,
                                           'records': [{'scope': '*', 'forbidden': False}]},
                                 'nodes': [{'hostId': host, 'status': 'confirmed'}
                                           for host in ('server-100', 'server-44', 'server-88')]})
            if '/api/automation/project-launch' in url:
                requests.append(request)
                return Response({'outcome': 'accepted', 'operation_id': 'op-quiet',
                                 'selected_harness': 'opencode',
                                 'model': 'minimax-coding-plan/MiniMax-M3.1-Flash',
                                 'mode': 'queue', 'session_id': 'real-native-id',
                                 'routing_reason': 'declared route', 'attempts': []})
            requests.append(request)
            return Response({'ok': True, 'sessionId': 'real-native-id', 'created': True})
        profile = {'url': 'http://127.0.0.1:18787/api/sessions/new-or-resume', 'name': 'diagnosis',
            'harness': 'opencode', 'cwd': self.tmp.name, 'instruction': 'Read only.', 'mode': 'queue',
            # Since 772aa87 the launch fails closed without its dedicated bearer.
            'health_launch_token': 'test-health-launch-token',
            'orchestrator_name': 'planner', 'orchestrator_model': 'configured', 'orchestrator_requested_model': 'configured'}
        details = {'messages': [{'role': 'assistant', 'text': json.dumps({'status': 'diagnosis_complete',
            'diagnosis': 'Сервис работает.', 'notify_user': False, 'notification_reason': 'Действий человека не требуется.'})}]}
        def session_json(_profile, native_id, suffix, _runner):
            self.assertEqual('real-native-id', native_id)
            return details if suffix.startswith('/details') else {'session': {'status': 'idle'}, 'fingerprint': 'native-progress'}
        event = {'schema': 'notify.agent-job.v1', 'job_id': 'health-diagnosis',
            'incident': self.center.get_incident(created['incident_id']),
            'health': {'incident_model': PROJECT_ROUTE_INTENT, 'notification_review_required': True}}
        with patch.object(agent_job_helper, '_load_health_callback', return_value=('unused', 'unused')), \
             patch.object(agent_job_helper, '_launch_policy_profile', side_effect=lambda p, *_a, **_kw: p), \
             patch.object(agent_job_helper, '_session_json', side_effect=session_json):
            receipt = agent_job_helper.run_profile('health-diagnosis', event, Path('unused'),
                profile_override=profile, runner=runner, session_callback=callback)
        launches = [r for r in requests if 'project-launch' in str(r.full_url)]
        self.assertEqual(1, len(launches), 'one admission call, no planner session')
        self.assertEqual(1, len(callbacks))
        self.assertFalse(receipt['notify_user'])
        self.assertEqual('real-native-id', receipt['session_id'])
        self.assertNotIn('orchestrator_session_id', receipt)
        body = json.loads([r for r in requests if "project-launch" in str(r.full_url)][0].data)
        self.assertIn('notify_user', body['message'])
        self.assertNotIn('humanRequested', body)
        # The incident project drives admission; no ancestor is consumed here.
        self.assertEqual(created['incident_id'] and body['project'], body['project'])
        self.assertNotIn('sourceSessions', body)

    def test_optional_judgement_requires_boolean(self):
        def parse(value):
            return _extract_health_diagnosis({'messages': [{'role': 'assistant', 'text': json.dumps(
                {'diagnosis': 'Работа завершена.', 'notify_user': value})}]})
        self.assertIsNone(parse('false'))
        self.assertIsNone(parse(0))
        self.assertFalse(parse(False)['notify_user'])

    def test_review_flag_is_trusted_and_strict_in_helper_payload(self):
        created = self.create()
        delivery = self.center._connection.execute('SELECT * FROM deliveries WHERE id=?',
            (created['agent_job_delivery_id'],)).fetchone()
        payload = self.center.delivery_payload(dict(delivery))
        self.assertIs(True, _agent_job_event('health-diagnosis', payload)['health']['notification_review_required'])
        for value in ('true', 1, None):
            payload['health_context']['notification_review_required'] = value
            self.assertNotIn('notification_review_required', _agent_job_event('health-diagnosis', payload)['health'])

    def test_untyped_explicit_diagnosis_is_direct_but_stays_quiet(self):
        created = self.create(event_type='custom.event', agent_job='health-diagnosis')
        self.assertIsNone(created['initial_delivery_id'])
        self.assertIsNotNone(created['agent_job_delivery_id'])
        self.assertFalse(self.center.health_notification_review_required(created['incident_id']))

    def test_other_health_types_or_incomplete_identity_stay_quiet_without_auto_diagnosis(self):
        for change in ({'event_type': 'health.other'}, {'source_id': ''}):
            created = self.create(**change)
            self.assertIsNone(created['initial_delivery_id'])
            self.assertIsNone(created['agent_job_delivery_id'])
