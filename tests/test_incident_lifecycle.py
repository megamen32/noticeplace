"""Durable attempt completion is separate from independent service health."""
import tempfile
import unittest
from pathlib import Path
from notification_center.core import NotificationCenter, AuthorizationError, ValidationError

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name)/'state.db', {
            'source': {'project':'test','max_severity':'emergency','principal_id':'watchdog','verification_sources':['probe']},
            'agent': {'project':'test','max_severity':'emergency','principal_id':'executor','incident_role':'executor'},
        })
        self.event = dict(schema='notify.event.v1',kind='incident',severity='notice',project='test',recipient='health',title='Unavailable',body='Probe failed',dedup_key='probe',event_type='health.degraded',source_id='probe',host_id='host',signal_type='http',correlation_id='episode',observed_at=100)
        self.inc = self.center.create_event('source','intake',self.event)['incident_id']

    def finish(self, attempt='one'):
        return self.center.record_work_finished('agent',self.inc,'finish:'+attempt,dict(attempt_id=attempt,session_id='session',harness='codex',evidence_refs=['change']))

    def check(self, request, state='healthy', token='source'):
        return self.center.record_source_check(token,self.inc,'check:'+request['request_id']+':'+state,dict(request_id=request['request_id'],generation=request['generation'],observed_state=state,evidence_refs=['probe']))

    def test_finish_durable_idempotent_and_not_resolved(self):
        first=self.finish()
        self.assertEqual(first,self.finish())
        self.assertEqual('open',self.center.get_incident(self.inc)['state'])
        self.assertEqual('due',self.center.get_incident(self.inc)['lifecycle']['verification_state'])
        self.assertEqual(first['request_id'],self.center.due_source_checks('source','test','probe')[0]['request_id'])

    def test_executor_cannot_impersonate_source(self):
        req=self.finish()
        with self.assertRaises(AuthorizationError): self.check(req,token='agent')

    def test_unknown_has_no_continuation_and_degraded_same_session(self):
        req=self.finish()
        before=self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel LIKE 'gptadmin.agent:%'").fetchone()[0]
        unknown=self.check(req,'unknown')
        self.assertEqual(before,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel LIKE 'gptadmin.agent:%'").fetchone()[0])
        self.assertNotIn('continuation',unknown)
        self.assertEqual('open',self.center.get_incident(self.inc)['state'])
        degraded=self.check(req,'degraded')
        self.assertEqual('session',degraded['continuation']['session_id'])
        self.assertEqual(req['generation'],degraded['continuation']['generation'])
        self.assertEqual(degraded['continuation'],self.center.get_incident(self.inc)['lifecycle']['continuation'])
        delivery=self.center._connection.execute('SELECT * FROM deliveries WHERE id=?',(degraded['continuation_delivery_id'],)).fetchone()
        self.assertTrue(delivery['delivery_key'].endswith('source-check:'+req['request_id']))
        import json
        self.assertEqual(degraded['continuation'],json.loads(delivery['target_json'])['source_check_continuation'])
        self.assertEqual(degraded,self.check(req,'degraded'))
        self.assertEqual(before+1,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel LIKE 'gptadmin.agent:%'").fetchone()[0])
        self.assertEqual('open',self.center.get_incident(self.inc)['state'])

    def test_stale_request_after_new_degraded_cannot_close(self):
        req=self.finish()
        self.center.create_event('source','new-degraded',{**self.event,'observed_at':300})
        with self.assertRaises(ValidationError): self.check(req)
        self.assertEqual('open',self.center.get_incident(self.inc)['state'])

    def test_fresh_source_closes_and_duplicate_is_safe(self):
        req=self.finish()
        result=self.check(req)
        self.assertEqual('resolved',self.center.get_incident(self.inc)['state'])
        self.assertEqual(result,self.check(req))

    def test_delayed_legacy_recovery_rejected(self):
        self.center.create_event('source','new-degraded',{**self.event,'observed_at':300})
        recovery={**self.event,'action':'resolve','event_type':'health.recovered','observed_at':200}
        with self.assertRaises(ValidationError): self.center.resolve_event('source','recovery',recovery)

    def test_legacy_unambiguous_periodic_recovery_remains_supported(self):
        recovery={**self.event,'action':'resolve','event_type':'health.recovered','observed_at':200}
        self.assertTrue(self.center.resolve_event('source','recovery',recovery)['resolved'])

    def test_ambiguous_legacy_without_clock_is_unverified(self):
        event={k:v for k,v in self.event.items() if k!='observed_at'}
        self.center.create_event('source','repeat-no-clock',event)
        with self.assertRaises(ValidationError):
            self.center.resolve_event('source','ambiguous',{**event,'action':'resolve','event_type':'health.recovered'})

    def test_explicit_problem_type_is_not_aliased(self):
        from notification_center.incident_lifecycle import canonical_problem_type
        self.assertEqual('cpu',canonical_problem_type({'signal_type':'load'}))
        self.assertEqual('disk',canonical_problem_type({'signal_type':'inodes'}))
        self.assertEqual('load',canonical_problem_type({'signal_type':'cpu','problem_type':'load'}))

    def test_executor_cannot_close_via_legacy_recovery(self):
        with self.assertRaises(AuthorizationError):
            self.center.resolve_event('agent','agent-resolve',{**self.event,'action':'resolve','event_type':'health.recovered','observed_at':200,'actor':'watchdog'})

    def test_durable_reopen_and_finish_key_conflict(self):
        from notification_center.core import IdempotencyConflict
        req=self.finish()
        with self.assertRaises(IdempotencyConflict):
            self.center.record_work_finished('agent',self.inc,'finish:one',dict(attempt_id='two',session_id='session',harness='codex'))
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        self.assertEqual(req['request_id'],reopened.get_incident(self.inc)['lifecycle']['request_id'])

    def test_http_finish_read_check_and_actor_cannot_bypass_auth(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from notification_center.http_api import build_handler
        server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(self.center,'probe-secret','mcp-test-secret'))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        def request(method,path,token,body=None,key='key'):
            req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':key})
            try:
                with urllib.request.urlopen(req,timeout=5) as res:return res.status,json.load(res)
            except urllib.error.HTTPError as res:return res.code,json.load(res)
        path='/v1/incidents/'+self.inc
        status,finished=request('POST',path+'/health/work-finished','agent',dict(attempt_id='one',session_id='session',harness='codex'))
        self.assertEqual(200,status,finished)
        self.assertEqual(200,request('GET',path,'agent')[0])
        self.assertEqual(401,request('GET','/v1/verification-requests?project=test&source_id=probe','agent')[0])
        self.assertEqual(finished['request_id'],request('GET','/v1/verification-requests?project=test&source_id=probe','source')[1]['requests'][0]['request_id'])
        body=dict(request_id=finished['request_id'],generation=finished['generation'],observed_state='healthy',actor='watchdog',evidence_refs=['probe'])
        self.assertEqual(401,request('POST',path+'/health/check-result','agent',body)[0])
        self.assertEqual(401,request('POST',path+'/health/verification','agent',body)[0])
        status,result=request('POST',path+'/health/check-result','source',body,key='fresh-check')
        self.assertEqual(200,status,result)
        self.assertEqual('resolved',self.center.get_incident(self.inc)['state'])

    def test_historical_enrollment_has_groups_but_no_fabricated_health(self):
        self.center._connection.execute('DELETE FROM incident_lifecycle')
        self.center._connection.commit()
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        life=reopened.get_incident(self.inc)['lifecycle']
        self.assertEqual('http',life['group']['problem_type'])
        self.assertEqual('profile_log',life['group']['consumer_id'])
        self.assertIsNone(life['verification_state'])
        finish=reopened.record_work_finished('agent',self.inc,'historical-finish',dict(attempt_id='legacy',session_id='session',harness='codex'))
        self.assertEqual('open',reopened.get_incident(self.inc)['state'])
        reopened.record_source_check('source',self.inc,'historical-check',dict(request_id=finish['request_id'],generation=finish['generation'],observed_state='healthy',evidence_refs=['probe']))
        self.assertEqual('resolved',reopened.get_incident(self.inc)['state'])

    def test_incomplete_health_is_not_typed_and_claimed_consumer_is_ignored(self):
        event={**self.event,'consumer_id':'claimed-other','dedup_key':'other','correlation_id':'other'}
        created=self.center.create_event('source','claimed-consumer',event)
        self.assertEqual('profile_log',self.center.get_incident(created['incident_id'])['lifecycle']['group']['consumer_id'])
        incomplete={k:v for k,v in self.event.items() if k not in ('source_id','host_id','signal_type','correlation_id')}
        incomplete['dedup_key']='untyped'
        created=self.center.create_event('source','untyped',incomplete)
        self.assertIsNone(self.center.get_incident(created['incident_id'])['lifecycle'])

    def test_mcp_work_finished_uses_unified_and_legacy_dispatch(self):
        from unittest.mock import patch
        from mcp import notify_mcp
        args=dict(incident_id=self.inc,attempt_id='one',session_id='session',harness='codex',evidence_refs=['change'])
        with patch.object(notify_mcp,'_noticeplace_api',return_value={'attempt_state':'work_finished'}) as api:
            unified=notify_mcp.tool_noticeplace({'action':'noticeplace_work_finished','params':args})
            legacy=notify_mcp.TOOLS['noticeplace_work_finished']['handler'](args)
        self.assertEqual(unified,legacy)
        self.assertEqual(2,api.call_count)
        self.assertTrue(api.call_args.args[1].endswith('/health/work-finished'))
        self.assertEqual(['noticeplace'],[tool['name'] for tool in notify_mcp.tool_specs()])

    def test_unscoped_old_source_cannot_become_executor(self):
        self.center._tokens['old-source']={'project':'test','max_severity':'emergency'}
        with self.assertRaises(AuthorizationError):
            self.center.record_work_finished('old-source',self.inc,'old-finish',dict(attempt_id='one',session_id='session',harness='codex'))

    def test_reordered_degraded_cannot_lower_recovery_freshness(self):
        self.center.create_event('source','newest',{**self.event,'observed_at':300})
        self.center.create_event('source','delayed-old',{**self.event,'observed_at':150})
        with self.assertRaises(ValidationError):
            self.center.resolve_event('source','late-recovery',{**self.event,'action':'resolve','event_type':'health.recovered','observed_at':200})
