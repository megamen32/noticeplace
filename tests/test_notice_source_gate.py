from __future__ import annotations
import unittest
import urllib.parse
from notification_center.source_gate import HealthSourceGateBlocked, source_chain, source_launch_fields


class NoticeSourceGateTests(unittest.TestCase):
    profile={'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','cwd':'/home/roomhacker/ServersAdministartion'}

    def test_all_native_sources_read_without_consuming_and_carried_exactly(self):
        sources=[{'harness':'zcode','sessionId':'sess_planner-original'}, {'harness':'codex','sessionId':'diagnosis-original'}]
        requests=[]
        def read(request, **options):
            requests.append((request,options));return {'humanStopHeld':False}
        fields=source_launch_fields(self.profile,sources,read)
        self.assertEqual(sources,fields['sourceSessions'])
        self.assertEqual('zcode',fields['sourceHarness'])
        self.assertEqual('sess_planner-original',fields['sourceSessionId'])
        self.assertNotIn('humanRequested',fields)
        self.assertEqual(2,len(requests))
        for (request,options),source in zip(requests,sources):
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            self.assertEqual([source['sessionId']],query['sessionId'])
            self.assertEqual(['0'],query['consume'])
            self.assertNotIn('touch',query)
            self.assertLessEqual(options['timeout'],5)

    def test_stopped_ancestor_blocks_even_if_immediate_source_is_not_held(self):
        sources=[{'harness':'zcode','sessionId':'planner'}, {'harness':'codex','sessionId':'stopped-diagnosis'}]
        def read(request,**kwargs):
            return {'humanStopHeld':'stopped-diagnosis' in request.full_url}
        with self.assertRaisesRegex(HealthSourceGateBlocked,'human_stop_held'):
            source_launch_fields(self.profile,sources,read)

    def test_missing_invalid_and_error_stop_authority_fail_closed(self):
        for response in ({}, {'humanStopHeld':0}, {'humanStopHeld':'false'}, {'humanStopHeld':None}):
            with self.assertRaises(HealthSourceGateBlocked):
                source_launch_fields(self.profile,[{'harness':'codex','sessionId':'native'}],lambda *_a,**_k:response)
        def read(*args,**kwargs):raise OSError('unreachable')
        with self.assertRaisesRegex(HealthSourceGateBlocked,'source_context_unavailable'):
            source_launch_fields(self.profile,[{'harness':'codex','sessionId':'native'}],read)

    def test_overflow_is_not_truncated_or_sent(self):
        with self.assertRaisesRegex(HealthSourceGateBlocked,'manual_handling'):
            source_chain([{'harness':'codex','sessionId':str(i)} for i in range(33)])

    def test_new_unrelated_launch_does_not_guess_an_ancestor(self):
        fields=source_launch_fields(self.profile,[],lambda *_a,**_k:self.fail('no source to read'))
        self.assertEqual({'sourceSessions':[]},fields)

    def test_union_keeps_actual_cross_harness_identity_and_order(self):
        a={'harness':'codex','sessionId':'same'};b={'harness':'zcode','sessionId':'same'}
        self.assertEqual([a,b],source_chain([a],[a,b]))


