"""One shared service owns one operator call escalation, not one per host.

Purpose: reproduce the defect where several observers of the same shared
gateway each owned their own call budget, so one continuously open common
outage could raise a second, unrelated phone escalation just because another
node reported it.

Expected 3 seconds; maximum 10 seconds. No network, no device, no real call:
the call adapters are local fakes and every assertion reads the center's own
deliveries and budgets.
"""
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker
from notification_center.incident_call_chain import ORDER
from notification_center.incident_call_chain import start as start_call_chain

MISSED = {'answered': False, 'ended': True, 'safe_to_fallback': True, 'status': 'unanswered'}


class SharedServiceCallFamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.temp.name) / 'db',
            {'producer': {'project': 'omniroute-guard', 'max_severity': 'emergency'}},
            default_quiet_hours=[])
        self.calls = []

        class Message:
            def send(this, payload):
                return {'message_id': 17, 'chat_id': '42'}

        class Sender:
            def __init__(this, name):
                this.name = name

            def send(this, payload, key):
                this and self.calls.append(this.name)
                return dict(MISSED)

        class Phone:
            can_phone_call = True

            def phone_call(this, payload):
                self.calls.append('phone')
                return {'callId': 'fake-call'}

        self.worker = DeliveryWorker(self.center, Message(), matrix_call=Sender('matrix'),
                                     telegram_call=Sender('telegram'), android_phone=Phone(),
                                     incident_call_order=ORDER,
                                     android_phone_quiet_start_hour=0, android_phone_quiet_end_hour=0)

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, host, service=None, episode='episode-1'):
        event = {
            'schema': 'notify.event.v1', 'project': 'omniroute-guard', 'recipient': 'health',
            'kind': 'incident', 'severity': 'emergency', 'event_type': 'health.degraded',
            'title': 'Общий маршрут недоступен', 'body': 'Шлюз не отвечает',
            'dedup_key': 'omniroute-unavailable', 'signal_type': 'omniroute-unavailable',
            'source_id': 'host:' + host, 'host_id': host,
            'correlation_id': 'omniroute-host:' + host + ':' + episode,
            'observed_at': 100,
        }
        if service:
            event['service'] = service
        return event

    def _observe(self, host, service=None, key='one', episode='episode-1'):
        created = self.center.create_event('producer', key, self._event(host, service, episode))
        # The message group is where the consumer policy starts the call chain.
        for row in self.center.claim_due_deliveries(now_epoch=10 ** 12, channel_group='message'):
            self.worker.deliver(row)
        return created['incident_id']

    def _call_delivery(self, incident_id):
        """The one call delivery the incident owns, if it owns one."""
        rows = self.center._connection.execute(
            "SELECT d.*, i.project, i.recipient, i.dedup_key, i.collapse_key FROM deliveries d "
            "JOIN incidents i ON i.id=d.incident_id WHERE d.incident_id=? AND d.channel LIKE '%.call' "
            "ORDER BY d.created_at, d.rowid", (incident_id,)).fetchall()
        return rows[0] if rows else None

    def _problem_key(self, incident_id):
        row = self._call_delivery(incident_id)
        if row is None:
            start_call_chain(self.center, incident_id, 0)
            row = self._call_delivery(incident_id)
        return self.center._call_problem_key(dict(row))

    def _budget_keys(self):
        return [row[0] for row in self.center._connection.execute(
            "SELECT scope_key FROM call_problem_budgets")]

    def _deliver_calls(self):
        rows = self.center.claim_due_deliveries(now_epoch=10 ** 12, channel_group='call')
        for row in rows:
            self.worker.deliver(row)
        return rows

    def test_observers_of_one_shared_target_share_one_call_escalation(self):
        first = self._observe('roomhacker-server-100', 'omniroute', key='one')
        second = self._observe('roomhacker-server-88', 'omniroute', key='two')
        self.assertEqual(self._problem_key(first), self._problem_key(second),
                         'the phone queue belongs to the shared service, not to the host')

    def test_observers_without_a_shared_target_keep_their_own_queue(self):
        first = self._observe('roomhacker-server-100', None, key='one')
        second = self._observe('roomhacker-server-88', None, key='two')
        self.assertNotEqual(self._problem_key(first), self._problem_key(second),
                            'a node without a declared target keeps its own queue')

    def test_a_second_episode_of_the_same_service_does_not_open_a_second_queue(self):
        first = self._observe('roomhacker-server-100', 'omniroute', key='one')
        self._deliver_calls()
        self.assertEqual(1, self.calls.count('matrix'))

        second = self._observe('roomhacker-server-88', 'omniroute', key='two', episode='episode-2')
        start_call_chain(self.center, second, 0)
        self._deliver_calls()
        delivery = self._call_delivery(second)
        self.assertIsNotNone(delivery)
        self.assertEqual('cancelled', delivery['status'],
                         'the shared outage owns one escalation lifecycle')
        self.assertEqual(1, self.calls.count('matrix'),
                         'no second phone queue for the same common outage')
        self.assertEqual(1, len(self._budget_keys()))

    def test_the_repeat_of_a_shared_escalation_is_not_removed(self):
        self.center.set_runtime_setting('call_repeat_min_interval_seconds', '1')
        first = self._observe('roomhacker-server-100', 'omniroute', key='one')
        self._deliver_calls()
        self.assertEqual(1, self.calls.count('matrix'))

        # The interval has passed: a later episode of the same shared service
        # escalates again.  Nothing is muted while the outage stays open.
        self.center._connection.execute(
            "UPDATE deliveries SET updated_at=0,claimed_at=0,created_at=0 WHERE channel LIKE '%.call'")
        self.center._connection.execute("UPDATE call_problem_budgets SET last_attempt_at=0")
        self.center._connection.commit()

        second = self._observe('roomhacker-server-88', 'omniroute', key='two', episode='episode-2')
        start_call_chain(self.center, second, 0)
        self._deliver_calls()
        self.assertEqual(2, self.calls.count('matrix'),
                         'the repeat of an unfinished escalation stays admitted')
        self.assertEqual(1, len(self._budget_keys()))

    def test_the_per_source_records_and_evidence_survive_the_grouping(self):
        first = self._observe('roomhacker-server-100', 'omniroute', key='one')
        second = self._observe('roomhacker-server-88', 'omniroute', key='two')
        self.assertNotEqual(first, second)
        for incident_id, host in ((first, 'roomhacker-server-100'), (second, 'roomhacker-server-88')):
            incident = self.center.get_incident(incident_id)
            self.assertEqual('host:' + host, incident['lifecycle']['group']['source_id'])
            self.assertEqual(host, incident['lifecycle']['group']['host_id'])
            self.assertEqual('omniroute', incident['lifecycle']['group']['service'])
        rows = self.center._connection.execute(
            "SELECT incident_id, count(*) FROM events GROUP BY incident_id").fetchall()
        self.assertEqual(2, len(rows),
                         'grouping is for the escalation, not for the evidence')


if __name__ == '__main__':
    unittest.main()
