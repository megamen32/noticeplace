"""Isolated SmartEdge consumer lifecycle; no external delivery or network."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
sys.path.insert(0,str(ROOT))
from notify_center_client import NotificationCenterClient, SourceVerifier
from notification_center.core import NotificationCenter

INFRA=Path(os.environ.get('SMARTEDGE_SOURCE_DIR','/home/roomhacker/ServersAdministartion/ops/router-health-lite'))
loader=importlib.machinery.SourceFileLoader('smartedge_test',str(INFRA/'router-haproxy-source-verifier'))
spec=importlib.util.spec_from_loader(loader.name,loader)
edge=importlib.util.module_from_spec(spec);loader.exec_module(edge)

class SmartEdgeIntegration(unittest.TestCase):
    def test_one_executor_then_fresh_check_closes(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root=Path(directory)
            center=NotificationCenter(root/'state.db',{
                'source':{'project':'health-monitor','max_severity':'critical','principal_id':'edge-source',
                          'incident_role':'verifier','verification_sources':['router-haproxy-watchdog'],
                          'agent_jobs':['health-diagnosis'],'suppress_owner_delivery':True},
                'executor':{'project':'health-monitor','max_severity':'critical','principal_id':'edge-executor','incident_role':'executor'}})
            self.addCleanup(center._connection.close)
            client=NotificationCenterClient('http://fixture/v1/events','source')
            client._request=lambda method,url,**kw:center.create_event('source',kw['idempotency_key'],kw['payload'])
            created=client.emit(project='health-monitor',recipient='health',severity='important',title='Маршрут недоступен',
                body='Диагностика',dedup_key='router-haproxy:lan-smart-edge',idempotency_key='edge:episode',
                event_type='health.degraded',source_id='router-haproxy-watchdog',host_id='router',
                signal_type='https',problem_type='https',correlation_id='router-haproxy:episode',observed_at=100)
            inc=created['incident_id']
            deliveries=center._connection.execute('SELECT channel FROM deliveries WHERE incident_id=?',(inc,)).fetchall()
            self.assertEqual(['gptadmin.agent:health-diagnosis'],[x['channel'] for x in deliveries])
            center.record_work_finished('executor',inc,'finish:one',dict(attempt_id='one',session_id='session',harness='opencode'))
            self.assertEqual('open',center.get_incident(inc)['state'])
            posted=[]
            def transport(method,path,body=None,key=None):
                if method=='POST':
                    posted.append(body['observed_state']);return center.record_source_check('source',inc,key,body)
                if path.startswith('/v1/incidents/'):return center.get_incident(inc)
                return {'requests':center.due_source_checks('source','health-monitor','router-haproxy-watchdog')}
            verifier=SourceVerifier('http://fixture','source','health-monitor','router-haproxy-watchdog','router',root/'verifier',transport=transport)
            counters=root/'counters';counters.write_text('0 2 0\n')
            def fake_run(args,**kw):
                is_ssh='ssh' in args[0]
                return subprocess.CompletedProcess(args,0,'running' if is_ssh else '','')
            original=edge.fresh_probe
            with patch.dict(os.environ,{'COUNTERS_FILE':str(counters)}), patch.object(edge,'fresh_probe',side_effect=lambda g,o:original(g,o,run=fake_run)):
                self.assertEqual('pending',edge.tick(verifier,'episode'))
                self.assertEqual('open',center.get_incident(inc)['state'])
                center._connection.execute('UPDATE incident_lifecycle SET verification_due=0 WHERE incident_id=?',(inc,));center._connection.commit()
                counters.write_text('0 3 0\n')
                self.assertEqual('healthy',edge.tick(verifier,'episode'))
            self.assertEqual(['unknown','healthy'],posted)
            self.assertEqual('resolved',center.get_incident(inc)['state'])
            self.assertEqual(1,len(deliveries))

    def test_loopback_http_sdk_end_to_end(self):
        from notification_center.http_api import build_handler
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root=Path(directory)
            center=NotificationCenter(root/'http.db',{
                'source':{'project':'health-monitor','max_severity':'critical','principal_id':'edge-source',
                          'incident_role':'verifier','verification_sources':['router-haproxy-watchdog'],
                          'agent_jobs':['health-diagnosis']},
                'executor':{'project':'health-monitor','max_severity':'critical','principal_id':'edge-executor','incident_role':'executor'}})
            self.addCleanup(center._connection.close)
            center._connection.execute("INSERT INTO runtime_settings(key,value,updated_at) VALUES ('telegram_all_notifications_enabled','0',0) ON CONFLICT(key) DO UPDATE SET value='0'")
            center._connection.commit()
            server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(center,'probe-fixture','mcp-fixture'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(thread.join);self.addCleanup(server.shutdown)
            url='http://127.0.0.1:'+str(server.server_port)
            client=NotificationCenterClient(url+'/v1/events','source')
            result=client.emit(project='health-monitor',recipient='health',severity='important',title='Маршрут недоступен',
                dedup_key='router-haproxy:lan-smart-edge',idempotency_key='fixture:open',event_type='health.degraded',
                source_id='router-haproxy-watchdog',host_id='router',signal_type='https',problem_type='https',
                correlation_id='router-haproxy:episode',observed_at=100)
            inc=result['incident_id']
            channels=[r['channel'] for r in center._connection.execute('SELECT channel FROM deliveries WHERE incident_id=?',(inc,)).fetchall()]
            self.assertEqual(['gptadmin.agent:health-diagnosis'],channels)
            req=urllib.request.Request(url+'/v1/incidents/'+inc+'/health/work-finished',
                data=json.dumps(dict(attempt_id='one',session_id='fixture-session',harness='opencode')).encode(),
                method='POST',headers={'Authorization':'Bearer executor','Content-Type':'application/json','Idempotency-Key':'fixture:finish'})
            with urllib.request.urlopen(req,timeout=5) as response: self.assertEqual(200,response.status)
            self.assertEqual('open',client.get_incident(inc)['state'])
            counters=root/'counters';counters.write_text('0 3 0\n')
            def fake_run(args,**kw):return subprocess.CompletedProcess(args,0,'running' if 'ssh' in args[0] else '','')
            original=edge.fresh_probe
            verifier=SourceVerifier(url,'source','health-monitor','router-haproxy-watchdog','router',root/'verifier')
            with patch.dict(os.environ,{'COUNTERS_FILE':str(counters)}),patch.object(edge,'fresh_probe',side_effect=lambda g,o:original(g,o,run=fake_run)):
                self.assertEqual('healthy',edge.tick(verifier,'episode'))
            self.assertEqual('resolved',client.get_incident(inc)['state'])

if __name__=='__main__':unittest.main()
