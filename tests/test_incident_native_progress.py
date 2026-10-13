"""Focused integration: safe native readback, exact identity, durable latest flush.

Purpose: catch tool/reasoning leaks and profile-default session reads.
Expected <1 second, maximum 5 seconds; bounded fake HTTP transport.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.incident_progress import IncidentProgressReader, safe_projection


class IncidentNativeProgressTests(unittest.TestCase):
    def test_projection_reads_only_assistant_text_and_retains_incomplete_marker(self):
        details={'session':{'status':'running','needsPermission':False,'lastActivity':'now'},'history':{'complete':False},
            'messages':[{'id':'old','role':'assistant','parts':[{'type':'text','text':'старый'}]},
                {'id':'answer','timestamp':'2026-10-11T12:00:00Z','role':'assistant','text':'RAW SECRET',
                 'parts':[{'type':'reasoning','text':'PRIVATE REASONING'},{'type':'tool','input':'TOKEN', 'output':'secret'},
                          {'type':'text','text':'Проверяю сервис. token=secret-value '+('а'*800)}]},
                {'role':'tool','parts':[{'type':'text','text':'SECRET OUTPUT'}]},
                {'role':'assistant','text':'UNSAFE FALLBACK','parts':[{'type':'reasoning','text':'NO TEXT'}]}]}
        projection=safe_projection(details)
        self.assertEqual('answer',projection['assistant_id'])
        self.assertEqual('running',projection['status'])
        self.assertFalse(projection['history_complete'])
        self.assertLessEqual(len(projection['assistant_text']),500)
        for forbidden in ('RAW SECRET','PRIVATE REASONING','SECRET OUTPUT','UNSAFE FALLBACK','secret-value'):
            self.assertNotIn(forbidden,json.dumps(projection))

    def test_exact_native_harness_and_changed_projection_use_existing_queue(self):
        with tempfile.TemporaryDirectory() as root:
            center=NotificationCenter(Path(root)/'db',{'p':{'project':'fleet','max_severity':'critical'}},default_quiet_hours=[])
            self.addCleanup(center._connection.close)
            created=center.create_event('p','new',{'schema':'notify.event.v1','project':'fleet','recipient':'me','kind':'incident',
                'severity':'critical','title':'Проверка','dedup_key':'load','event_type':'health.degraded',
                'source_id':'host:load','host_id':'100','signal_type':'load','correlation_id':'episode'})
            center.record_health_agent_session(created['incident_id'],'native-start','','hermes','actual native id',
                'https://agent.bezrabotnyi.com',stage='health-diagnosis')
            urls=[]
            def read(request,**kwargs):
                urls.append(request.full_url)
                data=io.BytesIO(json.dumps({'session':{'status':'running'},'messages':[{'role':'assistant','parts':[
                    {'type':'text','text':'Ищу причину'}]}],'history':{'complete':False}}).encode());data.status=200
                return data
            reader=IncidentProgressReader(center,{'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','harness':'codex'},read)
            self.assertEqual(1,reader.run_once())
            self.assertEqual('http://127.0.0.1:18787/api/sessions/hermes/actual%20native%20id/details?limit=5&history=auto',urls[0])
            self.assertEqual(0,reader.run_once(),'15-second durable read bound')
            with center._connection: center._connection.execute('UPDATE incidents SET native_polled_at=0')
            self.assertEqual(0,reader.run_once(),'unchanged projection does not enqueue another edit')
            queued=center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE json_extract(target_json,'$.card_refresh')=1").fetchone()[0]
            self.assertEqual(1,queued)
