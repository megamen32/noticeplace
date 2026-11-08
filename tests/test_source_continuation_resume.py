"""Deadline continuations must resume the exact authority, never admit a new chat."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from notification_center import agent_job_helper as helper


class Response:
    status = 200

    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return json.dumps(self.body).encode()


class SourceContinuationResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = dict(url='http://127.0.0.1:18787/api/sessions/new-or-resume',
                            harness='opencode', model=helper.HEALTH_LAUNCH_DEFAULT,
                            name='repair', cwd=self.tmp.name, mode='queue', instruction='repair',
                            health_launch_token='fixture-only')
        self.event = dict(schema='notify.agent-job.v1', job_id='health-diagnosis',
                          attempt_id='continuation-delivery-attempt',
                          incident=dict(id='inc_fixture', project='qa'),
                          health=dict(incident_session_policy=dict(enabled=False, ttlSeconds=0)),
                          source_check_continuation=dict(incident_id='inc_fixture',
                              session_id='original-session', harness='opencode', attempt_id='finished-attempt',
                              request_id='old-check', generation=2, evidence_refs=['verification deadline expired']))
        self.calls = []
        self.stopped = False
        self.owner_stopped = False
        self.missing = False
        self.resume_result = dict(ok=True, admitted=True, pending=True)
        self.session_override = {}
        for name, value in [('_launch_policy', {}), ('_load_health_callback', ('http://127.0.0.1', 'fake')),
                            ('_run_health_diagnosis', {'status': 'completed'}), ('arm_session_autopilot', True)]:
            patcher = mock.patch.object(helper, name, return_value=value)
            setattr(self, name.removeprefix('_'), patcher.start())
            self.addCleanup(patcher.stop)
        route = mock.patch.object(helper, '_selected_route_profile', side_effect=lambda p, *a, **k: p)
        route.start(); self.addCleanup(route.stop)

    def runner(self, request, **kwargs):
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, body))
        if request.full_url.endswith('/api/settings/automation/disarm'):
            return Response(dict(ok=True, state=dict(disarmed=self.owner_stopped,
                records=[dict(scope='*', forbidden=self.owner_stopped)]),
                nodes=[dict(hostId=h, status='confirmed') for h in ('server-100','server-44','server-88')]))
        if '/api/coordination/context?' in request.full_url:
            return Response(dict(humanStopHeld=self.stopped))
        if '/details?' in request.full_url:
            if self.missing:
                raise urllib.error.HTTPError(request.full_url, 404, 'missing', {}, None)
            return Response(dict(session={**dict(id='original-session', harness=self.event['source_check_continuation']['harness'],
                cwd=self.tmp.name, model='native-existing-model'), **self.session_override}))
        if request.full_url.endswith('/resume'):
            if isinstance(self.resume_result, BaseException): raise self.resume_result
            return Response({**self.resume_result, 'inputId': body['inputId']})
        if request.full_url.endswith('/api/automation/project-launch'):
            return Response(dict(outcome='accepted', session_id='replacement-session', selected_harness='opencode',
                created=True, model=helper.HEALTH_LAUNCH_DEFAULT, cwd=self.tmp.name))
        raise AssertionError('Unexpected network route: '+request.full_url)

    def run_helper(self):
        callback = mock.Mock()
        result = helper.run_profile('health-diagnosis', self.event, Path(self.tmp.name)/'unused',
                                    runner=self.runner, profile_override=self.profile, session_callback=callback)
        return result, callback

    def resume_posts(self):
        return [call for call in self.calls if call[0]=='POST' and call[1].endswith('/resume')]

    def assert_no_creation(self):
        self.assertFalse(any('/project-launch' in url or '/new-or-resume' in url for _,url,_ in self.calls))

    def test_disabled_or_expired_sticky_cannot_replace_exact_native_target(self):
        for policy in ({'enabled':False,'ttlSeconds':0}, {'enabled':True,'ttlSeconds':1}):
            with self.subTest(policy=policy):
                self.calls.clear()
                self.event['health']['incident_session_policy'] = policy
                result, callback = self.run_helper()
                self.assertEqual('original-session', result['session_id'])
                self.assert_no_creation()
                posts = self.resume_posts()
                self.assertEqual(1, len(posts))
                self.assertTrue(posts[0][1].endswith('/opencode/original-session/resume'))
                self.assertIs(posts[0][2]['humanRequested'], False)
                self.assertTrue(posts[0][2]['inputId'])
                self.assertFalse(result['created'])
                self.assertEqual('original-session', callback.call_args.args[0]['session_id'])
                self.assertEqual('original-session', self.arm_session_autopilot.call_args.args[1])
                self.assertEqual({'incidentId':'inc_fixture','attemptId':'continuation-delivery-attempt'},
                                 self.arm_session_autopilot.call_args.kwargs['notice_attempt'])
                self.assertIsNone(self.arm_session_autopilot.call_args.kwargs['notice_incident'])

    def test_nonce_rotation_keeps_input_id_and_new_attempt_changes_it(self):
        self.run_helper()
        first = self.resume_posts()[0][2]['inputId']
        self.event['source_check_continuation'].update(request_id='new-check',generation=3)
        self.run_helper()
        self.assertEqual(first, self.resume_posts()[-1][2]['inputId'])
        self.event['source_check_continuation']['attempt_id']='next-finished-attempt'
        self.run_helper()
        self.assertNotEqual(first, self.resume_posts()[-1][2]['inputId'])
        self.assert_no_creation()

    def test_current_route_cannot_replace_historical_harness(self):
        self.event['source_check_continuation']['harness']='codex'
        result, _ = self.run_helper()
        self.assertEqual('codex', result['harness'])
        self.assertTrue(self.resume_posts()[0][1].endswith('/codex/original-session/resume'))
        self.assert_no_creation()

    def test_missing_exact_session_never_falls_back(self):
        self.missing=True
        with self.assertRaises(RuntimeError): self.run_helper()
        self.assertEqual([], self.resume_posts())
        self.assert_no_creation()
        self.arm_session_autopilot.assert_not_called()

    def test_stopped_exact_session_never_resumes_or_launches(self):
        self.stopped=True
        with self.assertRaises(RuntimeError): self.run_helper()
        self.assertEqual([], self.resume_posts())
        self.assert_no_creation()
        self.arm_session_autopilot.assert_not_called()

    def test_owner_stop_is_not_released(self):
        self.owner_stopped=True
        result, callback = self.run_helper()
        self.assertEqual('source_blocked', result['status'])
        self.assertFalse(any(method=='POST' for method,_,_ in self.calls))
        callback.assert_not_called()

    def test_unknown_resume_is_not_retried_or_turned_into_a_new_session(self):
        for outcome in (TimeoutError('receipt lost'), {}, dict(ok=False,admissionUnknown=True,nonRetryable=True),
                        urllib.error.HTTPError('http://127.0.0.1/resume', 400, 'scoped automatic resume refused', {}, None)):
            with self.subTest(outcome=outcome):
                self.calls.clear()
                self.resume_result=outcome
                with self.assertRaises(RuntimeError): self.run_helper()
                self.assertEqual(1,len(self.resume_posts()))
                self.assert_no_creation()
        self.arm_session_autopilot.assert_not_called()
        self.run_health_diagnosis.assert_not_called()

    def test_wrong_session_readback_and_autopilot_failure_never_replace_or_stop_target(self):
        self.session_override = {'id': 'foreign-session'}
        with self.assertRaises(RuntimeError): self.run_helper()
        self.assertEqual([], self.resume_posts())
        self.session_override = {}
        self.arm_session_autopilot.return_value = False
        result, callback = self.run_helper()
        self.assertEqual('source_blocked', result['status'])
        self.assertFalse(result['created'])
        self.assert_no_creation()
        self.assertFalse(any(url.endswith(('/stop', '/terminate')) for _,url,_ in self.calls))
        callback.assert_not_called()

    def test_old_pending_deadline_through_maintenance_and_adapter_keeps_native_attempt(self):
        from notification_center.core import NotificationCenter
        from notification_center.gptadmin_agent import _agent_job_event
        tokens = {'p': dict(project='qa', max_severity='notice', verification_sources=['probe'], principal_id='source'),
                  'a': dict(project='qa', max_severity='notice', incident_role='executor', principal_id='executor')}
        db = Path(self.tmp.name)/'old-pending.db'
        center = NotificationCenter(db, tokens)
        inc = center.create_event('p', 'event', dict(schema='notify.event.v1', kind='incident', project='qa',
            recipient='health', severity='notice', title='fixture', dedup_key='probe', event_type='health.degraded',
            source_id='probe', host_id='qa', signal_type='cpu', correlation_id='episode'))['incident_id']
        finished = center.record_work_finished('a', inc, 'finish',
            dict(attempt_id='finished-attempt', session_id='original-session', harness='opencode'))
        center._connection.close()
        # A persisted pending request after restart uses the existing worker clock.
        center = NotificationCenter(db, tokens)
        self.addCleanup(center._connection.close)
        with mock.patch('notification_center.incident_lifecycle.time.time', return_value=finished['verification_deadline']+1):
            center.run_history_retention_tick()
        row = center._connection.execute("SELECT * FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'").fetchone()
        self.assertIsNotNone(row)
        incident = center.get_incident(inc)
        self.assertEqual('unknown', incident['lifecycle']['verification_state'])
        self.assertIsNone(incident['lifecycle'].get('last_check_receipt'))
        self.event = _agent_job_event('health-diagnosis',
            dict(incident=incident, target=json.loads(row['target_json'])), row['id'])
        result, _ = self.run_helper()
        self.assertEqual('original-session', result['session_id'])
        self.assert_no_creation()
        self.assertEqual(self.event['attempt_id'], self.arm_session_autopilot.call_args.kwargs['notice_attempt']['attemptId'])
        with center._connection:
            center._connection.execute("UPDATE deliveries SET status='uncertain',attempt=1 WHERE id=?", (row['id'],))
        center._history_retention_due = 0
        with mock.patch('notification_center.incident_lifecycle.time.time', return_value=finished['verification_deadline']+1000):
            center.run_history_retention_tick()
        self.assertEqual(['uncertain'], [r[0] for r in center._connection.execute(
            "SELECT status FROM deliveries WHERE json_type(target_json,'$.source_check_continuation')='object'")])
