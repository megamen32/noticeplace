"""A dead verifier wakes one existing attempt; it can never prove recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from notification_center.core import NotificationCenter
from notification_center import incident_lifecycle as lifecycle


class DeadlineTickTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.center = NotificationCenter(Path(tmp.name)/'db', {
            'p': dict(project='qa', max_severity='notice', verification_sources=['probe'], principal_id='source'),
            'a': dict(project='qa', max_severity='notice', incident_role='executor', principal_id='executor')})
        self.addCleanup(self.center._connection.close)
        self.inc = self.center.create_event('p', 'event', dict(schema='notify.event.v1',
            kind='incident', project='qa', recipient='health', severity='notice',
            title='fixture', dedup_key='probe', event_type='health.degraded',
            source_id='probe', host_id='qa', signal_type='cpu', correlation_id='episode'))['incident_id']
        self.finished = self.center.record_work_finished('a', self.inc, 'finish',
            dict(attempt_id='attempt', session_id='existing-session', harness='opencode'))
        self.deadline = self.finished['verification_deadline']

    def tick(self, now):
        return lifecycle.expire_verification_requests(self.center, now=now, limit=2)

    def test_dead_source_wakes_once_without_recovery_or_source_receipt(self):
        self.assertEqual(0, self.tick(self.deadline-1)['expired'])
        self.assertEqual(1, self.tick(self.deadline+1)['expired'])
        current = self.center.get_incident(self.inc)
        self.assertEqual('open', current['state'])
        self.assertEqual('unknown', current['lifecycle']['verification_state'])
        self.assertIsNone(current['lifecycle'].get('last_check_receipt'))
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'").fetchone()
        self.assertIsNotNone(row)
        authority = json.loads(row['target_json'])['source_check_continuation']
        self.assertEqual('existing-session', authority['session_id'])
        self.assertIn('verification deadline', ' '.join(authority['evidence_refs']))
        with self.center._connection:
            self.center._connection.execute("UPDATE deliveries SET status='uncertain',attempt=1 WHERE id=?", (row['id'],))
        self.tick(self.deadline+1000)
        rows = self.center._connection.execute("SELECT status FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'").fetchall()
        self.assertEqual(['uncertain'], [r[0] for r in rows])

    def test_no_session_and_resolved_are_never_launched(self):
        with self.center._connection:
            self.center._connection.execute("UPDATE incident_lifecycle SET session_id=NULL WHERE incident_id=?", (self.inc,))
        self.tick(self.deadline+1)
        self.assertEqual(0, self.center._connection.execute("SELECT count(*) FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'").fetchone()[0])
        with self.center._connection:
            self.center._connection.execute("UPDATE incidents SET state='resolved' WHERE id=?", (self.inc,))
        self.assertEqual(0, self.tick(self.deadline+1000)['expired'])

    def test_existing_continuation_is_not_replaced(self):
        self.center.record_source_check('p', self.inc, 'degraded', dict(
            request_id=self.finished['request_id'], generation=self.finished['generation'],
            observed_state='degraded', evidence_refs=['fresh probe']))
        before = self.center.get_incident(self.inc)['lifecycle']['continuation']
        self.tick(self.deadline+1000)
        self.assertEqual(before, self.center.get_incident(self.inc)['lifecycle']['continuation'])
