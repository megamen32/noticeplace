"""Exact operator discard of historical incidents; never asserts health."""
import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid


def preview(connection, incident_id):
    row = connection.execute(
        'SELECT i.id,i.state,i.project,i.recipient,i.dedup_key,i.correlation_id,'
        'i.consumer_id,i.updated_at,l.generation,l.metadata_json,l.request_id,l.verification_state,'
        'l.attempt_id,l.attempt_state,l.session_id '
        'FROM incidents i LEFT JOIN incident_lifecycle l ON l.incident_id=i.id WHERE i.id=?',
        (incident_id,)).fetchone()
    if row is None:
        raise ValueError('incident not found')
    item = dict(row)
    item['deliveries'] = [dict(r) for r in connection.execute(
        'SELECT id,status,channel,attempt,result_json,last_error,updated_at FROM deliveries WHERE incident_id=? ORDER BY id', (incident_id,)).fetchall()]
    for delivery in item['deliveries']:
        for field in ('result_json', 'last_error'):
            value = delivery.pop(field)
            delivery[field + '_sha256'] = hashlib.sha256(value.encode()).hexdigest() if value else None
    item['questions'] = [dict(r) for r in connection.execute(
        'SELECT request_id,state FROM human_requests WHERE incident_id=? ORDER BY request_id', (incident_id,)).fetchall()]
    item['started_sessions'] = [dict(r) for r in connection.execute(
        "SELECT event_id,event_type,payload_json FROM events WHERE incident_id=? AND event_type IN ('health.diagnosis_session_started','health.orchestrator_session_started','health.remediation_session_started') ORDER BY event_id", (incident_id,)).fetchall()]
    for session in item['started_sessions']:
        session['payload_sha256'] = hashlib.sha256(session.pop('payload_json').encode()).hexdigest()
    raw = json.dumps(item, sort_keys=True, separators=(',', ':')).encode()
    return {**item, 'revision': hashlib.sha256(raw).hexdigest()}


def decide(item, expected_revision):
    if item['revision'] != expected_revision:
        raise ValueError('incident changed since preview')
    if item['state'] != 'open' or not item['correlation_id'] or not item['metadata_json']:
        raise ValueError('incident is not an open typed historical episode')
    if any(d['status'] in ('claimed', 'sending', 'uncertain', 'unknown') for d in item['deliveries']):
        raise ValueError('delivery outcome requires reconciliation')
    if any(d.get('channel', '').startswith('gptadmin.agent:') and
           (d['status'] == 'sent' or d.get('result_json_sha256') or
            (d.get('attempt', 0) > 0 and d['status'] in ('queued', 'retry')))
           for d in item['deliveries']):
        raise ValueError('accepted executor delivery requires reconciliation')
    if any(q['state'] not in ('cancelled', 'resolved', 'expired') for q in item['questions']):
        raise ValueError('active human request requires reconciliation')
    if item['attempt_id'] or item['session_id'] or item['started_sessions']:
        raise ValueError('executor session requires reconciliation')
    return item


def discard(connection, incident_id, expected_revision, actor):
    if not actor or len(actor) > 128 or not expected_revision:
        raise ValueError('actor and exact preview revision are required')
    if isinstance(connection, sqlite3.Connection):
        connection.execute('BEGIN IMMEDIATE')
    with connection:
        item = decide(preview(connection, incident_id), expected_revision)
        now = time.time()
        connection.execute('INSERT INTO incident_discard_tombstones '
            '(incident_id,project,recipient,consumer_id,dedup_key,correlation_id,revision,actor,created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (incident_id,item['project'],item['recipient'],item['consumer_id'],item['dedup_key'],item['correlation_id'],expected_revision,actor,now))
        connection.execute("UPDATE incidents SET state='discarded',updated_at=? WHERE id=? AND state='open'", (now,incident_id))
        connection.execute("UPDATE deliveries SET status='cancelled',updated_at=? WHERE incident_id=? AND status IN ('queued','retry')", (now,incident_id))
        connection.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?)',
            (uuid.uuid4().hex,incident_id,'incident_discarded',actor,json.dumps({'revision':expected_revision},sort_keys=True),now))
    return {'incident_id': incident_id, 'state': 'discarded', 'revision': expected_revision}


def main():
    if os.geteuid() != 0:
        raise SystemExit('historical discard requires the local root operator')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--incident', required=True)
    parser.add_argument('--apply-revision')
    parser.add_argument('--actor', default='operator')
    args = parser.parse_args()
    if args.database.startswith('mesh:'):
        from .mesh_store import connect
        db = connect(args.database, readonly=not bool(args.apply_revision))
    else:
        db = sqlite3.connect(args.database)
        db.row_factory = sqlite3.Row
    try:
        result = discard(db,args.incident,args.apply_revision,args.actor) if args.apply_revision else preview(db,args.incident)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    finally:
        db.close()


if __name__ == '__main__':
    main()
