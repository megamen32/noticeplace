import tempfile
from pathlib import Path

import pytest

from notification_center.core import NotificationCenter, ValidationError
from notification_center.historical_discard import preview, discard


def test_exact_discard_is_terminal_and_retains_replay_receipts():
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'db', {
            'source': {'project': 'test', 'max_severity': 'emergency',
                       'principal_id': 'source', 'verification_sources': ['probe']},
            'agent': {'project': 'test', 'max_severity': 'emergency',
                      'principal_id': 'agent', 'incident_role': 'executor'}})
        event = dict(schema='notify.event.v1', kind='incident', severity='notice',
                     project='test', recipient='health', title='Old', body='Old',
                     dedup_key='probe', event_type='health.degraded', source_id='probe',
                     host_id='host', signal_type='http', correlation_id='historical', observed_at=100)
        incident = center.create_event('source', 'existing-key', event)['incident_id']
        current = center.create_event('source', 'current-key', {**event, 'correlation_id': 'current'})['incident_id']
        center._connection.execute("INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,attempt,created_at,updated_at) VALUES ('queued-agent',?,'gptadmin.agent:health-diagnosis','queued-agent-key',0,'queued',0,0,0)", (incident,))
        center._connection.commit()
        snapshot = preview(center._connection, incident)
        assert any(delivery['status'] == 'queued' for delivery in snapshot['deliveries'])
        with pytest.raises(ValueError):
            discard(center._connection, incident, '0' * 64, 'operator')
        assert center.get_incident(incident)['state'] == 'open'
        discard(center._connection, incident, snapshot['revision'], 'operator')
        assert center.get_incident(incident)['state'] == 'discarded'
        assert center.get_incident(current)['state'] == 'open'
        assert all(delivery['status'] != 'queued' for delivery in preview(center._connection, incident)['deliveries'])
        assert center.create_event('source', 'existing-key', event)['incident_id'] == incident
        with pytest.raises(ValidationError):
            center.create_event('source', 'new-key', event)
        with pytest.raises(ValidationError):
            center.record_work_finished('agent', incident, 'new-finish',
                                        dict(attempt_id='a', session_id='s', harness='codex'))
        with pytest.raises(ValidationError):
            center.snooze(incident, 9999999999, 'operator')
        with pytest.raises(ValidationError):
            center.record_health_update(incident, 'verification-new', 'health.verification_recorded', {'source_id': 'probe'})
        new_incident = center.create_event('source', 'next-key',
                         {**event, 'correlation_id': 'omniroute-host:next'})['incident_id']
        assert center.get_incident(new_incident)['state'] == 'open'
        assert center._connection.execute('SELECT count(*) FROM events WHERE incident_id=?', (incident,)).fetchone()[0] == 1


def test_decision_refuses_unknown_external_effect_and_executor():
    from notification_center.historical_discard import decide
    base = dict(revision='revision', state='open', correlation_id='old', metadata_json='{}',
                deliveries=[], questions=[], attempt_id=None, session_id=None, started_sessions=[])
    for changed in (
        {'deliveries': [dict(status='claimed')]},
        {'deliveries': [dict(status='uncertain')]},
        {'deliveries': [dict(status='queued', channel='gptadmin.agent:health-diagnosis', attempt=1)]},
        {'deliveries': [dict(status='failed', channel='gptadmin.agent:health-diagnosis', result_json_sha256='receipt')]},
        {'questions': [dict(state='pending')]},
        {'attempt_id': 'accepted'},
        {'started_sessions': [dict(event_id='accepted')]},
    ):
        with pytest.raises(ValueError):
            decide({**base, **changed}, 'revision')


