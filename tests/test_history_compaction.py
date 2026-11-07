import json
import tempfile
import time
import unittest
from pathlib import Path
from notification_center.core import NotificationCenter, IdempotencyConflict


class HistoryCompactionTests(unittest.TestCase):
    def test_old_details_removed_but_original_replay_and_unknown_protected(self):
        from notification_center.history_compaction import compact_event_details
        with tempfile.TemporaryDirectory() as root:
            center=NotificationCenter(Path(root)/'test.db',{'p':{'project':'p','max_severity':'emergency'}},default_quiet_hours=[])
            self.addCleanup(center._connection.close)
            event={'project':'p','recipient':'me','kind':'notification','severity':'notice',
                   'title':'Old report','body':'detail '*1000,'dedup_key':'a'}
            first=center.create_event('p','a',event)
            other=center.create_event('p','b',{**event,'dedup_key':'b'})
            now=time.time();old=now-9*86400
            with center._connection:
                center._connection.execute("INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,attempt,created_at,updated_at) VALUES(?,?,'matrix.call',?,?,'uncertain',1,?,?)",('fixture-receipt',other['incident_id'],'fixture-key',now,now,now))
                center._connection.execute("UPDATE incidents SET state='resolved',resolved_at=?,updated_at=?",(old,old))
                center._connection.execute('UPDATE events SET created_at=?',(old,))
                center._connection.execute("UPDATE deliveries SET status='cancelled'")
                changed=center._connection.execute("UPDATE deliveries SET status='uncertain' WHERE incident_id=?",(other['incident_id'],))
                self.assertGreater(changed.rowcount,0)
            result=compact_event_details(center._connection,cutoff=now-7*86400,batch_size=10)
            self.assertEqual(1,result['compacted'])
            row=center._connection.execute('SELECT payload_json,payload_sha256 FROM events WHERE idempotency_key=?',('a',)).fetchone()
            self.assertNotIn('detail',row['payload_json'])
            self.assertEqual(64,len(row['payload_sha256']))
            protected=center._connection.execute('SELECT payload_json FROM events WHERE idempotency_key=?',('b',)).fetchone()[0]
            self.assertIn('detail',protected)
            self.assertTrue(center.create_event('p','a',event)['idempotent'])
            with self.assertRaises(IdempotencyConflict):center.create_event('p','a',{**event,'body':'different'})
            self.assertEqual(2,center._connection.execute('SELECT count(*) FROM events').fetchone()[0])

    def test_bound_is_enforced(self):
        from notification_center.history_compaction import compact_event_details
        with self.assertRaises(ValueError):compact_event_details(None,cutoff=0,batch_size=1001)
