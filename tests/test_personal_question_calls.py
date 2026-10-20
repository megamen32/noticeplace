"""Question-call admission and canonical completion; all recipients are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import uuid

import pytest

from notification_center.core import NotificationCenter
from notification_center.question_phone import QuestionCalls, call_window, verified_source


def preferences(**changes):
    return dict(enabled=True, timezone='Europe/Moscow', weekdays=[0, 1, 2, 3, 4],
                start='13:00', end='20:00', locale='ru-RU', actor='telegram:42',
                target_ref='agentcall.configured_owner', approval_ref='synthetic-consent', **changes)


@pytest.mark.parametrize('stamp,allowed', [
    ('2026-10-19T12:59:00+03:00', False), ('2026-10-19T13:00:00+03:00', True),
    ('2026-10-19T19:59:00+03:00', True), ('2026-10-19T20:00:00+03:00', False),
    ('2026-10-24T14:00:00+03:00', False), ('2026-10-25T14:00:00+03:00', False),
])
def test_window(stamp, allowed):
    assert call_window(preferences(), datetime.fromisoformat(stamp))[0] is allowed


def test_no_inferred_timezone_or_days_and_dst_is_local():
    p = preferences(); p.pop('timezone')
    assert not call_window(p, datetime.now(timezone.utc))[0]
    p = preferences(); p.pop('weekdays')
    assert not call_window(p, datetime.now(timezone.utc))[0]
    p = preferences(); p.update(timezone='Europe/Berlin', weekdays=[6], start='01:00', end='03:00')
    for stamp in ['2026-10-25T00:30:00+00:00', '2026-10-25T01:30:00+00:00']:
        assert call_window(p, datetime.fromisoformat(stamp))[0]
    assert not call_window(p, datetime.fromisoformat('2026-10-25T02:00:00+00:00'))[0]


def test_verified_source_requires_exact_herder_binding_live_session_and_unheld_stop():
    request = {'request_id': 'request-1', 'native_harness': 'codex', 'native_session_id': 'session-1',
               'native_cwd': '/srv/example'}
    binding = {'result': {'structuredContent': {'request': {'kind': 'user', 'status': 'pending',
        'target': {'harness': 'codex', 'sessionId': 'session-1', 'cwd': '/srv/example'}}}}}
    session = {'session': {'id': 'session-1', 'harness': 'codex', 'cwd': '/srv/example', 'status': 'idle'}}
    with patch('notification_center.question_phone._read_herder', side_effect=[binding, session, {'humanStopHeld': False}]) as read:
        assert verified_source(request)
        assert '/api/coordination/context?' in read.call_args_list[-1].args[0]
    wrong_binding = {'result': {'structuredContent': {'request': {'kind': 'user', 'status': 'pending',
        'target': {'harness': 'codex', 'sessionId': 'other-session', 'cwd': '/srv/example'}}}}}
    with patch('notification_center.question_phone._read_herder', return_value=wrong_binding) as read:
        assert not verified_source(request)
        read.assert_called_once()
    with patch('notification_center.question_phone._read_herder', side_effect=[binding, session, {'humanStopHeld': True}]):
        assert not verified_source(request)


@pytest.fixture
def setup():
    with TemporaryDirectory() as root:
        center = NotificationCenter(Path(root)/'db', {'token': {'project': 'test', 'max_severity': 'important'}}, default_quiet_hours=[])
        phone = Mock()
        phone.readiness.return_value = {'ready': True, 'question_ready': True}
        guard = Mock(return_value=True)
        complete = Mock(return_value={'state': 'resolved', 'native': {'status': 'resumed'}})
        clock = lambda: datetime.fromisoformat('2026-10-20T14:00:00+03:00')
        calls = QuestionCalls(center, phone, guard=guard, complete=complete, clock=clock)
        calls.set_preferences('me', preferences())
        payload = dict(schema='ask_human.request.v1', request_id=str(uuid.uuid4()), project='test', recipient='me',
                       mode='choice', message='Продолжить работу?', choices=[{'value':'yes','label':'Продолжить'}, {'value':'later','label':'Позже'}],
                       allowed_actors=['telegram:42'], native_target={'harness':'codex','session_id':'synthetic-native','cwd':'/synthetic'})
        request = center.create_human_request('token', payload)
        phone.question_call.return_value = {'ok': True, 'call_id': 'synthetic-call', 'protocol': 'noticeplace.question.v1'}
        yield center, calls, phone, guard, complete, request, payload
        center._connection.close()


def test_single_and_concurrent_request_are_one_call(setup):
    center, calls, phone, guard, complete, request, payload = setup
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: center.create_human_request('token', payload), range(8)))
        list(pool.map(lambda _: calls.run_once(), range(8)))
    assert phone.question_call.call_count == 1
    assert calls.receipt(request['request_id'])['state'] == 'active'
    assert not complete.called


def test_separate_workers_reserve_one_attempt_and_restart_never_replays(setup):
    center, calls, phone, guard, complete, request, payload = setup
    second_center = NotificationCenter(center._connection.execute('PRAGMA database_list').fetchone()[2],
        {'token': {'project': 'test', 'max_severity': 'important'}}, default_quiet_hours=[])
    second_calls = QuestionCalls(second_center, phone, guard=guard, complete=complete,
                                 clock=calls.clock)
    second_calls.set_preferences('me', preferences())
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda worker: worker.run_once(), [calls, second_calls]))
    assert phone.question_call.call_count == 1
    first_receipt = calls.receipt(request['request_id'])
    assert first_receipt['state'] == 'active'
    second_center._connection.close()
    restarted = NotificationCenter(center._connection.execute('PRAGMA database_list').fetchone()[2],
        {'token': {'project': 'test', 'max_severity': 'important'}}, default_quiet_hours=[])
    restarted_calls = QuestionCalls(restarted, phone, guard=guard, complete=complete, clock=calls.clock)
    assert not restarted_calls.run_once()
    assert phone.question_call.call_count == 1
    restarted._connection.close()


def test_request_has_durable_redacted_delivery_status(setup):
    center, calls, phone, guard, complete, request, payload = setup
    receipt = calls.receipt(request['request_id'])
    assert receipt['state'] == 'queued'
    assert receipt['attempt_id']
    calls.set_preferences('me', preferences())
    phone.readiness.return_value = {'ready': False, 'question_ready': False, 'reason': 'unavailable'}
    calls.run_once()
    status = center.get_human_request('token', request['request_id'])['phone_call']
    assert status == {'state': 'fallback', 'reason': 'interactive_gateway_unavailable'}
    assert receipt['attempt_id'] not in json.dumps(status)
    assert not phone.question_call.called


@pytest.mark.parametrize('input_method', ['speech', 'dtmf'])
def test_confirmed_answer_resolves_same_request_and_resumes_exact_native(input_method, setup):
    center, calls, phone, guard, complete, request, payload = setup
    calls.run_once()
    attempt = calls.receipt(request['request_id'])
    value = 'yes'

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self, _size):
            return json.dumps({'request_id': request['request_id'], 'status': 'resumed'}).encode()

    def runner(http_request, timeout):
        body = json.loads(http_request.data)
        assert http_request.full_url.endswith('/internal/human-requests/ask-user-completion')
        assert body['request_id'] == request['request_id']
        assert body['event'] == 'ask.user.completed'
        assert set(body) == {'event', 'event_version', 'status', 'request_id', 'result_ref'}
        return Response()

    def canonical_complete(center_arg, request_arg, answer, actor, source):
        assert source == 'phone'
        assert request_arg['request_id'] == request['request_id']
        assert (request_arg['native_harness'], request_arg['native_session_id'], request_arg['native_cwd']) == ('codex', 'synthetic-native', '/synthetic')
        from notification_center.http_api import complete_human_request
        return complete_human_request(center_arg, request_arg, answer, actor, source, runner=runner)

    calls.complete = canonical_complete
    event = {'event': 'question_answer', 'callId': 'synthetic-call', 'protocol': 'noticeplace.question.v1',
             'request_id': request['request_id'], 'attempt_id': attempt['attempt_id'], 'actor': 'telegram:42',
             'confirmed': True, 'readback': True, 'input_method': input_method, 'value': value,
             'transcript': 'discarded event text'}
    assert calls.handle_event(event)
    assert not calls.handle_event(event)
    resolved = center.get_human_request('token', request['request_id'])
    assert (resolved['state'], resolved['response_value'], resolved['response_actor']) == ('resolved', value, 'telegram:42')
    assert resolved['phone_call'] == {'state': 'completed', 'reason': ''}
    assert calls.receipt(request['request_id'])['state'] == 'completed'
    assert 'discarded event text' not in json.dumps(calls.receipt(request['request_id']))


def test_concurrent_confirmed_events_resume_once(setup):
    center, calls, phone, guard, complete, request, payload = setup
    calls.run_once()
    attempt = calls.receipt(request['request_id'])
    event = {'event': 'question_answer', 'callId': 'synthetic-call', 'protocol': 'noticeplace.question.v1',
             'request_id': request['request_id'], 'attempt_id': attempt['attempt_id'], 'actor': 'telegram:42',
             'confirmed': True, 'readback': True, 'input_method': 'dtmf', 'value': 'yes'}
    outcomes = []
    def resolve_once(center_arg, request_arg, value, actor, source):
        assert source == 'phone'
        resolved = center_arg.resolve_human_request_for_surface(request_arg['request_id'], actor, value)
        result = {'state': resolved['state'], 'value': resolved['response_value']}
        outcomes.append(result)
        return result
    calls.complete = resolve_once
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: calls.handle_event(event), range(2)))
    assert sum(results) == 1
    assert len(outcomes) == 1
    assert center.get_human_request('token', request['request_id'])['response_value'] == 'yes'


def test_prior_telegram_answer_wins_over_later_phone_answer(setup):
    center, calls, phone, guard, complete, request, payload = setup
    calls.run_once()
    attempt = calls.receipt(request['request_id'])
    center.resolve_human_request_from_telegram(request['request_id'], 'telegram:42', 'later')
    calls.complete = Mock(return_value={'state': 'resolved', 'value': 'later', 'accepted': False})
    event = {'event': 'question_answer', 'callId': 'synthetic-call', 'protocol': 'noticeplace.question.v1',
             'request_id': request['request_id'], 'attempt_id': attempt['attempt_id'], 'actor': 'telegram:42',
             'confirmed': True, 'readback': True, 'input_method': 'speech', 'value': 'yes'}
    assert not calls.handle_event(event)
    assert center.get_human_request('token', request['request_id'])['response_value'] == 'later'
    assert calls.receipt(request['request_id'])['state'] == 'fallback'


@pytest.mark.parametrize('condition', ['resolved', 'cancelled', 'muted', 'wrong_actor', 'human_stop', 'no_timezone', 'disabled'])
def test_pending_admission_guards(setup, condition):
    center, calls, phone, guard, complete, request, payload = setup
    if condition in {'resolved', 'cancelled'}:
        with center._connection:
            center._connection.execute('UPDATE human_requests SET state=? WHERE request_id=?', (condition, request['request_id']))
    elif condition == 'human_stop':
        guard.return_value = False
    else:
        p = preferences()
        if condition == 'wrong_actor': p['actor'] = 'telegram:7'
        if condition == 'muted': p['muted'] = True
        if condition == 'no_timezone': p.pop('timezone')
        if condition == 'disabled': p['enabled'] = False
        calls.set_preferences('me', p)
    calls.run_once()
    assert not phone.question_call.called
    assert not complete.called


def test_dispatch_and_final_send_recheck_window(setup):
    center, calls, phone, guard, complete, request, payload = setup
    times = iter([datetime.fromisoformat('2026-10-20T19:59:59+03:00'), datetime.fromisoformat('2026-10-20T20:00:00+03:00')])
    calls.clock = lambda: next(times)
    calls.run_once()
    assert not phone.question_call.called


@pytest.mark.parametrize('failure', [TimeoutError('unknown'), RuntimeError('gateway unavailable')])
def test_gateway_failure_never_invents_answer_or_redials(setup, failure):
    center, calls, phone, guard, complete, request, payload = setup
    phone.question_call.side_effect = failure
    calls.run_once(); calls.run_once()
    assert phone.question_call.call_count == 1
    assert not complete.called
    assert center.get_human_request('token', request['request_id'])['state'] == 'pending'


def test_no_answer_voicemail_ambiguous_and_transcript_are_not_confirmed_receipts(setup):
    center, calls, phone, guard, complete, request, payload = setup
    calls.run_once()
    for event in [dict(event='transcript_final', speaker='remote', text='Да'),
                  dict(event='question_answer', confirmed=False, value='yes'),
                  dict(event='question_answer', confirmed=True, readback=False, value='yes'),
                  dict(event='question_answer', confirmed=True, readback=True, value='yes', actor='telegram:7'),
                  dict(event='question_answer', confirmed=True, readback=True, value='yes', input_method='unknown'),
                  dict(event='question_answer', confirmed=True, readback=True, value='not-a-choice', input_method='speech')]:
        calls.handle_event(dict(callId='synthetic-call', request_id=request['request_id'], **event))
    assert not complete.called
    calls.handle_event(dict(event='ended', callId='synthetic-call'))
    assert calls.receipt(request['request_id'])['state'] == 'fallback'
    assert center.get_human_request('token',request['request_id'])['state'] == 'pending'
