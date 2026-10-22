"""Family collapse must follow the producer's reason key, not the node id.

Purpose: reproduce the omniroute-guard collapse defect where two nodes that
report the same reason (identical dedup_key) were split into separate
telegram cards because the family fell back to source_id.

Expected 2 seconds; maximum 10 seconds. No network, no delivery worker.
"""
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter


class IncidentFamilyDedupKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(
            Path(self.temp.name) / 'notice.sqlite3',
            {'p': {'project': 'omniroute-guard', 'max_severity': 'emergency'}},
            default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)

    def _event(self, host):
        return {
            'schema': 'notify.event.v1', 'project': 'omniroute-guard', 'recipient': 'health',
            'kind': 'incident', 'severity': 'emergency',
            'title': 'OmniRoute недоступен',
            'body': 'Общий маршрут перестал отвечать',
            'event_type': 'health.degraded',
            'signal_type': 'omniroute-unavailable',
            # Producer dedup_key IS the reason key: omniroute-unavailable:<reason>
            'dedup_key': 'omniroute-unavailable:pressure',
            'source_id': 'host:' + host,
            'host_id': host,
            'correlation_id': 'omniroute-' + host + '-episode-1',
        }

    def test_same_reason_on_two_nodes_shares_one_card(self):
        first = self.center.create_event('p', 'node-a', self._event('roomhacker-server-88'))
        second = self.center.create_event('p', 'node-b', self._event('roomhacker-server-44'))

        first_incident = self.center.get_incident(first['incident_id'])
        second_incident = self.center.get_incident(second['incident_id'])

        self.assertIsNotNone(first_incident.get('collapse_key'),
                            'the first incident must be bound to a telegram family')
        self.assertEqual(
            first_incident['collapse_key'], second_incident['collapse_key'],
            'two nodes reporting the same reason must share one telegram family')
        self.assertEqual(
            second_incident.get('telegram_card_owner_id'), first_incident['id'],
            'the second incident must reuse the card owned by the first')

    def test_different_reasons_on_two_nodes_stay_separate(self):
        event_a = self._event('roomhacker-server-88')
        event_b = self._event('roomhacker-server-44')
        event_b['dedup_key'] = 'omniroute-unavailable:unreachable'

        first = self.center.create_event('p', 'node-a', event_a)
        second = self.center.create_event('p', 'node-b', event_b)

        first_incident = self.center.get_incident(first['incident_id'])
        second_incident = self.center.get_incident(second['incident_id'])

        self.assertNotEqual(
            first_incident['collapse_key'], second_incident['collapse_key'],
            'different reasons must not collapse into one card')


if __name__ == '__main__':
    unittest.main()
