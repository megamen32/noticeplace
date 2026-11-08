"""Isolated maintenance: expiry is not recovery, an answer, or a retry."""
import json
import sqlite3
import time

import pytest

from notification_center.core import NotificationCenter, ValidationError


@pytest.fixture
def center(tmp_path):
    result = NotificationCenter(tmp_path / 'notice.db',
        {'p': {'project': 'p', 'max_severity': 'critical'}}, default_quiet_hours=[])
    yield result
    result._connection.close()


def question(center, request_id='q', **fields):
    payload = dict(schema='ask_human.request.v1', request_id=request_id, project='p',
                   recipient='fixture', mode='question', message='Fixture question',
                   allowed_actors=['fixture'])
    center.create_human_request('p', payload)
    with center._connection:
        values = {'created_at': time.time()-10*86400, **fields}
        center._connection.execute('UPDATE human_requests SET '+
            ','.join(f'{key}=?' for key in values)+' WHERE request_id=?',
            (*values.values(), request_id))
    return payload


def states(center):
    return dict(center._connection.execute('SELECT request_id,state FROM human_requests'))


def test_tick_expires_old_unbound_question_without_recovery_or_resume(center):
    payload = question(center)
    before = center._connection.execute('SELECT count(*) FROM events').fetchone()[0]
    center.run_history_retention_tick()
    assert states(center) == {'q': 'expired'}
    row = center._connection.execute('SELECT * FROM human_requests').fetchone()
    assert row['resolved_at'] is None and row['response_value'] is None
    assert row['task_resume_ref'] is None
    assert center._connection.execute('SELECT count(*) FROM human_request_completions').fetchone()[0] == 0
    assert center._connection.execute('SELECT count(*) FROM events').fetchone()[0] == before
    assert center._connection.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
    replay = center.create_human_request('p', payload)
    assert replay['idempotent'] and replay['state'] == 'expired'
    assert center.list_human_requests('p') == []
    assert center.get_human_request('p', 'q')['state'] == 'expired'
    with pytest.raises(ValidationError, match='human request is expired'):
        center.resolve_human_request('p', 'q', 'fixture', 'late answer')
    with pytest.raises(ValidationError):
        center.create_human_request('p', {**payload, 'message': 'Changed'})


def test_preview_is_read_only_and_deadlines_take_precedence(center):
    from notification_center.maintenance_expiry import maintain_expiry
    now = time.time()
    question(center, 'age')
    question(center, 'deadline', created_at=now, expires_at=now-1)
    question(center, 'future', expires_at=now+86400)
    question(center, 'fresh', created_at=now)
    before = center._connection.total_changes
    report = maintain_expiry(center._connection, now=now)
    assert report['questions']['eligible'] == 2
    assert report['questions']['expired'] == 0
    assert center._connection.total_changes == before
    assert set(states(center).values()) == {'pending'}
    applied = maintain_expiry(center._connection, now=now, dry_run=False)
    assert applied['questions']['expired'] == 2
    assert states(center) == dict(age='expired', deadline='expired', future='pending', fresh='pending')


@pytest.mark.parametrize('fields', [
    {'native_harness': 'codex'}, {'native_session_id': 'session'}, {'native_cwd': '/fixture'},
    {'kind': 'secret'}, {'mode': 'notify'}, {'state': 'uncertain'}, {'state': 'unknown'},
    {'task_context_json': '{}'}, {'task_decision_ref': 'decision'},
    {'task_control_state': 'stop'}, {'task_control_state': 'requested'},
    {'task_control_state': 'acknowledged', 'task_control_kind': 'hold'},
    {'task_control_id': 'stop-id'}, {'task_control_effects_json': '["effect"]'},
    {'task_notification_state': 'unknown'}, {'task_resume_ref': 'resume'},
    {'task_late_instructions_json': '["stop"]'}, {'task_decision_value': 'yes'},
    {'response_value': 'yes'}, {'response_actor': 'actor'}, {'response_result_ref': 'result'},
    {'resolved_at': 1}, {'cancelled_at': 1}, {'progress_json': '{}'},
    {'telegram_document_state': 'sending'}, {'telegram_document_state': 'uncertain'},
    {'incident_id': 'unverified-binding'},
])
def test_ownership_stop_unknown_and_answer_evidence_are_preserved(center, fields):
    from notification_center.maintenance_expiry import maintain_expiry
    question(center, **fields)
    before = dict(center._connection.execute('SELECT * FROM human_requests').fetchone())
    result = maintain_expiry(center._connection, dry_run=False)
    assert result['questions']['expired'] == 0
    assert dict(center._connection.execute('SELECT * FROM human_requests').fetchone()) == before


def test_existing_completion_is_preserved(center):
    from notification_center.maintenance_expiry import maintain_expiry
    question(center)
    with center._connection:
        center._connection.execute('INSERT INTO human_request_completions '
            '(request_id,result_ref,harness,session_id,cwd,state,next_attempt_at,card_next_attempt_at,created_at,updated_at) '
            "VALUES('q','result','codex','session','/fixture','unknown',0,0,1,1)")
    assert maintain_expiry(center._connection, dry_run=False)['questions']['expired'] == 0
    assert states(center)['q'] == 'pending'


