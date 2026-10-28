"""Durable attempt completion is separate from independent service health."""
import tempfile
import unittest
from pathlib import Path
from notification_center.core import NotificationCenter, AuthorizationError, ValidationError

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name)/'state.db', {
            'source': {'project':'test','max_severity':'emergency','principal_id':'watchdog','verification_sources':['probe']},
            'agent': {'project':'test','max_severity':'emergency','principal_id':'executor','incident_role':'executor'},
        })
        self.event = dict(schema='notify.event.v1',kind='incident',severity='notice',project='test',recipient='health',title='Unavailable',body='Probe failed',dedup_key='probe',event_type='health.degraded',source_id='probe',host_id='host',signal_type='http',correlation_id='episode',observed_at=100)
        self.inc = self.center.create_event('source','intake',self.event)['incident_id']

    def finish(self, attempt='one'):
        return self.center.record_work_finished('agent',self.inc,'finish:'+attempt,dict(attempt_id=attempt,session_id='session',harness='codex',evidence_refs=['change']))

    def shared_episode(self, suffix, **changes):
        event = {**self.event, 'dedup_key': suffix, 'correlation_id': suffix, **changes}
        incident = self.center.create_event('source', 'intake:'+suffix, event)['incident_id']
        self.center.record_health_agent_session(incident, 'native:'+suffix, '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        return incident

    def test_shared_finish_checks_each_bound_episode_without_group_resolution(self):
        self.center.record_health_agent_session(self.inc, 'native:first', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        latest = self.shared_episode('latest')
        unrelated = self.shared_episode('cpu', signal_type='cpu')
        otherhost = self.shared_episode('otherhost', host_id='other')
        historical = self.center.create_event('source', 'unbound',
            {**self.event, 'dedup_key':'unbound', 'correlation_id':'unbound'})['incident_id']
        consumer = self.consumer()
        otherconsumer = self.center.create_event(consumer['intake_token'], 'other-consumer',
            {**self.event, 'dedup_key':'other-consumer', 'correlation_id':'other-consumer'})['incident_id']
        self.center.record_health_agent_session(otherconsumer, 'native:consumer', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        result = self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex'))
        due = self.center.due_source_checks('source', 'test', 'probe')
        self.assertEqual({self.inc, latest}, {row['incident_id'] for row in due})
        latest_request = next(row for row in due if row['incident_id'] == latest)
        self.center.record_source_check('source', latest, 'healthy-latest',
            dict(request_id=latest_request['request_id'], generation=latest_request['generation'],
                 observed_state='healthy', evidence_refs=['fresh latest predicate']))
        self.assertEqual('open', self.center.get_incident(self.inc)['state'])
        old_request = next(row for row in due if row['incident_id'] == self.inc)
        self.check(old_request)
        self.assertEqual('resolved', self.center.get_incident(self.inc)['state'])
        for incident in (unrelated, otherhost, historical, otherconsumer):
            self.assertIsNone(self.center.get_incident(incident)['lifecycle']['verification_due'])
        self.assertEqual(result, self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex')))

    def test_shared_degraded_episodes_queue_one_continuation_for_group_attempt(self):
        self.center.record_health_agent_session(self.inc, 'native:first', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        latest = self.shared_episode('latest')
        self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex'))
        due = self.center.due_source_checks('source', 'test', 'probe')
        self.assertEqual(2, len(due))
        results = []
        for row in due:
            results.append(self.center.record_source_check('source', row['incident_id'], 'degraded:'+row['incident_id'],
                dict(request_id=row['request_id'], generation=row['generation'],
                     observed_state='degraded', evidence_refs=['fresh measurement'])))
        self.assertEqual(results[0]['continuation_delivery_id'], results[1]['continuation_delivery_id'])
        self.assertEqual(1, self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()[0])

    def test_shared_finish_preserves_each_source_principal_and_native_binding(self):
        self.center._tokens['other-source'] = {**self.center._tokens['source'], 'principal_id':'other-watchdog'}
        sibling = self.center.create_event('other-source', 'other-principal',
            {**self.event, 'dedup_key':'sibling', 'correlation_id':'sibling'})['incident_id']
        self.center.record_health_agent_session(sibling, 'native:other', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        other_session = self.shared_episode('other-session')
        self.center.record_health_agent_session(other_session, 'native:separate', '', 'codex', 'separate',
            'https://agent.example.test', stage='health-diagnosis')
        latest = self.shared_episode('latest')
        self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex'))
        self.assertEqual([latest], [r['incident_id'] for r in self.center.due_source_checks('source','test','probe')])
        requests = self.center.due_source_checks('other-source','test','probe')
        self.assertEqual([sibling], [r['incident_id'] for r in requests])
        row = requests[0]
        body = dict(request_id=row['request_id'], generation=row['generation'], observed_state='healthy', evidence_refs=['independent other source'])
        with self.assertRaises(AuthorizationError):
            self.center.record_source_check('source', sibling, 'forged-source', body)
        self.center.record_source_check('other-source', sibling, 'own-source', body)
        self.assertEqual('resolved', self.center.get_incident(sibling)['state'])
        self.assertIsNone(self.center.get_incident(other_session)['lifecycle']['verification_due'])

    def test_older_degraded_latest_healthy_retains_one_durable_group_authority(self):
        self.center.record_health_agent_session(self.inc, 'native:first', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        latest = self.shared_episode('latest')
        self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex'))
        due = {r['incident_id']:r for r in self.center.due_source_checks('source','test','probe')}
        first = self.check(due[self.inc], 'degraded')
        row = due[latest]
        self.center.record_source_check('source', latest, 'latest-healthy',
            dict(request_id=row['request_id'], generation=row['generation'], observed_state='healthy', evidence_refs=['latest healthy']))
        self.assertEqual('resolved', self.center.get_incident(latest)['state'])
        self.assertEqual(self.inc, first['continuation']['incident_id'])
        self.assertEqual(first['continuation'], self.center.get_incident(latest)['lifecycle']['continuation'])
        reopened = NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        reopened._connection.execute('UPDATE incident_lifecycle SET verification_due=0 WHERE incident_id=?',(self.inc,))
        reopened._connection.commit()
        fresh = reopened.due_source_checks('source','test','probe')[0]
        second = reopened.record_source_check('source', self.inc, 'old-still-degraded',
            dict(request_id=fresh['request_id'], generation=fresh['generation'], observed_state='degraded', evidence_refs=['old still bad']))
        self.assertEqual(first['continuation_delivery_id'], second['continuation_delivery_id'])
        self.assertEqual(first['continuation'], second['continuation'])
        current = reopened.get_incident(self.inc)['lifecycle']
        reopened.record_source_check('source', self.inc, 'old-final-healthy',
            dict(request_id=current['request_id'], generation=current['generation'], observed_state='healthy', evidence_refs=['all now healthy']))
        self.assertNotIn('continuation', reopened.get_incident(latest)['lifecycle'])

    def test_new_shared_attempt_invalidates_old_group_continuation(self):
        self.center.record_health_agent_session(self.inc, 'native:first', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        latest = self.shared_episode('latest')
        self.center.record_work_finished('agent', latest, 'latest-finish',
            dict(attempt_id='latest-delivery', session_id='session', harness='codex'))
        old = next(r for r in self.center.due_source_checks('source','test','probe') if r['incident_id'] == self.inc)
        self.check(old, 'degraded')
        self.assertIn('continuation', self.center.get_incident(latest)['lifecycle'])
        self.center.record_work_finished('agent', latest, 'second-finish',
            dict(attempt_id='second-delivery', session_id='session', harness='codex'))
        for incident in (self.inc, latest):
            life = self.center.get_incident(incident)['lifecycle']
            self.assertEqual('second-delivery', life['attempt_id'])
            self.assertNotIn('continuation', life)

    def test_shared_attempt_payload_conflict_rolls_back_new_episode_completion(self):
        from notification_center.core import IdempotencyConflict
        self.center.record_health_agent_session(self.inc, 'native:first', '', 'codex', 'session',
            'https://agent.example.test', stage='health-diagnosis')
        self.finish('shared-attempt')
        latest = self.shared_episode('latest')
        with self.assertRaises(IdempotencyConflict):
            self.center.record_work_finished('agent', latest, 'conflicting-finish',
                dict(attempt_id='shared-attempt', session_id='session', harness='codex', evidence_refs=['different']))
        self.assertIsNone(self.center.get_incident(latest)['lifecycle']['verification_due'])

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
        req=self.next_round()
        degraded=self.check(req,'degraded')
        self.assertEqual('session',degraded['continuation']['session_id'])
        self.assertEqual(req['generation'],degraded['continuation']['generation'])
        self.assertEqual(degraded['continuation'],self.center.get_incident(self.inc)['lifecycle']['continuation'])
        delivery=self.center._connection.execute('SELECT * FROM deliveries WHERE id=?',(degraded['continuation_delivery_id'],)).fetchone()
        self.assertTrue(delivery['delivery_key'].endswith('source-check:'+req['attempt_id']))
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
        status,result=request('POST',path+'/health/check-result','source',{**body,'observed_state':'degraded'},key='first-degraded')
        self.assertEqual(200,status,result)
        self.assertEqual('open',self.center.get_incident(self.inc)['state'])
        self.assertEqual([],request('GET','/v1/verification-requests?project=test&source_id=probe','source')[1]['requests'])
        self.center._connection.execute('UPDATE incident_lifecycle SET verification_due=0 WHERE incident_id=?',(self.inc,))
        self.center._connection.commit()
        second=request('GET','/v1/verification-requests?project=test&source_id=probe','source')[1]['requests'][0]
        self.assertNotEqual(body['request_id'],second['request_id'])
        status,result=request('POST',path+'/health/check-result','source',{**body,'request_id':second['request_id']},key='fresh-check')
        self.assertEqual(200,status,result)
        self.assertEqual('resolved',self.center.get_incident(self.inc)['state'])
        self.assertEqual(1,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()[0])

    def test_historical_enrollment_has_groups_but_no_fabricated_health(self):
        self.center._connection.execute('DELETE FROM incident_lifecycle')
        self.center._connection.commit()
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        life=reopened.get_incident(self.inc)['lifecycle']
        self.assertEqual('http',life['group']['problem_type'])
        self.assertEqual('test+probe',life['group']['consumer_id'])
        self.assertIsNone(life['verification_state'])
        finish=reopened.record_work_finished('agent',self.inc,'historical-finish',dict(attempt_id='legacy',session_id='session',harness='codex'))
        self.assertEqual('open',reopened.get_incident(self.inc)['state'])
        reopened.record_source_check('source',self.inc,'historical-check',dict(request_id=finish['request_id'],generation=finish['generation'],observed_state='healthy',evidence_refs=['probe']))
        self.assertEqual('resolved',reopened.get_incident(self.inc)['state'])

    def test_incomplete_health_is_not_typed_and_claimed_consumer_is_ignored(self):
        event={**self.event,'consumer_id':'claimed-other','dedup_key':'other','correlation_id':'other'}
        created=self.center.create_event('source','claimed-consumer',event)
        self.assertEqual('test+probe',self.center.get_incident(created['incident_id'])['lifecycle']['group']['consumer_id'])
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

    def next_round(self):
        self.center._connection.execute('UPDATE incident_lifecycle SET verification_due=0 WHERE incident_id=?',(self.inc,))
        self.center._connection.commit()
        checks=self.center.due_source_checks('source','test','probe')
        self.assertEqual(1,len(checks),'independent source must keep checking an unresolved incident')
        return checks[0]

    def test_degraded_then_held_executor_still_allows_next_healthy_check(self):
        first=self.finish()
        self.check(first,'degraded')
        # No native resume or launch happens here: the executor may remain STOPped.
        next_check=self.next_round()
        self.assertNotEqual(first['request_id'],next_check['request_id'])
        self.check(next_check,'healthy')
        self.assertEqual('resolved',self.center.get_incident(self.inc)['state'])

    def test_ordinary_degraded_preserves_one_continuation_and_fences_old_nonce(self):
        first=self.finish()
        degraded=self.check(first,'degraded')
        old=self.next_round()
        self.center.create_event('source','ordinary-new',{**self.event,'observed_at':300})
        life=self.center.get_incident(self.inc)['lifecycle']
        self.assertEqual(degraded['continuation'],life['continuation'])
        with self.assertRaises(ValidationError): self.check(old,'healthy')
        fresh=self.next_round()
        self.assertNotEqual(old['request_id'],fresh['request_id'])
        self.check(fresh,'degraded')
        rows=self.center._connection.execute("SELECT * FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchall()
        self.assertEqual(1,len(rows))

    def test_repeated_degraded_rounds_schedule_once_per_finished_attempt(self):
        first=self.finish()
        result=self.check(first,'degraded')
        second=self.next_round()
        second_result=self.check(second,'degraded')
        self.assertEqual(result['continuation'],second_result['continuation'])
        self.assertEqual(result['continuation_delivery_id'],second_result['continuation_delivery_id'])
        self.assertEqual(1,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()[0])

    def test_unknown_reply_receipt_is_readable_after_nonce_rotation(self):
        first=self.finish()
        result=self.check(first,'unknown')
        life=self.center.get_incident(self.inc)['lifecycle']
        self.assertNotEqual(first['request_id'],life['request_id'])
        receipt=life['last_check_receipt']
        self.assertEqual('check:'+first['request_id']+':unknown',receipt['idempotency_key'])
        self.assertEqual(first['request_id'],receipt['request_id'])
        self.assertEqual(result,receipt['result'])
        self.assertNotIn('continuation',life)

    def test_new_attempt_clears_continuation_and_duplicate_finish_does_not_rearm(self):
        first=self.finish()
        self.check(first,'degraded')
        current=self.center.get_incident(self.inc)['lifecycle']['request_id']
        self.assertEqual(first,self.finish())
        self.assertEqual(current,self.center.get_incident(self.inc)['lifecycle']['request_id'])
        second=self.finish('two')
        self.assertNotIn('continuation',self.center.get_incident(self.inc)['lifecycle'])
        self.check(second,'degraded')
        self.assertEqual(2,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()[0])

    def test_additive_upgrade_rearms_old_terminal_degraded_without_new_delivery(self):
        first=self.finish()
        result=self.check(first,'degraded')
        self.center._connection.execute("UPDATE incident_lifecycle SET request_id=?,verification_due=NULL WHERE incident_id=?",(first['request_id'],self.inc))
        self.center._connection.execute('ALTER TABLE incident_lifecycle DROP COLUMN continuation_json')
        self.center._connection.execute('ALTER TABLE incident_lifecycle DROP COLUMN last_check_receipt_json')
        self.center._connection.commit()
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        life=reopened.get_incident(self.inc)['lifecycle']
        self.assertIsNotNone(life['verification_due'])
        self.assertNotEqual(first['request_id'],life['request_id'])
        self.assertEqual(result['continuation'],life['continuation'])
        self.assertEqual(first['request_id'],life['last_check_receipt']['request_id'])
        self.assertEqual(1,reopened._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()[0])

    def consumer(self,project='test'):
        return self.center.create_consumer(project=project,name='Verifier fixture',policy=[dict(id='root',platform='telegram',action='message',target={'chat_id':-100},retry_interval_seconds=60,max_repeats=1)],max_severity='notice')

    def test_registered_consumer_verifier_grant_preserves_db_scope_and_revocation(self):
        consumer=self.consumer()
        self.center._tokens['grant-owner']=dict(project='test',max_severity='emergency',incident_role='verifier',principal_id='source-owner',verification_owned_sources=True,verification_consumer_ids=[consumer['id']])
        token=consumer['intake_token']
        scope=self.center._token_scope(token)
        self.assertEqual('verifier',scope.get('incident_role'))
        self.assertEqual('consumer:'+consumer['id'],scope['principal_id'])
        self.assertEqual('notice',scope['max_severity'])
        self.assertEqual(consumer['id'],scope['consumer_id'])
        event={**self.event,'dedup_key':'consumer-signal','source_id':'consumer:probe','correlation_id':'consumer-episode'}
        incident=self.center.create_event(token,'consumer-open',event)['incident_id']
        first=self.center.record_work_finished('agent',incident,'consumer-finish',dict(attempt_id='one',session_id='consumer-session',harness='codex'))
        self.assertEqual(1,len(self.center.due_source_checks(token,'test','consumer:probe')))
        self.center.record_source_check(token,incident,'consumer-healthy',dict(request_id=first['request_id'],generation=first['generation'],observed_state='healthy',evidence_refs=['probe']))
        self.center._connection.execute('UPDATE consumers SET token_hash=? WHERE id=?',('revoked',consumer['id']))
        self.center._connection.commit()
        with self.assertRaises(AuthorizationError): self.center._token_scope(token)

    def test_foreign_or_executor_or_ambiguous_consumer_grants_are_ignored(self):
        consumer=self.consumer()
        token=consumer['intake_token']
        base=dict(project='test',incident_role='verifier',verification_owned_sources=True,verification_consumer_ids=[consumer['id']])
        for grant in (dict(base,project='foreign'),dict(base,incident_role='executor'),dict(base,verification_consumer_ids=['other'])):
            self.center._tokens['grant-owner']=grant
            self.assertNotIn('incident_role',self.center._token_scope(token))
        self.center._tokens['grant-owner']=base
        self.center._tokens['grant-duplicate']=base
        self.assertNotIn('incident_role',self.center._token_scope(token))

    def test_supplemental_consumer_grant_needs_no_project_legacy_token(self):
        consumer=self.consumer('autotelegramviews')
        grants=[dict(consumer_id=consumer['id'],project='autotelegramviews',incident_role='verifier',verification_owned_sources=True,verification_sources=[])]
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',{'unrelated':dict(project='atv-views',max_severity='emergency'),'executor':dict(project='*',max_severity='emergency',incident_role='executor',principal_id='worker')},consumer_verifier_grants=grants)
        self.addCleanup(reopened._connection.close)
        token=consumer['intake_token']
        scope=reopened._token_scope(token)
        self.assertEqual('verifier',scope['incident_role'])
        self.assertEqual('consumer:'+consumer['id'],scope['principal_id'])
        self.assertEqual('autotelegramviews',scope['project'])
        self.assertEqual('notice',scope['max_severity'])
        event={**self.event,'project':'autotelegramviews','dedup_key':'supplemental','source_id':'atv:probe','correlation_id':'supplemental'}
        incident=reopened.create_event(token,'supplemental-open',event)['incident_id']
        self.assertEqual(scope['principal_id'],reopened._connection.execute('SELECT source_principal FROM incident_lifecycle WHERE incident_id=?',(incident,)).fetchone()[0])
        finished=reopened.record_work_finished('executor',incident,'supplemental-finish',dict(attempt_id='one',session_id='consumer-session',harness='codex'))
        self.assertEqual(1,len(reopened.due_source_checks(token,'autotelegramviews','atv:probe')))
        reopened.record_source_check(token,incident,'supplemental-healthy',dict(request_id=finished['request_id'],generation=finished['generation'],observed_state='healthy',evidence_refs=['probe']))
        self.assertEqual('resolved',reopened.get_incident(incident)['state'])
        with self.assertRaises(AuthorizationError): reopened.create_event(token,'consumer-escalation',{**event,'severity':'important'})
        reopened._connection.execute('UPDATE consumers SET token_hash=? WHERE id=?',('revoked',consumer['id']))
        reopened._connection.commit()
        with self.assertRaises(AuthorizationError): reopened._token_scope(token)

    def test_supplemental_invalid_policy_fails_closed_before_opening_db(self):
        from unittest.mock import patch
        base=dict(consumer_id='consumer',project='test',incident_role='verifier')
        for policy in ({}, [dict(base,principal_id='forged')], [dict(base,verification_owned_sources='true')], [dict(base,verification_sources='source')], [dict(base,consumer_id='')], [dict(base,incident_role=['verifier'])], [base]*1025):
            with self.subTest(policy_type=type(policy).__name__),self.assertRaises(ValidationError):
                NotificationCenter(Path(self.tmp.name)/'invalid.db',{},consumer_verifier_grants=policy)
        with patch.dict('os.environ',{'NOTICEPLACE_CONSUMER_VERIFIER_GRANTS_JSON':'invalid-json'}),self.assertRaises(ValidationError):
            NotificationCenter(Path(self.tmp.name)/'invalid.db',{})
        self.assertFalse((Path(self.tmp.name)/'invalid.db').exists())

    def test_supplemental_env_policy_and_combined_grants_are_unambiguous(self):
        import json
        from unittest.mock import patch
        consumer=self.consumer()
        base=dict(consumer_id=consumer['id'],project='test',incident_role='verifier',verification_owned_sources=True)
        token=consumer['intake_token']
        with patch.dict('os.environ',{'NOTICEPLACE_CONSUMER_VERIFIER_GRANTS_JSON':json.dumps([base])}):
            reopened=NotificationCenter(Path(self.tmp.name)/'state.db',{})
        self.addCleanup(reopened._connection.close)
        self.assertEqual('verifier',reopened._token_scope(token)['incident_role'])
        legacy=dict(project='test',incident_role='verifier',verification_consumer_ids=[consumer['id']],verification_owned_sources=True)
        reopened._tokens['legacy']=legacy
        self.assertNotIn('incident_role',reopened._token_scope(token))
        for policy in ([dict(base,project='foreign')],[dict(base,incident_role='executor')],[dict(base,consumer_id='unregistered')],[base,base]):
            other=NotificationCenter(Path(self.tmp.name)/'state.db',{},consumer_verifier_grants=policy)
            self.addCleanup(other._connection.close)
            self.assertNotIn('incident_role',other._token_scope(token))
        # Event metadata cannot grant a consumer source authority.
        ungranted=NotificationCenter(Path(self.tmp.name)/'state.db',{},consumer_verifier_grants=[])
        self.addCleanup(ungranted._connection.close)
        incident=ungranted.create_event(token,'body-role',{**self.event,'dedup_key':'body-role','incident_role':'verifier','verification_owned_sources':True,'principal_id':'forged'})['incident_id']
        with self.assertRaises(AuthorizationError): ungranted.due_source_checks(token,'test','probe')
        self.assertNotIn('incident_role',ungranted._token_scope(token))

    def test_builtin_delivery_profiles_do_not_split_a_legacy_producer_group(self):
        self.center._tokens['source']['automatic_health_diagnosis']=False
        groups=[]
        for index,severity in enumerate(('important','critical')):
            incident=self.center.create_event('source','severity:'+severity,{**self.event,'severity':severity,'dedup_key':'severity:'+severity,'correlation_id':'severity:'+severity,'signal_type':'load','consumer_id':'forged-producer'})['incident_id']
            groups.append(self.center.get_incident(incident)['lifecycle']['group_key'])
        self.assertEqual(groups[0],groups[1])
        self.assertEqual('test+probe',self.center.get_incident(incident)['lifecycle']['group']['consumer_id'])

    def test_real_consumers_keep_distinct_groups_for_same_source_host_type(self):
        keys=[]
        for index in range(2):
            consumer=self.consumer()
            incident=self.center.create_event(consumer['intake_token'],'real:'+str(index),{**self.event,'dedup_key':'real:'+str(index),'consumer_id':'forged-common'})['incident_id']
            group=self.center.get_incident(incident)['lifecycle']
            self.assertEqual(consumer['id'],group['group']['consumer_id'])
            keys.append(group['group_key'])
        self.assertNotEqual(keys[0],keys[1])

    def test_existing_builtin_group_projection_upgrade_preserves_source_and_health(self):
        import json
        before=dict(self.center._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(self.inc,)).fetchone())
        group=json.loads(before['metadata_json']);group['consumer_id']='profile_log'
        self.center._connection.execute('UPDATE incident_lifecycle SET metadata_json=?,group_key=? WHERE incident_id=?',(json.dumps(group),'old-route-key',self.inc))
        self.center._connection.commit()
        reopened=NotificationCenter(Path(self.tmp.name)/'state.db',self.center._tokens)
        self.addCleanup(reopened._connection.close)
        after=dict(reopened._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(self.inc,)).fetchone())
        self.assertEqual('test+probe',json.loads(after['metadata_json'])['consumer_id'])
        self.assertEqual(before['group_key'],after['group_key'])
        for key in before:
            if key not in ('metadata_json','group_key'):
                self.assertEqual(before[key],after[key],key)
        self.assertEqual('open',reopened.get_incident(self.inc)['state'])
