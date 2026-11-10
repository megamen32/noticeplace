import io
import json
import urllib.error
from unittest.mock import patch
import pytest
from notification_center import agent_job_helper as h
from notification_center.core import NotificationCenter
from notification_center.gptadmin_agent import GptAdminAgentJobAdapter
from notification_center.herder_policy_client import HerderPolicyClient, HerderPolicyUnavailable
from notification_center.incident_model_policy import incident_model_state
from notification_center.admin_http import _incident_model_block


def test_real_http_409_proven_denial():
    body = {'outcome':'no_harness','routingReasonCode':'model_not_permitted_for_project','attempts':[],'operationId':'op1','project':'p'}
    def runner(req, **kw):
        raise urllib.error.HTTPError(req.full_url,409,'conflict',{},io.BytesIO(json.dumps(body).encode()))
    profile={'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','cwd':'/tmp','mode':'queue'}
    with patch.object(h,'require_herder_owner_running'):
        result=h.project_launch(profile,'p','/tmp','test',runner,operation_id='op1',harness='opencode',model='old/model',purpose=h.HEALTH_LAUNCH_PURPOSE,health_launch_token='fixture')
    assert result['outcome']=='no_harness'


@pytest.mark.parametrize('source,reason', [('agent-herder','herder_rejected'),('agent-herder-owner-disarm','herder_preflight_unavailable')])
def test_negative_survives_projection_and_core(source,reason):
    receipt={'status':'source_blocked','source':source,'reason':reason,'admission':'not_attempted','operation_id':'op1','incident_id':'inc1','human_held':True,'retryable':False,'non_retryable':True}
    result=GptAdminAgentJobAdapter._bounded_agent_receipt({'status':'completed','result':receipt})
    assert result==receipt
    assert NotificationCenter._explicit_agent_prelaunch_negative({'status':'completed','job_id':'j','route_id':'r','agent_receipt':result},'inc1')
    assert not NotificationCenter._explicit_agent_prelaunch_negative({'status':'completed','job_id':'j','route_id':'r','agent_receipt':{**result,'session_id':'ses'}},'inc1')


def test_page_revision_controls_write():
    class Response(io.BytesIO):
        headers={'ETag':'"new"'}
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    calls=[]
    def runner(req,**kw):
        calls.append(req)
        return Response(b'{"incidentExecution":{"provider":"p","model":"p/m"}}')
    client=HerderPolicyClient('http://fixture/',runner=runner)
    with pytest.raises(HerderPolicyUnavailable):client.set_incident_model('p/new','"old"')
    assert len(calls)==1
    with patch('notification_center.herder_policy_client.HerderPolicyClient.read',return_value=({'incidentExecution':{'provider':'p','model':'p/m'}},'"old"')):
        state=incident_model_state(None)
    assert 'name="revision" value="&quot;old&quot;"' in _incident_model_block(state,'csrf')


@pytest.mark.parametrize('changes', [
    {'attempts':[{'outcome':'in_flight'}]}, {'operationId':'other'}, {'routingReasonCode':'other'},
    {'sessionId':'native'}, {'outcome':'in_flight'},
])
def test_ambiguous_http_409_is_not_classified_as_no_admission(changes):
    body={'outcome':'no_harness','routingReasonCode':'model_not_permitted_for_project','attempts':[],'operationId':'op1','project':'p', **changes}
    def runner(req,**kw):raise urllib.error.HTTPError(req.full_url,409,'conflict',{},io.BytesIO(json.dumps(body).encode()))
    profile={'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','cwd':'/tmp','mode':'queue'}
    with patch.object(h,'require_herder_owner_running'), pytest.raises(h.HealthAgentEndpointError):
        h.project_launch(profile,'p','/tmp','test',runner,operation_id='op1',harness='opencode',purpose=h.HEALTH_LAUNCH_PURPOSE,health_launch_token='fixture')


def test_cas_uses_page_etag_when_update_races():
    class Response(io.BytesIO):
        headers={'ETag':'"page"'}
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    seen=[]
    def runner(req,**kwargs):
        seen.append(req)
        if req.get_method()=='PUT':raise urllib.error.HTTPError(req.full_url,412,'changed',{},None)
        return Response(b'{"incidentExecution":{"provider":"p","model":"p/m"}}')
    with pytest.raises(HerderPolicyUnavailable,match='изменились'):
        HerderPolicyClient('http://fixture/',runner=runner).set_incident_model('p/new','"page"')
    assert len(seen)==2 and seen[1].get_header('If-match')=='"page"'


@pytest.mark.parametrize('mutation', [{'incident_id':''},{'operation_id':'x\n'},{'session_id':'s'},{'outcome':'unknown'},{'retryable':True},{'incident_id':'different'}])
def test_negative_receipt_does_not_accept_ambiguous_evidence(mutation):
    original={'status':'source_blocked','source':'agent-herder','reason':'herder_rejected','admission':'not_attempted','operation_id':'op1','incident_id':'inc1','human_held':True,'retryable':False,'non_retryable':True}
    envelope={'status':'completed','job_id':'j','route_id':'r','agent_receipt':{**original,**mutation}}
    assert not NotificationCenter._explicit_agent_prelaunch_negative(envelope,'inc1')

@pytest.mark.parametrize('changes', [{'delivery':'admission_unknown'}, {'project':'other-project'}, {'pending':True}, {'admissionUnknown':True}, {'uncertain':True}, {'created':'false'}, {'attempts':{}}])
def test_contradictory_http_409_is_not_definite(changes):
    body={'outcome':'no_harness','routingReasonCode':'model_not_permitted_for_project','attempts':[],'operationId':'op1','project':'p', **changes}
    def runner(req,**kw):raise urllib.error.HTTPError(req.full_url,409,'conflict',{},io.BytesIO(json.dumps(body).encode()))
    profile={'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','cwd':'/tmp','mode':'queue'}
    with patch.object(h,'require_herder_owner_running'), pytest.raises(h.HealthAgentEndpointError):
        h.project_launch(profile,'p','/tmp','test',runner,operation_id='op1',harness='opencode',purpose=h.HEALTH_LAUNCH_PURPOSE,health_launch_token='fixture')

@pytest.mark.parametrize('mutation', [{'pending':True}, {'admissionUnknown':True}, {'uncertain':True}, {'delivery':'admission_unknown'}, {'attempts':[{'outcome':'in_flight'}]}, {'created':'false'}])
def test_negative_contradictions_cannot_project(mutation):
    receipt={'status':'source_blocked','source':'agent-herder','reason':'herder_rejected','admission':'not_attempted','operation_id':'op1','incident_id':'inc1','human_held':True,'retryable':False,'non_retryable':True,**mutation}
    assert GptAdminAgentJobAdapter._bounded_agent_receipt({'status':'completed','result':receipt}) == {}
    assert not NotificationCenter._explicit_agent_prelaunch_negative({'status':'completed','job_id':'j','route_id':'r','agent_receipt':receipt},'inc1')
