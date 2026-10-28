"""Trusted identity and explicit executor finish contract at the launch seam."""
import json
import unittest
from notification_center import agent_job_helper


class LaunchCompletionContractTests(unittest.TestCase):
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
