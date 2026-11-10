import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from notification_center.core import NotificationCenter
from notification_center.herder_policy_client import HerderPolicyClient, HerderPolicyUnavailable
from notification_center.incident_model_policy import incident_model_state, write_incident_model
from notification_center.http_api import DeliveryWorker


def test_incident_lifecycle_works_without_herder():
    with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', side_effect=OSError('offline')):
        center = NotificationCenter(Path(tmp) / 'test.sqlite', {'producer': {'project': 'fleet', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        event = {'schema':'notify.event.v1', 'project':'fleet', 'recipient':'me', 'kind':'incident', 'severity':'emergency', 'title':'test', 'body':'test', 'dedup_key':'case'}
        first = center.create_event('producer', 'one', event)
        repeated = center.create_event('producer', 'two', event)
        assert first['incident_id'] == repeated['incident_id']
        class Sender:
            def send(self, payload):
                return {'message_id': 123, 'chat_id': 'local-fixture'}
        worker = DeliveryWorker(center, Sender())
        delivery = next(d for d in center.claim_due_deliveries(now_epoch=10**12) if d['id'] == first['initial_delivery_id'])
        worker.deliver(delivery)
        assert center._connection.execute('SELECT status FROM deliveries WHERE id=?', (delivery['id'],)).fetchone()['status'] == 'sent'
        result = center.resolve_event('producer', 'resolve', {'schema':'notify.event.v1', 'project':'fleet', 'recipient':'me', 'dedup_key':'case', 'action':'resolve'})
        assert result['resolved'] is True
        center.set_runtime_setting('health_incident_model', 'stale/model')
        state = incident_model_state(center)
        assert state['available'] is False and state['effective'] == ''
        with pytest.raises(HerderPolicyUnavailable):
            write_incident_model(center, 'omniroute/cx/gpt-6-luna')
        assert center.get_runtime_setting('health_incident_model') == 'stale/model'
        center._connection.close()


def test_update_uses_current_etag():
    seen = []
    class Response(io.BytesIO):
        headers = {'ETag': '"v1"'}
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def runner(request, **kwargs):
        seen.append(request)
        policy = {'incidentExecution': {'runtime':'opencode', 'provider':'old', 'model':'old/model'}}
        return Response(json.dumps(policy).encode())
    HerderPolicyClient('http://fixture/path', runner=runner).set_incident_model('omniroute/cx/gpt-6-luna', '"v1"')
    assert seen[1].get_header('If-match') == '"v1"'
    assert json.loads(seen[1].data)['incidentExecution']['model'] == 'omniroute/cx/gpt-6-luna'


def test_project_intent_submits_without_model_and_uses_receipt():
    from notification_center import agent_job_helper as h
    from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT
    profile = {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume', 'name':'health', 'harness':'opencode', 'cwd':'/tmp', 'mode':'queue', 'instruction':'repair', 'health_launch_token':'fixture'}
    event = {'schema':'notify.agent-job.v1', 'job_id':'health-diagnosis', 'incident':{'id':'fixture', 'project':'sample', 'severity':'critical', 'title':'fixture', 'body':'fixture', 'dedup_key':'fixture'}, 'health':{'incident_model':PROJECT_ROUTE_INTENT}}
    requests = []
    def launch(profile, project, cwd, message, runner, **kwargs):
        requests.append(kwargs)
        return {'outcome':'accepted', 'session_id':'session-1', 'selected_harness':'opencode', 'model':'provider/selected', 'operation_id':kwargs['operation_id']}
    with patch.object(h, 'require_herder_owner_running'), patch.object(h, '_load_health_callback'), patch.object(h, 'source_launch_fields', return_value={}), patch.object(h, 'project_launch', side_effect=launch), patch.object(h, 'arm_session_autopilot', return_value=True), patch.object(h, '_run_health_diagnosis', return_value={'status':'completed'}) as diagnose:
        result = h.run_profile('health-diagnosis', event, Path('/unused'), profile_override=profile)
    assert requests[0]['model'] == '' and requests[0]['harness'] == 'opencode'
    assert requests[0]['purpose'] == h.HEALTH_LAUNCH_PURPOSE and requests[0]['operation_id']
    assert result['model'] == 'provider/selected'
    assert diagnose.call_args.args[0]['model'] == 'provider/selected'


def test_old_concrete_snapshot_is_submitted_exactly():
    from notification_center import agent_job_helper as h
    profile = {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume', 'name':'health', 'harness':'opencode', 'cwd':'/tmp', 'mode':'queue', 'instruction':'repair'}
    event = {'schema':'notify.agent-job.v1','job_id':'health-diagnosis','incident':{'id':'fixture','project':'sample'},'health':{'incident_model':'old/model'}}
    response = {'outcome':'no_harness', 'routing_reason_code':'model_not_permitted_for_project', 'routing_reason':'denied', 'attempts':[], 'project':'sample', 'operation_id':h._operation_id(event, 'health-diagnosis')}
    with patch.object(h, 'require_herder_owner_running'), patch.object(h, '_load_health_callback'), patch.object(h, 'source_launch_fields', return_value={}), patch.object(h, 'project_launch', return_value=response) as launch:
        receipt = h.run_profile('health-diagnosis', event, Path('/unused'), profile_override=profile)
    assert launch.call_count == 1 and launch.call_args.kwargs['model'] == 'old/model'
    assert receipt['admission'] == 'not_attempted' and receipt['reason'] == 'herder_rejected'


def test_absent_herder_mute_and_dashboard():
    from notification_center.admin import AdminConfigStore
    with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', side_effect=OSError('offline')):
        root = Path(tmp)
        center = NotificationCenter(root / 'test.sqlite', {'producer': {'project': 'fleet', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        event = {'schema':'notify.event.v1','project':'fleet','recipient':'me','kind':'incident','severity':'emergency','title':'test','body':'test','dedup_key':'muted'}
        created = center.create_event('producer', 'one', event)
        center.set_incident_notifications_muted(created['incident_id'], True, 'fixture')
        assert center.list_notification_mutes()
        store = AdminConfigStore(root/'env', root/'routes', root, health_config_path=root/'no-config')
        store._consumer_center = center
        assert store.health_dashboard()['incident_model']['available'] is False
        center._connection.close()


def test_etag_conflict_does_not_retry_or_write_local():
    import urllib.error
    calls = []
    class Response(io.BytesIO):
        headers = {'ETag':'"one"'}
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def runner(request, **kwargs):
        calls.append(request)
        if request.get_method() == 'PUT':
            raise urllib.error.HTTPError(request.full_url, 412, 'conflict', {}, None)
        return Response(b'{"incidentExecution":{"runtime":"opencode","provider":"p","model":"p/m"}}')
    with pytest.raises(HerderPolicyUnavailable, match='изменились'):
        HerderPolicyClient('http://fixture/', runner=runner).set_incident_model('p/new', '"one"')
    assert len(calls) == 2


def test_absent_owner_gate_is_definite_before_admission():
    from notification_center import agent_job_helper as h
    from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT
    profile = {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','name':'health','harness':'opencode','cwd':'/tmp','mode':'queue','instruction':'repair','health_launch_token':'fixture'}
    event = {'schema':'notify.agent-job.v1','job_id':'health-diagnosis','incident':{'id':'i','project':'p'},'health':{'incident_model':PROJECT_ROUTE_INTENT}}
    with patch.object(h, 'require_herder_owner_running', side_effect=h.HerderOwnerStateBlocked('herder_owner_state_unknown','offline')), patch.object(h, 'project_launch') as launch:
        receipt = h.run_profile('health-diagnosis', event, Path('/unused'), profile_override=profile)
    assert receipt['admission'] == 'not_attempted' and receipt['status'] == 'source_blocked'
    launch.assert_not_called()


def test_actual_project_launch_body_and_receipt():
    from notification_center import agent_job_helper as h
    from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT
    profile = {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','name':'health','harness':'opencode','cwd':'/tmp','mode':'queue','instruction':'repair','health_launch_token':'fixture'}
    event = {'schema':'notify.agent-job.v1','job_id':'health-diagnosis','incident':{'id':'i','project':'p','title':'fixture','body':'test','severity':'critical'},'health':{'incident_model':PROJECT_ROUTE_INTENT}}
    bodies = []
    class Response(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def runner(req, **kwargs):
        assert req.full_url.endswith('/api/automation/project-launch')
        body = json.loads(req.data)
        bodies.append(body)
        return Response(json.dumps({'outcome':'accepted','operation_id':body['operation_id'],'selected_harness':'opencode','model':'omniroute/cx/gpt-6-luna','session_id':'native'}).encode())
    callbacks = []
    with patch.object(h, 'require_herder_owner_running'), patch.object(h, '_load_health_callback'), patch.object(h, 'source_launch_fields', return_value={}), patch.object(h, 'arm_session_autopilot', return_value=True), patch.object(h, '_run_health_diagnosis', return_value={'status':'completed'}) as observer:
        receipt = h.run_profile('health-diagnosis', event, Path('/unused'), runner=runner, profile_override=profile, session_callback=lambda value: callbacks.append(dict(value)))
    assert len(bodies) == 1
    assert bodies[0]['harness'] == 'opencode' and 'model' not in bodies[0]
    assert bodies[0]['purpose'] == 'noticeplace.health-analysis' and bodies[0]['project'] == 'p' and bodies[0]['operation_id']
    assert receipt['model'] == 'omniroute/cx/gpt-6-luna'
    assert observer.call_args.args[0]['model'] == receipt['model']
    assert callbacks[0]['model'] == receipt['model']


def test_independent_source_verification_closes_while_herder_offline(tmp_path):
    with patch('urllib.request.urlopen', side_effect=OSError('offline')):
        center = NotificationCenter(tmp_path / 'notify.db', {'p': {'project': 'fleet-health', 'max_severity': 'critical'}}, default_quiet_hours=[])
        try:
            created = center.create_event('p', 'health', {'schema':'notify.event.v1', 'project':'fleet-health', 'recipient':'me', 'kind':'incident', 'severity':'critical', 'title':'disk', 'body':'full', 'dedup_key':'health:disk', 'event_type':'health.degraded', 'source_id':'disk-source', 'host_id':'host', 'signal_type':'disk', 'correlation_id':'corr', 'source_fingerprint':'fp-1'})
            incident = created['incident_id']
            receipt = {'status':'completed','agent_receipt':{'path':'repair-first','plan_id':'repair','step':'freed space','observed_state':'healthy','source_id':'disk-source','source_fingerprint':'fp-1','evidence_refs':['probe:disk'],'session_id':'ses-executor','harness':'opencode'}}
            result = center.record_agent_job_result(incident, 'd1', 'health-diagnosis', receipt, {'source_id':'disk-source','source_fingerprint':'fp-1'})
            assert result['accepted'] and center.get_incident(incident)['state'] == 'open'
            verified = center.record_health_verification(incident, 'probe:server100', {'source_id':'disk-source','verifier_id':'disk-probe','verification_id':'probe-1','healthy':True,'fingerprint':'fp-1','evidence_refs':['probe:disk']}, 'probe-1')
            assert verified['healthy'] and center.get_incident(incident)['state'] == 'resolved'
        finally:
            center._connection.close()


def test_herder_timeout_after_admission_is_never_replayed():
    from notification_center import agent_job_helper as h
    from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT
    profile = {'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','name':'health','harness':'opencode','cwd':'/tmp','mode':'queue','instruction':'repair','health_launch_token':'fixture'}
    event = {'schema':'notify.agent-job.v1','job_id':'health-diagnosis','incident':{'id':'i','project':'p','title':'fixture','severity':'critical'},'health':{'incident_model':PROJECT_ROUTE_INTENT}}
    calls = []
    def runner(request, **kwargs):
        calls.append(request)
        raise TimeoutError('admission response lost')
    with patch.object(h, 'require_herder_owner_running'), patch.object(h, '_load_health_callback'), patch.object(h, 'source_launch_fields', return_value={}):
        with pytest.raises(Exception):
            h.run_profile('health-diagnosis', event, Path('/unused'), runner=runner, profile_override=profile)
    assert len(calls) == 1


def test_worker_records_definite_unavailable_receipt(tmp_path):
    from notification_center.gptadmin_agent import DirectHealthRemediationAdapter
    from notification_center.agent_job_helper import HerderOwnerStateBlocked
    from notification_center import agent_job_helper as h
    center = NotificationCenter(tmp_path/'db', {'source': {'project':'fixture','max_severity':'critical','agent_jobs':['health-remediation']}}, default_quiet_hours=[])
    try:
        created = center.create_event('source','e',{'schema':'notify.event.v1','kind':'incident','project':'fixture','recipient':'me','severity':'critical','title':'fixture','dedup_key':'x'})
        delivery_id = center._schedule_delivery(created['incident_id'],'gptadmin.agent:health-remediation','fixture',0, center._health_agent_delivery_target())
        adapter = DirectHealthRemediationAdapter(config_path=tmp_path/'missing')
        worker = DeliveryWorker(center, object(), agent_jobs={'health-remediation': adapter})
        claim = center.claim_due_deliveries(delivery_id=delivery_id)[0]
        with patch.object(h, 'protected_launch_token', return_value='fixture'), patch.object(h, 'require_herder_owner_running', side_effect=HerderOwnerStateBlocked('herder_owner_state_unknown','offline')), patch.object(h, 'project_launch', side_effect=AssertionError('unexpected admission')) as launch, patch.object(center, 'record_agent_job_result', wraps=center.record_agent_job_result) as record:
            worker.deliver(claim)
        row = center._connection.execute('SELECT status FROM deliveries WHERE id=?',(delivery_id,)).fetchone()
        assert row['status'] == 'failed'
        launch.assert_not_called()
        receipt = record.call_args.args[3]['agent_receipt']
        assert receipt['status'] == 'source_blocked'
        assert receipt['admission'] == 'not_attempted'
        assert receipt['reason'] == 'herder_owner_state_unknown'
    finally:
        center._connection.close()


@pytest.mark.parametrize('body', [b'not-json', b'{"incidentExecution":[]}', b'{"incidentExecution":"invalid"}', b'{"incidentExecution":{"model":17,"provider":"p"}}', b'x' * (1024 * 1024 + 1)])
def test_malformed_policy_never_crashes_dashboard(tmp_path, body):
    from notification_center.admin import AdminConfigStore
    class Response(io.BytesIO):
        headers = {'ETag':'"v1"'}
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    with patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs: Response(body)):
        center = NotificationCenter(tmp_path/'db', {'p': {'project':'fleet','max_severity':'critical'}}, default_quiet_hours=[])
        store = AdminConfigStore(tmp_path/'env', tmp_path/'routes', tmp_path, health_config_path=tmp_path/'absent')
        store._consumer_center = center
        assert store.health_dashboard()['incident_model']['available'] is False
        center._connection.close()