def test_discard_preserves_other_consumer_and_blocks_recorded_sessions():
    with tempfile.TemporaryDirectory() as tmp:
        center = NotificationCenter(Path(tmp) / 'db', {
            'a': {'project': 'test', 'max_severity': 'emergency', 'consumer_id': 'a'},
            'b': {'project': 'test', 'max_severity': 'emergency', 'consumer_id': 'b'}})
        event = dict(schema='notify.event.v1', kind='incident', severity='notice', project='test',
                     recipient='health', title='Old', body='Old', dedup_key='probe',
                     event_type='health.degraded', source_id='probe', host_id='host',
                     signal_type='http', correlation_id='same', observed_at=100)
        first = center.create_event('a', 'event-a', event)['incident_id']
        discard(center._connection, first, preview(center._connection, first)['revision'], 'operator')
        assert center.create_event('b', 'event-b', event)['incident_id'] != first
        second = center.create_event('a', 'event-c', {**event, 'correlation_id': 'second'})['incident_id']
        center._connection.execute("UPDATE events SET event_type='health.remediation_session_started' WHERE incident_id=?", (second,))
        center._connection.commit()
        with pytest.raises(ValueError):
            discard(center._connection, second, preview(center._connection, second)['revision'], 'operator')
        third = center.create_event('a', 'event-d', {**event, 'correlation_id': 'third'})['incident_id']
        center._connection.execute("INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,attempt,created_at,updated_at,result_json) VALUES ('sent-agent',?,'gptadmin.agent:health-diagnosis','sent-agent-key',0,'sent',1,0,0,?)", (third, '{"session_id":"accepted"}'))
        center._connection.commit()
        with pytest.raises(ValueError):
            discard(center._connection, third, preview(center._connection, third)['revision'], 'operator')


def test_legacy_tombstone_migrates_consumer_from_original_incident():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'db'
        scopes = {'a': {'project': 'test', 'max_severity': 'emergency', 'consumer_id': 'a'},
                  'b': {'project': 'test', 'max_severity': 'emergency', 'consumer_id': 'b'}}
        center = NotificationCenter(path, scopes)
        event = dict(schema='notify.event.v1', kind='incident', severity='notice', project='test',
                     recipient='health', title='Old', body='Old', dedup_key='probe',
                     event_type='health.degraded', source_id='probe', host_id='host',
                     signal_type='http', correlation_id='old', observed_at=100)
        incident = center.create_event('a', 'original', event)['incident_id']
        db = center._connection
        db.execute('DROP TABLE incident_discard_tombstones')
        db.execute('CREATE TABLE incident_discard_tombstones (incident_id TEXT PRIMARY KEY,project TEXT NOT NULL,recipient TEXT NOT NULL,dedup_key TEXT NOT NULL,correlation_id TEXT NOT NULL,revision TEXT NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL)')
        db.execute("INSERT INTO incident_discard_tombstones VALUES (?, 'test','health','probe','old','rev','operator',0)", (incident,))
        db.commit()
        db.close()
        migrated = NotificationCenter(path, scopes)
        assert migrated._connection.execute('SELECT consumer_id FROM incident_discard_tombstones WHERE incident_id=?', (incident,)).fetchone()[0] == 'a'
        with pytest.raises(ValidationError):
            migrated.create_event('a', 'same-consumer', event)
        assert migrated.create_event('b', 'other-consumer', event)['incident_id'] != incident


def test_legacy_orphan_tombstone_refuses_guessing_consumer():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'db'
        center = NotificationCenter(path, {})
        db = center._connection
        db.execute('DROP TABLE incident_discard_tombstones')
        db.execute('CREATE TABLE incident_discard_tombstones (incident_id TEXT PRIMARY KEY,project TEXT NOT NULL,recipient TEXT NOT NULL,dedup_key TEXT NOT NULL,correlation_id TEXT NOT NULL,revision TEXT NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL)')
        db.execute("INSERT INTO incident_discard_tombstones VALUES ('missing','test','health','probe','old','rev','operator',0)")
        db.commit()
        db.close()
        with pytest.raises(RuntimeError, match='orphan discard tombstone'):
            NotificationCenter(path, {})
