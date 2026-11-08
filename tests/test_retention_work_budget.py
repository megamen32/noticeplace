"""Maintenance cost must depend on its page, not all unrelated questions."""
import sqlite3

from notification_center.core import NotificationCenter
from notification_center.retention import RetentionWorker


def test_retention_page_does_not_scan_unrelated_human_requests(tmp_path):
    center = NotificationCenter(tmp_path / 'notice.db',
        {'source': {'project': 'retention', 'max_severity': 'critical'}})
    db = center._connection
    try:
        created = center.create_event('source', 'fixture', {
            'schema': 'notify.event.v1', 'kind': 'incident', 'project': 'retention',
            'recipient': 'me', 'severity': 'critical', 'title': 'Fixture', 'dedup_key': 'one'})
        with db:
            db.execute("UPDATE incidents SET state='resolved' WHERE id=?", (created['incident_id'],))
            db.execute('DELETE FROM audit_events')
            db.executemany('INSERT INTO audit_events(id,incident_id,type,payload_json,created_at) VALUES(?,?,?,?,?)',
                [(f'a-{i:03}', created['incident_id'], 'ordinary', '{}', 1) for i in range(100)])
            db.executemany('INSERT INTO human_requests(request_id,project,recipient,mode,message,choices_json,'
                'allowed_actors_json,state,created_at,incident_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                [(f'q-{i}', 'other', 'me', 'question', '', '[]', '[]', 'resolved', 1, f'other-{i}')
                 for i in range(10000)])
        # Interrupt pathological O(page * whole table) work deterministically,
        # without a timing assertion that depends on the shared host's load.
        instructions = 0
        def guard():
            nonlocal instructions
            instructions += 1000
            return instructions > 100000
        db.set_progress_handler(guard, 1000)
        try:
            result = RetentionWorker(db, batch_size=100).run_batch(now=2000000000, dry_run=False)
        except sqlite3.OperationalError as error:
            raise AssertionError('One maintenance page scanned unrelated question history') from error
        finally:
            db.set_progress_handler(None, 0)
        assert result.deleted == 100
    finally:
        db.close()
