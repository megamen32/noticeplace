"""Focused integration: typed producer identity survives HTTP/DB/card truncation.

Detects generic long-summary misattribution and accidental execution authority.
Expected 2s per consumer scenario, maximum 8s; fast validation/render <1s/max3s.
"""
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError
from notification_center.http_api import DeliveryWorker, TelegramSender, build_handler
from notification_center.incident_card import render
from notification_center.telegram_interactions import TelegramActionCodec


class ExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'notice.sqlite3'
        self.tokens={'p':{'project':'hermes','max_severity':'critical','agent_jobs':['health-diagnosis']}}
        self.center=NotificationCenter(self.path,self.tokens,default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.codec=TelegramActionCodec('x'*32)
        self.identity={'harness':'opencode','session_id':'ses_actual_repair','model':'minimax-coding-plan/MiniMax-M3.1-Flash-Preview',
                       'cwd':'/home/roomhacker/ServersAdministartion',
                       'session_url':'https://agent.bezrabotnyi.com/#/session/opencode%3Ases_actual_repair'}
        self.event={'schema':'notify.event.v1','project':'hermes','recipient':'me','kind':'notification','severity':'important',
                    'title':'Работа по сервису завершена','body':'Фактический результат ремонта. '*250,
                    'dedup_key':'agent:actual:done','event_type':'agent.done','execution_identity':self.identity}

    def test_long_generic_event_http_db_restart_and_worker_keep_actual_executor(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(self.center,'health-token','mcp-token'))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/events',method='POST',
            data=json.dumps(self.event).encode(),headers={'Authorization':'Bearer p','Idempotency-Key':'generic-long','Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:created=json.load(response)
        reopened=NotificationCenter(self.path,self.tokens,default_quiet_hours=[]);self.addCleanup(reopened._connection.close)
        calls=[]
        def network(request,**_kwargs):
            if not request.full_url.startswith('https://api.telegram.org/'):
                raise AssertionError('display identity must never contact native/executor')
            data={k:v[0] for k,v in urllib.parse.parse_qs(request.data.decode()).items()}
            method=request.full_url.rsplit('/',1)[-1];calls.append((method,data))
            response=io.BytesIO(json.dumps({'ok':True,'result':{'message_id':42,'chat':{'id':-100}}}).encode());response.status=200
            return response
        worker=DeliveryWorker(reopened,TelegramSender('fake','-100',action_codec=self.codec,center=reopened))
        with patch('notification_center.http_api.urllib.request.urlopen',side_effect=network):
            for delivery in worker.claim_due(limit=5,channel_group='message'):worker.deliver(delivery)
            self.assertEqual(1,len(calls))
            initial=calls[0][1];markup=json.loads(initial['reply_markup']);buttons=[b for row in markup['inline_keyboard'] for b in row]
            self.assertIn('Исполнитель: OpenCode',initial['text']);self.assertIn(self.identity['model'],initial['text'])
            self.assertIn(self.identity['cwd'],initial['text']);self.assertIn('Проект уведомления: hermes',initial['text'])
            self.assertNotIn('Уведомление · hermes',initial['text'])
            self.assertNotIn('Инцидент принят',initial['text'])
            self.assertNotIn('Проверка и исправление выполняются',initial['text'])
            self.assertIn('Сессия сообщила о завершении работы',initial['text'])
            self.assertFalse(any(b.get('callback_data','').startswith('n:ai:') for b in buttons))
            self.assertTrue(any(b.get('url')==self.identity['session_url'] for b in buttons))
            self.assertFalse(any(b.get('text')=='✅ ИИ запущен' for b in buttons),'display metadata is not a launch receipt')
            payload=reopened.incident_card_payload(created['incident_id'])
            self.assertEqual(self.identity,payload['execution_identity']);self.assertFalse(payload.get('ai_session'))
            self.assertIsNone(reopened.latest_agent_session(created['incident_id']))
            self.assertEqual(0,reopened._connection.execute("SELECT COUNT(*) FROM deliveries WHERE channel LIKE 'gptadmin.agent:%'").fetchone()[0])
            reopened.create_event('p','generic-update',{**self.event,'body':'Последнее проверенное состояние. '*250})
            reopened.queue_incident_card_refresh(created['incident_id'],now=time.time()+16)
            with patch('notification_center.core.time.time',return_value=time.time()+17):
                for delivery in worker.claim_due(limit=5,channel_group='message'):worker.deliver(delivery)
        self.assertEqual(1,sum(method=='sendMessage' for method,_ in calls))
        self.assertEqual(1,sum(method=='editMessageText' for method,_ in calls))
        self.assertIn('Последнее проверенное состояние.',calls[-1][1]['text'])
        self.assertTrue(all(data['message_id']=='42' for method,data in calls if method=='editMessageText'))
        self.assertIn('Исполнитель: OpenCode',calls[-1][1]['text'])
        self.assertTrue(any(b.get('url')==self.identity['session_url'] for row in json.loads(calls[-1][1]['reply_markup'])['inline_keyboard'] for b in row))

    def test_metadata_has_no_event_authority_and_rejects_invalid_identity(self):
        invalid=[None,[],{}, {**self.identity,'harness':'project-hermes'}, {**self.identity,'session_id':'bad\nID'},
                 {**self.identity,'session_url':'https://agent.bezrabotnyi.com/#/session/codex%3Asomeone-else'},
                 {**self.identity,'session_url':'https://other.example/#/session/opencode%3Ases_actual_repair'},
                 {**self.identity,'cwd':'relative/path'}, {**self.identity,'model':'x'*257},
                 {**self.identity,'command':'reboot'}, {**self.identity,'token':'never-accept'}]
        for index,identity in enumerate(invalid):
            with self.subTest(identity=identity),self.assertRaises(ValidationError):
                self.center.create_event('p','bad-'+str(index),{**self.event,'execution_identity':identity})
        with self.assertRaises(AuthorizationError):
            self.center.create_event('p','foreign',{**self.event,'project':'autosell'})
        with self.assertRaises(ValidationError):
            self.center.create_event('p','authority',{**self.event,'command':'reboot'})
        self.assertEqual(0,self.center._connection.execute('SELECT COUNT(*) FROM events').fetchone()[0])

    def test_legacy_is_unknown_and_metadata_without_url_does_not_invent_link(self):
        legacy={key:value for key,value in self.event.items() if key!='execution_identity'}
        legacy['body']='OpenCode ses_actual_repair says done. '+legacy['body']
        created=self.center.create_event('p','legacy',legacy)
        text,keyboard=render(self.center.incident_card_payload(created['incident_id']),self.codec)
        self.assertIn('Исполнитель не указан',text)
        self.assertFalse(any('url' in b for row in keyboard['inline_keyboard'] for b in row))

    def test_producer_encode_uri_component_preserves_exact_native_identity(self):
        identity={**self.identity,'session_id':"ses_!'()*",'session_url':"https://agent.bezrabotnyi.com/#/session/opencode%3Ases_!'()*"}
        created=self.center.create_event('p','encoded-native',{**self.event,'execution_identity':identity})
        text,keyboard=render(self.center.incident_card_payload(created['incident_id']),self.codec)
        self.assertIn('Исполнитель: OpenCode',text)
        self.assertTrue(any(b.get('url')==identity['session_url'] for row in keyboard['inline_keyboard'] for b in row))
        identity={key:value for key,value in self.identity.items() if key!='session_url'}
        new=self.center.create_event('p','no-url',{**self.event,'dedup_key':'no-url','execution_identity':identity})
        text,keyboard=render(self.center.incident_card_payload(new['incident_id']),self.codec)
        self.assertIn('Исполнитель: OpenCode',text)
        self.assertFalse(any('url' in b for row in keyboard['inline_keyboard'] for b in row))

    def test_bound_health_receipt_outranks_display_metadata_and_keeps_progress(self):
        created=self.center.create_event('p','bound',{**self.event,'event_type':'health.degraded','kind':'incident',
            'source_id':'actual-source','host_id':'100','signal_type':'service_runtime','correlation_id':'bound-episode'})
        self.center.record_health_agent_session(created['incident_id'],'native-bound','repair','codex','actual-bound',
            'https://agent.bezrabotnyi.com',stage='health-diagnosis',model='actual-bound-model')
        payload=self.center.incident_card_payload(created['incident_id'])
        payload['native_progress']={'status':'running','assistant_text':'Чиню реального потребителя.'}
        text,keyboard=render(payload,self.codec)
        self.assertIn('Исполнитель: Codex',text);self.assertIn('actual-bound-model',text)
        self.assertNotIn('Исполнитель: OpenCode',text);self.assertIn('Чиню реального потребителя.',text)
        buttons=[b for row in keyboard['inline_keyboard'] for b in row]
        self.assertTrue(any(b.get('url','').endswith('codex%3Aactual-bound') and b['text']=='✅ ИИ запущен' for b in buttons))
        self.assertFalse(any(b.get('url')==self.identity['session_url'] for b in buttons))

    def test_notification_keeps_ack_mute_url_choices_and_does_not_guess_done_from_prose(self):
        """Fast unit real stored events: informational type/controls (<1s/max3s)."""
        for event_type in ('agent.done','agent.legacy'):
            with self.subTest(event_type=event_type):
                created=self.center.create_event('p','info-'+event_type,{**self.event,'event_type':event_type,
                    'dedup_key':'info-'+event_type,'severity':'critical','title':'Работа завершена',
                    'body':'Сессия завершила работу, исправила проблему.','choice_request_id':'question-'+event_type,
                    'choices':[{'choice_id':'read','label':'Посмотрел'},{'choice_id':'details','label':'Нужны подробности'}]})
                payload=self.center.incident_card_payload(created['incident_id'])
                text,keyboard=render(payload,self.codec)
                buttons=[b for row in keyboard['inline_keyboard'] for b in row]
                self.assertNotIn('Проверка и исправление выполняются',text)
                self.assertFalse(any(b.get('callback_data','').startswith('n:ai:') for b in buttons))
                self.assertTrue(any(b.get('callback_data','').startswith('n:ack:') for b in buttons))
                self.assertTrue(any(b.get('callback_data','').startswith('n:mute:') for b in buttons))
                self.assertEqual(2,sum(b.get('callback_data','').startswith('n:choice:') for b in buttons))
                self.assertTrue(any(b.get('url')==self.identity['session_url'] for b in buttons))
                if event_type=='agent.done':self.assertIn('Сессия сообщила о завершении работы',text)
                else:
                    self.assertIn('Текущее состояние сессии не указано',text)
                    self.assertNotIn('Сессия сообщила о завершении работы',text)

                self.center.acknowledge(created['incident_id'],'operator:test')
                text,keyboard=render(self.center.incident_card_payload(created['incident_id']),self.codec)
                self.assertIn('Вы прочитали уведомление.',text)
                self.assertNotIn('Проверка и исправление продолжаются',text)
                self.assertTrue(any(b.get('text')=='✅ Прочитал' for row in keyboard['inline_keyboard'] for b in row))
