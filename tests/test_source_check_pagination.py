import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from notification_center import incident_lifecycle as lifecycle
from notification_center.core import NotificationCenter, ValidationError
from notification_center.consumer_lifecycle import SourceVerifier

class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name)/'db', {
          'source': {'project':'test','max_severity':'emergency','principal_id':'probe','incident_role':'verifier','verification_sources':['src']},
          'other': {'project':'test','max_severity':'emergency','principal_id':'other','incident_role':'verifier','verification_sources':['src']},
          'agent': {'project':'test','max_severity':'emergency','principal_id':'agent','incident_role':'executor'}})
        self.addCleanup(self.center._connection.close)
        for n in range(21):
            event = dict(schema='notify.event.v1',kind='incident',severity='notice',project='test',recipient='test',title='x',dedup_key=f'case:{n}',event_type='health.degraded',source_id='src',host_id='host',signal_type='disk',correlation_id=f'corr:{n}')
            incident = self.center.create_event('source',f'event:{n}',event)['incident_id']
            self.center.record_work_finished('agent',incident,f'finish:{n}',dict(attempt_id=f'a:{n}',session_id='s',harness='codex'))
        self.center._connection.execute('UPDATE incident_lifecycle SET verification_due=?',(time.time()-1,))
        self.center._connection.commit()
    def test_cursor_reaches_21_and_preserves_pending(self):
        page=lifecycle.due_checks_page(self.center,'source','test','src',limit=20)
        second=lifecycle.due_checks_page(self.center,'source','test','src',limit=20,cursor=page['next_cursor'])
        ids=[r['incident_id'] for r in page['requests']+second['requests']]
        self.assertEqual(21,len(ids));self.assertEqual(21,len(set(ids)));self.assertIsNone(second['next_cursor'])
        self.assertEqual(21,self.center._connection.execute("SELECT COUNT(*) FROM incident_lifecycle WHERE verification_state='due'").fetchone()[0])
        with self.assertRaises((ValueError,ValidationError)): lifecycle.due_checks_page(self.center,'other','test','src',cursor=page['next_cursor'])
        with patch.object(lifecycle.time,'time',return_value=page['cursor_expires_at']+1):
            with self.assertRaises((ValueError,ValidationError)): lifecycle.due_checks_page(self.center,'source','test','src',cursor=page['next_cursor'])

    def test_http_endpoint_passes_pagination_and_rejects_bad_limits(self):
        import json, threading, urllib.request, urllib.parse, urllib.error
        from http.server import ThreadingHTTPServer
        from notification_center.http_api import build_handler
        server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(self.center,'fixture-health',producer_only=True))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def get(**extra):
            query=urllib.parse.urlencode({'project':'test','source_id':'src',**extra})
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/verification-requests?'+query,headers={'Authorization':'Bearer source'})
            with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
        first=get(limit=20)
        self.assertTrue(first['next_cursor'])
        second=get(limit=20,cursor=first['next_cursor'])
        self.assertEqual(1,len(second['requests']))
        for value in ('0','101','1.5'):
            with self.assertRaises(urllib.error.HTTPError) as caught:get(limit=value)
            self.assertEqual(400,caught.exception.code)
    def test_wait_releases_center_lock(self):
        import threading
        self.center._connection.execute("UPDATE incident_lifecycle SET verification_state='healthy'")
        self.center._connection.commit()
        started = threading.Event()
        acquired = []
        original_sleep = time.sleep
        def sleep_without_lock(seconds):
            def take_lock():
                ok = self.center._lock.acquire(timeout=0.1)
                acquired.append(ok)
                if ok: self.center._lock.release()
                started.set()
            worker = threading.Thread(target=take_lock)
            worker.start(); worker.join()
            original_sleep(seconds)
        with patch.object(lifecycle.time, 'sleep', side_effect=sleep_without_lock):
            page = lifecycle.due_checks_page(self.center, 'source', 'test', 'src', wait_seconds=0.02)
        self.assertTrue(started.is_set())
        self.assertEqual([True], acquired)
        self.assertEqual([], page['requests'])

    def test_sdk_advances_past_unowned_first_page(self):
        requests=[{'incident_id':f'i{n}','request_id':f'r{n}','generation':1,'group':{'project':'test','source_id':'src','host_id':'host','signal_type':str(n)}} for n in range(21)]
        calls=[]
        def transport(method,path,body=None,key=None):
            calls.append((method,path))
            if method=='GET': return {'requests':requests[20:] if 'cursor=' in path else requests[:20],'next_cursor':None if 'cursor=' in path else 'next'}
            return {'accepted':True}
        client=SourceVerifier('http://test','source','test','src','host',self.temp.name,transport=transport)
        result=client.tick(lambda _: {'observed_state':'healthy','evidence_refs':['fresh']},accept_group=lambda group:group['signal_type']=='20')
        self.assertEqual(1,len(result));self.assertEqual(2,len([x for x in calls if x[0]=='GET']))

class SDKContextTests(unittest.TestCase):
    def test_source_context_reaches_health_event(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
        from notify_center_client import NotificationCenterClient
        client = NotificationCenterClient('https://example.invalid/v1/events', 'token')
        sent=[]
        client._request=lambda method,url,**kwargs: sent.append(kwargs['payload']) or {'incident_id':'i'}
        context={'schema':'notify.source-context.v1','service':'edge','impact':'offline','symptoms':['timeout'],'observed_at':100,'freshness_seconds':60,'verifier_ref':'probe:edge'}
        client.emit(project='test',severity='notice',title='x',dedup_key='d',idempotency_key='id',event_type='health.degraded',source_id='src',host_id='host',signal_type='disk',problem_type='disk',correlation_id='c',observed_at=100,source_context=context)
        self.assertEqual(context,sent[0]['source_context'])
        with self.assertRaises(ValueError):
            client.emit(project='test',severity='notice',title='x',dedup_key='d',source_context=context)
