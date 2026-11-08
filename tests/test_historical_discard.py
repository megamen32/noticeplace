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
        {'questions': [dict(state='pending')]},
        {'attempt_id': 'accepted'},
        {'started_sessions': [dict(event_id='accepted')]},
    ):
        with pytest.raises(ValueError):
            decide({**base, **changed}, 'revision')
