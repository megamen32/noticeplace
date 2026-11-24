"""One shared target owns one repair identity, and one green is not a recovery.

Purpose: reproduce the omniroute-guard defect where three observers of one
shared gateway produced three repair sessions, and where a single transient
green closed the card so the next degradation opened a new one.

Expected 3 seconds; maximum 15 seconds. No network, no delivery worker.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from notification_center import incident_lifecycle
from notification_center.core import NotificationCenter


class SharedRepairIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name) / 'state.db', {
            'source': {'project': 'omniroute-guard', 'max_severity': 'emergency',
                       'principal_id': 'watchdog',
                       'verification_sources': ['host:roomhacker-server-100']},
        })
        self.addCleanup(self.center._connection.close)
        # Two confirmations are required; the interval between them is the
        # operator's bound and is not what this test exercises.
        self.center.set_runtime_setting('health_recovery_min_interval_seconds', '0')

    def _event(self, host, service=None, correlation=None):
        event = {
            'schema': 'notify.event.v1', 'project': 'omniroute-guard', 'recipient': 'health',
            'kind': 'incident', 'severity': 'emergency',
            'title': 'Общий маршрут недоступен',
            'body': 'Шлюз не отвечает',
            'event_type': 'health.degraded',
            'dedup_key': 'omniroute-unavailable',
            'signal_type': 'omniroute-unavailable',
            'problem_type': 'omniroute-unavailable',
            'source_id': 'host:' + host,
            'host_id': host,
            'correlation_id': correlation or ('omniroute-host:' + host + ':20261124T0900Z'),
            'observed_at': 100,
        }
        if service:
            event['service'] = service
        return event

    def _lifecycle(self, incident_id):
        return self.center.get_incident(incident_id)['lifecycle']

    def _due_now(self, incident_id):
        """Make the re-armed request due: the next confirmation is one cycle later."""
        self.center._connection.execute(
            'UPDATE incident_lifecycle SET verification_due=? WHERE incident_id=?',
            (time.time() - 1, incident_id))
        self.center._connection.commit()

    def test_one_declared_target_shares_the_repair_identity(self):
        first = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100', 'omniroute'))
        second = self.center.create_event('source', 'node-b', self._event('roomhacker-server-88', 'omniroute'))

        first_group = self._lifecycle(first['incident_id'])['group']
        second_group = self._lifecycle(second['incident_id'])['group']

        self.assertEqual('omniroute', first_group['service'])
        self.assertEqual('omniroute', second_group['service'],
                         'the observers of one target share the repair identity')
        # The exact source ownership stays per host and per incident.
        self.assertEqual('host:roomhacker-server-100', first_group['source_id'])
        self.assertEqual('host:roomhacker-server-88', second_group['source_id'])
        self.assertNotEqual(first_group['host_id'], second_group['host_id'])
        self.assertNotEqual(self._lifecycle(first['incident_id'])['group_key'],
                            self._lifecycle(second['incident_id'])['group_key'],
                            'the incident group stays per host')

    def test_a_node_without_a_declared_target_stays_anonymous(self):
        incident = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100'))
        self.assertEqual('', self._lifecycle(incident['incident_id'])['group']['service'])

    def test_the_executor_facts_carry_the_shared_target(self):
        from notification_center.agent_job_helper import _repair_first_message
        incident = {
            'id': 'inc_fixture', 'project': 'omniroute-guard', 'severity': 'emergency',
            'title': 'OmniRoute', 'body': 'body', 'dedup_key': 'omniroute-unavailable',
            'consumer_id': 'omniroute-guard+host:roomhacker-server-100',
            'lifecycle': {'group': {'consumer_id': 'omniroute-guard+host:roomhacker-server-100',
                                    'problem_type': 'omniroute-unavailable',
                                    'service': 'omniroute',
                                    'source_id': 'host:roomhacker-server-100',
                                    'host_id': 'roomhacker-server-100'}},
        }
        message = _repair_first_message({'instruction': 'repair'}, {'health': {
            'source_id': 'host:roomhacker-server-100', 'host_id': 'roomhacker-server-100'}}, incident)
        facts = json.loads(message.strip().splitlines()[-1])
        self.assertEqual('omniroute', facts['service'])
        self.assertEqual('host:roomhacker-server-100', facts['source_id'],
                         'the per-source evidence stays exact')

    def test_one_green_does_not_close_the_episode(self):
        incident = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100', 'omniroute'))['incident_id']
        request = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]

        self.center.record_source_check('source', incident, 'check-one', dict(
            request_id=request['request_id'], generation=request['generation'],
            observed_state='healthy', evidence_refs=['probe:one']))

        self.assertEqual('open', self.center.get_incident(incident)['state'],
                         'one green is a sample, not a recovery')
        lifecycle = self._lifecycle(incident)
        self.assertEqual('recovering', lifecycle['verification_state'])
        self.assertEqual(1, lifecycle['healthy_streak'])
        self.assertNotEqual(request['request_id'], lifecycle['request_id'],
                            'the source is asked to confirm again')

    def test_two_confirmations_close_the_episode(self):
        incident = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100', 'omniroute'))['incident_id']
        request = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]
        self.center.record_source_check('source', incident, 'check-one', dict(
            request_id=request['request_id'], generation=request['generation'],
            observed_state='healthy', evidence_refs=['probe:one']))
        self._due_now(incident)
        second = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]

        self.center.record_source_check('source', incident, 'check-two', dict(
            request_id=second['request_id'], generation=second['generation'],
            observed_state='healthy', evidence_refs=['probe:two']))

        self.assertEqual('resolved', self.center.get_incident(incident)['state'])
        self.assertEqual(2, self._lifecycle(incident)['healthy_streak'])

    def test_an_episode_without_a_shared_target_keeps_the_ordinary_contract(self):
        incident = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100'))['incident_id']
        request = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]
        self.center.record_source_check('source', incident, 'check-one', dict(
            request_id=request['request_id'], generation=request['generation'],
            observed_state='healthy', evidence_refs=['probe:one']))
        self.assertEqual('resolved', self.center.get_incident(incident)['state'],
                         'an episode with a single owner keeps closing on one confirmation')

    def test_a_degraded_observation_cancels_the_streak(self):
        incident = self.center.create_event('source', 'node-a', self._event('roomhacker-server-100', 'omniroute'))['incident_id']
        request = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]
        self.center.record_source_check('source', incident, 'check-one', dict(
            request_id=request['request_id'], generation=request['generation'],
            observed_state='healthy', evidence_refs=['probe:one']))
        self._due_now(incident)
        second = incident_lifecycle.due_checks(self.center, 'source', 'omniroute-guard', 'host:roomhacker-server-100')[0]
        self.center.record_source_check('source', incident, 'check-degraded', dict(
            request_id=second['request_id'], generation=second['generation'],
            observed_state='degraded', evidence_refs=['probe:two']))

        self.assertEqual('open', self.center.get_incident(incident)['state'])
        self.assertEqual(0, self._lifecycle(incident)['healthy_streak'])


if __name__ == '__main__':
    unittest.main()
