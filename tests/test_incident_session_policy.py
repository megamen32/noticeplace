import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from notification_center.core import NotificationCenter
from notification_center.gptadmin_agent import _agent_job_event
from notification_center.incident_session_policy import normalize_policy, read_policy
from notification_center.agent_job_helper import project_launch

class SessionPolicyTests(unittest.TestCase):
    def test_operator_setting_survives_delivery_signature_envelope_and_launch(self):
        with tempfile.TemporaryDirectory() as root:
            center = NotificationCenter(Path(root)/'test.sqlite', {'p': {'project':'health-monitor','max_severity':'emergency','agent_jobs':['health-diagnosis']}},default_quiet_hours=[])
            center.set_runtime_setting('incident_session_stickiness_enabled','0')
            center.set_runtime_setting('incident_session_ttl_seconds','7200')
            center.set_runtime_setting('incident_skill_learning_enabled','0')
            created = center.create_event('p','sticky-source',{'schema':'notify.event.v1','kind':'incident','project':'health-monitor','recipient':'me','severity':'important','title':'Test','dedup_key':'test','event_type':'health.degraded','source_id':'probe','host_id':'node','signal_type':'cpu','correlation_id':'episode'})
            delivery=center.claim_due_deliveries(channel_group='agent')[0]
            payload=center.delivery_payload(delivery)
            event=_agent_job_event('health-diagnosis',payload,delivery['delivery_key'])
            policy=event['health']['incident_session_policy']
            self.assertEqual({'enabled':False,'ttlSeconds':7200},policy)
            self.assertEqual({'learn':False,'publish':False},event['health']['incident_skill_policy'])
            self.assertIs(event['health']['classifier_enabled'],True)
            captured=[]
            class Response:
                status = 200
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,*args):return b'{"outcome":"accepted"}'
            def runner(request,**kwargs):
                captured.append(json.loads(request.data));return Response()
            with patch('notification_center.agent_job_helper.require_herder_owner_running'):
                project_launch({'url':'http://127.0.0.1:18787/api/sessions/new-or-resume'},'health-monitor','/tmp/qa','test',runner,purpose='noticeplace-health-analysis',health_launch_token='fixture-token',incident_session_policy=policy)
            self.assertEqual(policy,captured[0]['incident_session_policy'])
            self.assertNotIn('fixture-token',json.dumps(captured))
            center._connection.close()

    def test_defaults_and_invalid_policy(self):
        self.assertEqual({'enabled':True,'ttlSeconds':86400},normalize_policy())
        for ttl in [-1,1.5,True,'86400',2592001]:
            with self.assertRaises(ValueError):normalize_policy({'enabled':True,'ttlSeconds':ttl})
