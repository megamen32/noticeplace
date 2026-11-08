"""Read-only review probes; all stores are disposable, no transports involved."""
import runpy
import unittest
from pathlib import Path
from notification_center import incident_lifecycle

ROOT = Path(__file__).resolve().parents[1]


class DeadlineGroupReview(unittest.TestCase):
    def fixture(self):
        fixture = runpy.run_path(str(ROOT/'tests/test_incident_lifecycle.py'))['LifecycleTests']()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def expire(self, center):
        deadline = center._connection.execute('SELECT MAX(verification_deadline) FROM incident_lifecycle').fetchone()[0]
        return incident_lifecycle.expire_verification_requests(center, now=deadline+1)

    def test_deadline_reanchors_cancelled_never_dispatched_group_ticket(self):
        fixture = self.fixture()
        latest, first = fixture.cancelled_shared_authority()
        center = fixture.center
        before = center._connection.execute('SELECT status,attempt,claimed_at,result_json FROM deliveries WHERE id=?',
            (first['continuation_delivery_id'],)).fetchone()
        self.assertEqual(('cancelled',0,None,None), tuple(before))
        self.expire(center)
        rows = center._connection.execute("SELECT * FROM deliveries WHERE status='queued' AND json_type(target_json,'$.source_check_continuation')='object'").fetchall()
        self.assertEqual(1, len(rows), 'dead verifier must continue the still-open peer after definite non-dispatch')
        self.assertEqual(latest, rows[0]['incident_id'])

    def test_group_unknown_dispatch_history_never_replays(self):
        fixture = self.fixture()
        latest, first = fixture.cancelled_shared_authority()
        center = fixture.center
        with center._connection:
            center._connection.execute("UPDATE deliveries SET status='uncertain',attempt=1 WHERE id=?", (first['continuation_delivery_id'],))
        self.expire(center)
        rows = center._connection.execute("SELECT id,status FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'").fetchall()
        self.assertEqual([(first['continuation_delivery_id'],'uncertain')], [tuple(row) for row in rows])