def test_stale_open_is_reported_not_archived_and_replay_is_unchanged(center):
    from notification_center.maintenance_expiry import maintain_expiry, REPORT_KEY
    event = dict(project='p', recipient='fixture', kind='incident', severity='critical',
                 title='Fixture', dedup_key='old')
    created = center.create_event('p', 'old-event', event)
    incident_id = created['incident_id']
    with center._connection:
        center._connection.execute('UPDATE incidents SET created_at=1,updated_at=1 WHERE id=?', (incident_id,))
    before = dict(center._connection.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone())
    report = maintain_expiry(center._connection, dry_run=False)
    assert report['incidents']['requires_owner_review'] == 1
    assert report['incidents']['sample_ids'] == [incident_id]
    assert dict(center._connection.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()) == before
    assert center.create_event('p', 'old-event', event)['idempotent']
    saved = json.loads(center._connection.execute('SELECT value FROM runtime_settings WHERE key=?', (REPORT_KEY,)).fetchone()[0])
    assert saved['scope'] == 'bounded_page' and saved['incidents']['requires_owner_review'] == 1


def test_pages_are_bounded_and_revisit_records(center):
    from notification_center.maintenance_expiry import maintain_expiry
    for index in range(5):
        question(center, str(index))
    counts = [maintain_expiry(center._connection, batch_size=2, dry_run=False)['questions']['expired']
              for _ in range(5)]
    assert counts == [2, 2, 1, 0, 0]
    assert set(states(center).values()) == {'expired'}


def test_update_rechecks_ownership_after_sampling(center):
    from notification_center.maintenance_expiry import maintain_expiry
    question(center)
    db = center._connection
    class ConcurrentOwnership:
        def execute(self, sql, args=()):
            if sql.startswith('UPDATE human_requests'):
                db.execute("UPDATE human_requests SET native_harness='codex' WHERE request_id='q'")
            return db.execute(sql, args)
        def __enter__(self):
            db.__enter__()
            return self
        def __exit__(self, *args):
            return db.__exit__(*args)
    report = maintain_expiry(ConcurrentOwnership(), dry_run=False)
    assert report['questions']['expired'] == 0
    assert states(center)['q'] == 'pending'


def test_work_is_bounded_in_large_history(center):
    from notification_center.maintenance_expiry import maintain_expiry
    db = center._connection
    with db:
        db.executemany('INSERT INTO human_requests(request_id,project,recipient,mode,message,'
            'choices_json,allowed_actors_json,state,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            [(str(i),'p','fixture','question','','[]','[]','pending',1) for i in range(10000)])
    steps = 0
    def guard():
        nonlocal steps
        steps += 1000
        return steps > 100000
    db.set_progress_handler(guard, 1000)
    try:
        result = maintain_expiry(db, batch_size=10, dry_run=False)
    except sqlite3.OperationalError as error:
        raise AssertionError('Maintenance exceeded bounded page work') from error
    finally:
        db.set_progress_handler(None, 0)
    assert result['questions']['sampled'] == result['questions']['expired'] == 10


def test_old_bound_stop_and_unknown_incident_remain_visible(center):
    from notification_center.maintenance_expiry import maintain_expiry
    incident_id = center.create_event('p', 'protected-event', dict(project='p',
        recipient='fixture', kind='incident', severity='critical', title='Fixture',
        dedup_key='protected'))['incident_id']
    question(center, incident_id=incident_id, task_control_state='stop')
    db = center._connection
    with db:
        db.execute('UPDATE incidents SET updated_at=1 WHERE id=?', (incident_id,))
        db.execute('INSERT INTO deliveries(id,incident_id,channel,delivery_key,due_at,status,'
            "attempt,created_at,updated_at) VALUES('unknown',?,'matrix.call','unknown-key',1,'uncertain',1,1,1)",
            (incident_id,))
    report = maintain_expiry(db, dry_run=False)
    assert report['incidents']['protected_with_evidence'] == 1
    assert report['incidents']['requires_owner_review'] == 0
    assert report['questions']['expired'] == 0
    assert states(center)['q'] == 'pending'
    assert db.execute('SELECT state FROM incidents WHERE id=?', (incident_id,)).fetchone()[0] == 'open'
    assert db.execute("SELECT status FROM deliveries WHERE id='unknown'").fetchone()[0] == 'uncertain'


def test_retention_setting_and_cursor_revisit_future_deadline(center):
    from notification_center.maintenance_expiry import maintain_expiry
    now = time.time()
    question(center, 'age')
    question(center, 'future', expires_at=now+10)
    with center._connection:
        center._connection.execute("INSERT INTO runtime_settings(key,value,updated_at) VALUES('history_retention_days','30',0)")
    assert maintain_expiry(center._connection, now=now, dry_run=False)['questions']['expired'] == 0
    # Empty page resets the cursor; a subsequent sweep sees the elapsed deadline.
    maintain_expiry(center._connection, now=now+20, dry_run=False)
    assert maintain_expiry(center._connection, now=now+20, dry_run=False)['questions']['expired'] == 1
    assert states(center) == {'age': 'pending', 'future': 'expired'}


@pytest.mark.parametrize('options', [
    {'batch_size': 0}, {'batch_size': 1001}, {'batch_size': True},
    {'now': float('nan')}, {'cutoff': float('inf')}, {'now': 10, 'cutoff': 11},
])
def test_invalid_budget_or_time_does_not_mutate(center, options):
    from notification_center.maintenance_expiry import maintain_expiry
    before = center._connection.total_changes
    with pytest.raises(ValueError):
        maintain_expiry(center._connection, dry_run=False, **options)
    assert center._connection.total_changes == before