class ProducerSourceAdmissionTests(unittest.TestCase):
    @staticmethod
    def execution():
        from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE
        return dict(HEALTH_EXECUTION_PROFILE)

    @staticmethod
    def profile():
        return {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume', 'cwd':'/home/roomhacker/ServersAdministartion',
                'name':'health-repair', 'harness':'opencode', 'model':'minimax-coding-plan/MiniMax-M3.1-Flash', 'mode':'queue', 'instruction':'Только выбранный план.'}

    @staticmethod
    def event(runtime='opencode', provider='minimax-coding-plan', model='MiniMax-M3.1-Flash'):
        return {'schema':'notify.agent-job.v1','job_id':'health-remediation','humanRequested':True,
                'incident':{'id':'existing-incident','project':'fleet-health'}, 'health':{
                'source_sessions':[{'harness':'zcode','sessionId':'native-planner'}, {'harness':'codex','sessionId':'native-diagnosis'}],
                'selection':{'plan_id':'repair','execution':{'runtime':runtime,'provider':provider,'model':model,'reasoning':'default','topic':'health'}}}}

    @staticmethod
    def response(payload):
        import json
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps(payload).encode()
        return Response()

    def test_remediation_carries_verified_cross_harness_ancestors_and_never_human_requested(self):
        import json
        from pathlib import Path
        from unittest.mock import patch
        from notification_center import agent_job_helper as helper
        posts=[];reads=[]
        def runner(request,**kwargs):
            if '/coordination/context?' in request.full_url:
                reads.append(request.full_url);return self.response({'humanStopHeld':False})
            if '/launch-policy' in request.full_url:return self.response({'version':1,'incidentExecution':self.execution(),'allowedHarnesses':['opencode'],'preferredHarness':'opencode','models':{'opencode':'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            if '/api/models?' in request.full_url:return self.response({'models':['minimax-coding-plan/MiniMax-M3.1-Flash'],'stale':False})
            posts.append(json.loads(request.data));return self.response({'ok':True,'sessionId':'remediation-native','created':True})
        with patch.object(helper,'_run_health_remediation',return_value={'status':'completed'}):
            helper.run_profile('health-remediation',self.event(),Path('unused'),runner=runner,profile_override=self.profile())
        body=posts[0]
        self.assertEqual('opencode',body['harness'])
        self.assertEqual('queue',body['mode'])
        self.assertEqual(32,len(body['operation_id']))
        self.assertNotIn('humanRequested',body)
        self.assertTrue(all('consume=0' in url for url in reads))
        self.assertTrue(any('sessionId=native-planner' in url for url in reads))

    def test_stopped_diagnosis_blocks_remediation_before_any_post(self):
        from pathlib import Path
        from notification_center import agent_job_helper as helper
        posts=[]
        def runner(request,**kwargs):
            if '/coordination/context?' in request.full_url:return self.response({'humanStopHeld':'native-diagnosis' in request.full_url})
            if '/launch-policy' in request.full_url:return self.response({'version':1,'incidentExecution':self.execution(),'allowedHarnesses':['opencode'],'preferredHarness':'opencode','models':{'opencode':'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            if '/api/models?' in request.full_url:return self.response({'models':['minimax-coding-plan/MiniMax-M3.1-Flash'],'stale':False})
            posts.append(request);self.fail('a held ancestor must not create/send')
        with self.assertRaisesRegex(HealthSourceGateBlocked,'human_stop_held'):
            helper.run_profile('health-remediation',self.event(),Path('unused'),runner=runner,profile_override=self.profile())
        self.assertEqual([],posts)

    def test_stopped_diagnostic_cannot_create_a_new_planner_name(self):
        from unittest.mock import patch
        from notification_center import agent_job_helper as helper
        posts=[]
        profile={**self.profile(),'orchestrator_name':'different-planner-name','orchestrator_model':'gpt-5.6-sol','orchestrator_requested_model':'gpt-5.6-sol'}
        def runner(request,**kwargs):
            if '/coordination/context?' in request.full_url:return self.response({'humanStopHeld':True})
            if '/launch-policy' in request.full_url:return self.response({'version':1,'incidentExecution':self.execution(),'allowedHarnesses':['opencode'],'preferredHarness':'opencode','models':{'opencode':'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            if '/api/models?' in request.full_url:return self.response({'models':['minimax-coding-plan/MiniMax-M3.1-Flash'],'stale':False})
            posts.append(request);self.fail('planner launch after stop')
        with (patch.object(helper,'_load_health_callback',return_value=('http://127.0.0.1:8091','test')),
              patch.object(helper,'_session_json',return_value={'session':{'status':'completed'}}),
              patch.object(helper,'_extract_health_diagnosis',return_value={'diagnosis':'bounded diagnosis'})):
            with self.assertRaisesRegex(HealthSourceGateBlocked,'human_stop_held'):
                helper._run_health_diagnosis(profile,{'incident':{'id':'existing','project':'fleet-health'}},'native-diagnostic-stopped',runner,0)
        self.assertEqual([],posts)

    def test_quota_failure_is_never_retried_into_a_second_route(self):
        import json,io,urllib.error
        from pathlib import Path
        from notification_center import agent_job_helper as helper
        posts=[];reads=[]
        event=self.event()
        def runner(request,**kwargs):
            if '/coordination/context?' in request.full_url:
                reads.append(request.full_url);return self.response({'humanStopHeld':False})
            if '/launch-policy' in request.full_url:
                return self.response({'version':1,'incidentExecution':self.execution(),'allowedHarnesses':['opencode'],'preferredHarness':'opencode','models':{'opencode':'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            posts.append(json.loads(request.data))
            return self.response({'outcome':'pre_admission_rejected','operation_id':'op-1','routing_reason':'quota exhausted','attempts':[{'harness':'opencode','outcome':'pre_admission_rejected','reason':'quota exhausted'}]})
        with self.assertRaisesRegex(RuntimeError, 'before admission'):
            helper.run_profile('health-remediation',event,Path('unused'),runner=runner,profile_override=self.profile())
        self.assertEqual(1,len(posts))
        # Agent Herder owns the fallback chain; Notice Place sends one launch
        # with the declared preference and no second route.
        self.assertEqual('fleet-health',posts[0]['project'])
        self.assertEqual('minimax-coding-plan/MiniMax-M3.1-Flash',posts[0]['model'])
        self.assertTrue(any('sessionId=native-diagnosis' in url and 'consume=0' in url for url in reads))

    def test_failed_first_run_carries_sources_and_a_later_held_ancestor_blocks(self):
        import json
        from pathlib import Path
        from unittest.mock import patch
        from notification_center import agent_job_helper as helper
        posts=[];reads=[];held=[False]
        event=self.event()
        def runner(request,**kwargs):
            if '/coordination/context?' in request.full_url:
                reads.append(request.full_url);return self.response({'humanStopHeld':held[0]})
            if '/launch-policy' in request.full_url:
                return self.response({'version':1,'incidentExecution':self.execution(),'allowedHarnesses':['opencode'],'preferredHarness':'opencode','models':{'opencode':'minimax-coding-plan/MiniMax-M3.1-Flash'}})
            if '/project-launch' in request.full_url:
                posts.append(json.loads(request.data))
                return self.response({'outcome':'accepted','operation_id':'op-1','selected_harness':'opencode','model':'minimax-coding-plan/MiniMax-M3.1-Flash','mode':'queue','session_id':'remediation-native','routing_reason':'declared route','attempts':[]})
            if '/api/autopilot/sessions/' in request.full_url:return self.response({'enabled':True,'sessionId':'remediation-native'})
            posts.append(json.loads(request.data));return self.response({'ok':True,'sessionId':'remediation-native','created':True})
        with patch.object(helper,'_run_health_remediation',side_effect=helper.HealthStartPlanUnavailable()):
            with self.assertRaises(helper.HealthStartPlanUnavailable):
                helper.run_profile('health-remediation',event,Path('unused'),runner=runner,profile_override=self.profile())
            self.assertEqual(1,len(posts))
            self.assertTrue(any('sessionId=native-diagnosis' in url and 'consume=0' in url for url in reads))
            held[0]=True
            with self.assertRaisesRegex(HealthSourceGateBlocked,'human_stop_held'):
                helper.run_profile('health-remediation',event,Path('unused'),runner=runner,profile_override=self.profile())
        self.assertEqual(1,len(posts))


class DurableNoticeSourceTests(unittest.TestCase):
    def test_receipts_cross_the_actual_core_hub_boundary_and_blocked_worker_is_terminal(self):
        self.check_receipt_boundary(False)

    def test_ordinary_manual_retry_keeps_native_sources_without_original_health_event(self):
        self.check_receipt_boundary(True)

    def check_receipt_boundary(self, manual):
        import tempfile
        from pathlib import Path
        from notification_center.core import NotificationCenter
        from notification_center.gptadmin_agent import _agent_job_event
        from notification_center.http_api import DeliveryWorker
        with tempfile.TemporaryDirectory() as directory:
            center=NotificationCenter(Path(directory)/'notice.sqlite3',{'p':{'project':'fleet','max_severity':'critical'}},default_quiet_hours=[])
            created=center.create_event('p','intake',{'schema':'notify.event.v1','kind':'incident','project':'fleet','recipient':'me','severity':'critical','title':'Проверка','body':'Проверка','dedup_key':'same-pain','event_type':'ordinary.info' if manual else 'health.degraded','source_id':'source','host_id':'host','signal_type':'load','correlation_id':'episode'})
            if manual:
                center.apply_telegram_action(created['incident_id'],'ai','telegram:42')
                self.assertIsNone(center._health_original_event(created['incident_id']))
                with center._connection:
                    center._connection.execute("UPDATE deliveries SET status='cancelled' WHERE incident_id=? AND channel LIKE 'gptadmin.agent:%'",(created['incident_id'],))
            for stage,harness,native in [('health-diagnosis','codex','original-diagnostic'),('health-orchestrator','zcode','original-planner')]:
                center.record_health_agent_session(created['incident_id'],native,'',harness,native,'https://agent.bezrabotnyi.com',stage=stage)
            with center._connection:
                delivery_id=center._schedule_delivery(created['incident_id'],'gptadmin.agent:health-diagnosis','retry',0)
            claim=center.claim_due_deliveries(now_epoch=10**12,channel_group='agent')[0]
            payload=center.delivery_payload(claim)
            sources=[{'harness':'zcode','sessionId':'original-planner'},{'harness':'codex','sessionId':'original-diagnostic'}]
            self.assertEqual(sources,payload['health_context']['source_sessions'])
            self.assertEqual(sources,_agent_job_event('health-diagnosis',payload)['health']['source_sessions'])
            class Agent:
                def send(self,payload,key):raise HealthSourceGateBlocked('human_stop_held')
            worker=DeliveryWorker(center,object(),agent_jobs={'health-diagnosis':Agent()})
            worker.deliver(claim)
            row=center._connection.execute('SELECT status FROM deliveries WHERE id=?',(delivery_id,)).fetchone()
            self.assertEqual('cancelled',row['status'])
            self.assertEqual([],center.claim_due_deliveries(now_epoch=10**12+1000,channel_group='agent'))
            outcome=center._connection.execute("SELECT target_json FROM deliveries WHERE incident_id=? AND json_type(target_json,'$.health_outcome')='object'",(created['incident_id'],)).fetchone()
            import json
            self.assertEqual('human_stopped',json.loads(outcome['target_json'])['health_outcome']['status'])
            center._connection.close()

    def test_existing_plan_batch_native_receipts_are_retained_without_guessing(self):
        import tempfile
        from pathlib import Path
        from notification_center.core import NotificationCenter
        with tempfile.TemporaryDirectory() as directory:
            center=NotificationCenter(Path(directory)/'notice.sqlite3',{'p':{'project':'fleet','max_severity':'critical'}},default_quiet_hours=[])
            created=center.create_event('p','intake',{'schema':'notify.event.v1','kind':'incident','project':'fleet','recipient':'me','severity':'critical','title':'Проверка','dedup_key':'same-pain','event_type':'health.degraded'})
            center.record_health_update(created['incident_id'],'legacy-native-plan-receipt','health.plans_attached',{'orchestration':{'harness':'codex','diagnosis_session_id':'known-diagnosis','orchestrator_session_id':'known-planner'}},'worker')
            self.assertEqual([{'harness':'codex','sessionId':'known-planner'},{'harness':'codex','sessionId':'known-diagnosis'}],center._health_source_sessions(created['incident_id']))
            center._connection.close()
