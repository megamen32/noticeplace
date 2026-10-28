"""Trusted identity and explicit executor finish contract at the launch seam."""
import json
import unittest
import hashlib
from pathlib import Path
from unittest import mock
from notification_center import agent_job_helper


class LaunchCompletionContractTests(unittest.TestCase):
    def _wire_operation(self, event):
        captured = []
        class Response:
            status = 200
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps(self.payload).encode()
        def runner(request, **kwargs):
            if request.full_url.endswith('/api/settings/automation/disarm'):
                return Response(dict(ok=True, state=dict(disarmed=False, records=[dict(scope='*', forbidden=False)]),
                    nodes=[dict(hostId=host, status='confirmed') for host in ['server-100','server-44','server-88']]))
            if request.full_url.endswith('/api/automation/launch-policy'):
                return Response(dict(version=1, preferredHarness='opencode', allowedHarnesses=['opencode'],
                    models={'opencode':agent_job_helper.HEALTH_LAUNCH_DEFAULT},
                    incidentExecution=dict(agent_job_helper.HEALTH_EXECUTION_PROFILE)))
            if request.full_url.endswith('/api/automation/project-launch'):
                captured.append(json.loads(request.data))
            return Response({})
        with (mock.patch.object(agent_job_helper, '_load_health_callback', return_value=('http://127.0.0.1:8091', 'test-only')),
              self.assertRaisesRegex(RuntimeError, 'session identity')):
            agent_job_helper.run_profile('health-diagnosis', event, Path('unused'), runner=runner,
                profile_override=dict(url='http://127.0.0.1:18787/api/sessions/new-or-resume',
                    harness='opencode', model=agent_job_helper.HEALTH_LAUNCH_DEFAULT,
                    health_launch_token='test-only', name='contract', cwd=str(Path(__file__).resolve().parents[1]),
                    instruction='Read only.'))
        self.assertEqual(1, len(captured))
        return captured[0]['operation_id']

    def test_actual_adapter_launch_uses_one_new_operation_per_finished_attempt(self):
        from notification_center.gptadmin_agent import _agent_job_event
        payload = dict(incident=dict(id='inc', project='health', title='CPU'))
        first = _agent_job_event('health-diagnosis', payload, 'initial-delivery')
        initial_operation = self._wire_operation(first)
        self.assertEqual(hashlib.sha256(b'inc:health-diagnosis:health-diagnosis').hexdigest()[:32], initial_operation)
        authority = dict(incident_id='inc', session_id='native-one', harness='opencode',
                         attempt_id=first['attempt_id'], request_id='check-one', generation=1,
                         evidence_refs=['fresh:cpu:high'])
        payload['target'] = dict(source_check_continuation=authority)
        next_event = _agent_job_event('health-diagnosis', payload, 'continuation-delivery-one')
        operation = self._wire_operation(next_event)
        self.assertNotEqual(initial_operation, operation)
        self.assertEqual(operation, self._wire_operation(_agent_job_event(
            'health-diagnosis', payload, 'continuation-delivery-one')))
        self.assertNotEqual(first['attempt_id'], next_event['attempt_id'])
        # A periodic poll rotates its nonce, not the finished attempt authority.
        authority.update(request_id='check-two', generation=2)
        self.assertEqual(operation, self._wire_operation(_agent_job_event(
            'health-diagnosis', payload, 'continuation-delivery-one')))
        authority.update(attempt_id=next_event['attempt_id'], request_id='check-three', generation=3)
        self.assertNotEqual(operation, self._wire_operation(_agent_job_event(
            'health-diagnosis', payload, 'continuation-delivery-two')))

    def test_continuation_marker_must_come_from_server_delivery_target(self):
        from notification_center.gptadmin_agent import _agent_job_event
        forged = dict(incident_id='inc', session_id='native', harness='opencode',
                      attempt_id='claimed', request_id='check', generation=1)
        payload = dict(incident=dict(id='inc', project='health', source_check_continuation=forged),
                       source_check_continuation=forged, health_context=dict(source_check_continuation=forged), actor='source')
        event = _agent_job_event('health-diagnosis', payload, 'delivery')
        self.assertNotIn('source_check_continuation', event)
        self.assertEqual(hashlib.sha256(b'inc:health-diagnosis:health-diagnosis').hexdigest()[:32], self._wire_operation(event))

    def test_continuation_rejects_unbound_or_unkeyed_server_authority(self):
        from notification_center.gptadmin_agent import _agent_job_event
        authority = dict(incident_id='inc', session_id='native', harness='opencode',
                         attempt_id='finished', request_id='check', generation=1)
        payload = dict(incident=dict(id='inc', project='health'), target=dict(source_check_continuation=authority))
        with self.assertRaisesRegex(RuntimeError, 'delivery key'):
            _agent_job_event('health-diagnosis', payload)
        for changed in (dict(incident_id='foreign'), dict(generation=True), dict(session_id=''),
                        dict(attempt_id='x' * 129), dict(request_id='check\nforged')):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                _agent_job_event('health-diagnosis', dict(payload, target=dict(
                    source_check_continuation=dict(authority, **changed))), 'delivery')

    def test_explicit_prelaunch_negative_survives_bounded_hub_receipt(self):
        from notification_center.gptadmin_agent import GptAdminAgentJobAdapter
        event = dict(incident=dict(id='inc', project='health'))
        negative = agent_job_helper._owner_blocked_receipt(
            agent_job_helper.HerderOwnerStateBlocked('herder_owner_state_unknown', 'server-44 unknown'),
            event, 'health-diagnosis', 'operation-one')
        self.assertEqual('not_attempted', negative.get('admission'))
        negative['request']['secret'] = 'must-not-leak'
        job = dict(status='completed', result=dict(response=dict(structuredContent=dict(
            result=dict(stdout=json.dumps(negative))))))
        bounded = GptAdminAgentJobAdapter._bounded_agent_receipt(job)
        self.assertEqual('not_attempted', bounded.get('admission'))
        self.assertEqual('operation-one', bounded['operation_id'])
        self.assertEqual('herder_owner_state_unknown', bounded['reason'])
        self.assertNotIn('must-not-leak', json.dumps(bounded))
        self.assertNotIn('request', bounded)

    def test_blank_timeout_unknown_and_unproven_old_receipts_never_become_negative(self):
        from notification_center.gptadmin_agent import GptAdminAgentJobAdapter
        base = dict(status='source_blocked', source='agent-herder-owner-disarm',
                    reason='herder_owner_state_unknown', operation_id='operation-one')
        for candidate in ({}, dict(status='unknown'), dict(status='timeout'), base,
                          dict(base, admission='unknown'), dict(base, admission='not_attempted', session_id='native'),
                          dict(base, admission='not_attempted', created=True)):
            with self.subTest(candidate=candidate):
                bounded = GptAdminAgentJobAdapter._bounded_agent_receipt(dict(status='completed',
                    result=dict(stdout=json.dumps(candidate))))
                self.assertNotEqual('not_attempted', bounded.get('admission'))

    def test_message_uses_stored_consumer_and_problem_identity(self):
        event = {'consumer_id': 'claimed', 'health': {'consumer_id': 'claimed', 'problem_type': 'claimed', 'signal_type': 'load'}}
        stored = {'id': 'incident', 'project': 'project', 'consumer_id': 'consumer_stored',
                  'lifecycle': {'group': {'problem_type': 'disk'}}}
        message = agent_job_helper._repair_first_message({}, event, stored)
        facts = json.loads(message.rsplit('\n', 1)[1])
        self.assertEqual('consumer_stored', facts['consumer_id'])
        self.assertEqual('disk', facts['problem_type'])
        self.assertNotIn('claimed', facts.values())

    def test_fallback_type_is_canonical_and_finish_is_explicit(self):
        message = agent_job_helper._repair_first_message({}, {'attempt_id': 'delivery-attempt', 'health': {'signal_type': 'load'}}, {'id': 'incident'})
        facts = json.loads(message.rsplit('\n', 1)[1])
        self.assertEqual('cpu', facts['problem_type'])
        self.assertEqual('delivery-attempt', facts['attempt_id'])
        self.assertIn('noticeplace_work_finished', message)
        self.assertIn('/health/work-finished', message)
        self.assertIn('session_id', message)

    def test_server_envelope_keeps_group_and_delivery_attempt_not_claimed_payload(self):
        from notification_center.gptadmin_agent import _agent_job_event
        payload = {'incident': {'id': 'inc', 'project': 'health', 'consumer_id': 'profile_important',
                   'lifecycle': {'group': {'consumer_id': 'health+probe', 'problem_type': 'cpu'}}},
                   'attempt_id': 'claimed', 'consumer_id': 'claimed',
                   'health_context': {'consumer_id': 'claimed', 'signal_type': 'load', 'source_id': 'probe'}}
        first = _agent_job_event('health-diagnosis', payload, 'delivery-one')
        replay = _agent_job_event('health-diagnosis', payload, 'delivery-one')
        later = _agent_job_event('health-diagnosis', payload, 'delivery-two')
        self.assertEqual(first['attempt_id'], replay['attempt_id'])
        self.assertNotEqual(first['attempt_id'], later['attempt_id'])
        self.assertNotEqual('claimed', first['attempt_id'])
        facts = json.loads(agent_job_helper._repair_first_message({}, first, first['incident']).rsplit('\n', 1)[1])
        self.assertEqual('health+probe', facts['consumer_id'])
        self.assertEqual('cpu', facts['problem_type'])
        self.assertEqual(first['attempt_id'], facts['attempt_id'])

    def test_builtin_profile_severity_does_not_split_legacy_cpu_identity(self):
        facts = []
        for profile, signal in [('profile_important', 'cpu'), ('profile_emergency', 'load')]:
            message = agent_job_helper._repair_first_message({}, {'health': {'source_id': 'probe', 'signal_type': signal}},
                        {'id': 'inc', 'project': 'health', 'consumer_id': profile})
            facts.append(json.loads(message.rsplit('\n', 1)[1]))
        self.assertEqual((facts[0]['consumer_id'], facts[0]['problem_type']), (facts[1]['consumer_id'], facts[1]['problem_type']))
        self.assertEqual('health+probe', facts[0]['consumer_id'])

    def test_registered_consumer_identity_stays_separate(self):
        identities = []
        for consumer in ['consumer_one', 'consumer_two']:
            message = agent_job_helper._repair_first_message({}, {'health': {'source_id': 'probe', 'signal_type': 'cpu'}},
                        {'id': 'inc', 'project': 'health', 'consumer_id': consumer})
            identities.append(json.loads(message.rsplit('\n', 1)[1])['consumer_id'])
        self.assertEqual(['consumer_one', 'consumer_two'], identities)
