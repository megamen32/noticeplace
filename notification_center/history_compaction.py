"""Drop old terminal event prose while retaining identity and replay fences."""
import hashlib
import json
import time

CURSOR_KEY = 'history_event_compaction_cursor'
ARCHIVED = 'Подробности удалены по сроку хранения истории.'
SAFE_TERMINAL = """i.state='resolved' AND i.updated_at<? AND i.resolved_at<?
 AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=i.id
   AND lower(d.status) NOT IN ('sent','failed','cancelled','superseded'))
 AND NOT EXISTS (SELECT 1 FROM human_requests h WHERE h.incident_id=i.id
   AND (lower(h.state) IN ('pending','sending','unknown','uncertain','open')
     OR lower(coalesce(h.task_control_state,'clear')) IN ('pending','sending','unknown','uncertain','requested','stop')
     OR (lower(coalesce(h.task_control_state,'clear'))='acknowledged'
         AND lower(coalesce(h.task_control_kind,''))='hold' AND h.task_resume_ref IS NULL)))"""


def compact_event_details(connection, *, cutoff, batch_size=100, dry_run=False):
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be 1..1000')
    row=connection.execute('SELECT value FROM runtime_settings WHERE key=?',(CURSOR_KEY,)).fetchone()
    after=int(row[0]) if row else 0
    # First bound by monotonically increasing event rowid. Eligibility must not
    # turn a quiet tick into a scan of all historical episodes.
    page=connection.execute('SELECT rowid,idempotency_key,incident_id,created_at,payload_sha256 '
                            'FROM events WHERE rowid>? ORDER BY rowid LIMIT ?', (after,batch_size)).fetchall()
    compacted=0
    with connection:
        for row in page:
            rowid,key,incident_id,created_at,old_hash=tuple(row)
            if created_at>=cutoff or old_hash:
                continue
            eligible=connection.execute('SELECT 1 FROM incidents i WHERE i.id=? AND '+SAFE_TERMINAL,
                                        (incident_id,cutoff,cutoff)).fetchone()
            if not eligible:
                continue
            # A single oversized legacy event is left intact instead of
            # overflowing the bounded mesh response frame.
            size=connection.execute('SELECT length(CAST(payload_json AS BLOB)) FROM events WHERE rowid=?',(rowid,)).fetchone()[0]
            if size>1_000_000:
                continue
            original=connection.execute('SELECT payload_json FROM events WHERE rowid=?',(rowid,)).fetchone()[0]
            payload=json.loads(original)
            if not isinstance(payload,dict):
                continue
            digest=hashlib.sha256(original.encode()).hexdigest()
            # Structured control/evidence/session/receipt fields remain. Their
            # independent lifetime is not decided by a free-text history TTL.
            for field in ('body','details','diagnostics','source_context'):
                payload.pop(field,None)
            payload['body']=ARCHIVED
            if not dry_run:
                connection.execute('UPDATE events SET payload_json=?,payload_sha256=? WHERE rowid=? AND payload_sha256 IS NULL',
                                   (json.dumps(payload,ensure_ascii=False,sort_keys=True),digest,rowid))
                connection.execute('UPDATE incidents SET body=? WHERE id=?',(ARCHIVED,incident_id))
            compacted+=1
        if not dry_run:
            connection.execute('INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?) '
                               'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                               (CURSOR_KEY,str(page[-1][0] if page else 0),time.time()))
    return {'sampled':len(page),'compacted':compacted,'dry_run':dry_run,'cutoff':cutoff}
