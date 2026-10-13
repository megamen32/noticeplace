"""Focused integration: one canonical create across early/late delivery leases.

Purpose: reproduce the status-before-initial duplicate and unknown-create gap.
Expected 2 seconds; maximum 10 seconds. The real worker/sender use a fake network.
"""
import io
import json
import tempfile
import time
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec


class IncidentCardDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name) / 'notice.sqlite3',
            {'p': {'project': 'fleet-health', 'max_severity': 'emergency', 'agent_jobs': ['health-diagnosis']}},
            default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        created = self.center.create_event('p', 'one-card', {
            'schema':'notify.event.v1', 'project':'fleet-health', 'recipient':'me', 'kind':'incident',
            'severity':'critical', 'title':'Нагрузка сервера', 'body':'Проверяется загрузка', 'dedup_key':'health:load:host',
            'event_type':'health.degraded', 'source_id':'host:load', 'host_id':'host', 'signal_type':'load',
            'correlation_id':'load-episode-1', 'source_fingerprint':'fp-1'})
        self.incident_id = created['incident_id']; self.initial_id = created['initial_delivery_id']
        self.session = self.center.record_health_agent_session(self.incident_id, 'session-start', 'health-diagnosis',
            'codex', 'native-one', 'https://agent.bezrabotnyi.com', stage='health-diagnosis')
        self.status_id = self.session['session_delivery_id']
        self.calls=[]
        codec=TelegramActionCodec('x'*32)
        self.worker=DeliveryWorker(self.center, TelegramSender('fake-token', '-100', action_codec=codec, center=self.center,
            severity_routes={'health': {'chat_id': '-100', 'message_thread_id': 324}}))
        def network(request, **kwargs):
            payload={k:v[0] for k,v in urllib.parse.parse_qs(request.data.decode()).items()}
            method=request.full_url.rsplit('/',1)[-1]; self.calls.append((method,payload))
            message_id=len([m for m,_ in self.calls if m=='sendMessage']) if method=='sendMessage' else int(payload['message_id'])
            response=io.BytesIO(json.dumps({'ok':True,'result':{'chat':{'id':-100},'message_id':message_id}}).encode())
            response.status=200
            return response
        patcher=patch('notification_center.http_api.urllib.request.urlopen', side_effect=network)
        patcher.start(); self.addCleanup(patcher.stop)

    def claim(self, delivery_id):
        # Represents a legacy lease already claimed before deployment; exercise
        # the real final consumer fence, rather than the current claim coalescer.
        with self.center._lock,self.center._connection:
            self.center._connection.execute("UPDATE deliveries SET status='claimed',claimed_at=?,attempt=attempt+1 WHERE id=?",
                                            (time.time(),delivery_id))
            return dict(self.center._connection.execute('SELECT * FROM deliveries WHERE id=?',(delivery_id,)).fetchone())

    def test_status_before_initial_creates_one_message(self):
        self.worker.deliver(self.claim(self.status_id))
        self.worker.deliver(self.claim(self.initial_id))
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))
        self.assertTrue(all(p.get('message_id')=='1' for m,p in self.calls if m=='editMessageText'))
        self.assertEqual(1,self.center.original_telegram_card(self.incident_id)['message_id'])

    def test_concurrent_initial_and_status_have_one_creation_fence(self):
        claims=[self.claim(self.initial_id),self.claim(self.status_id)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.worker.deliver,claims))
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))

    def test_two_database_connections_share_the_creation_fence(self):
        other = NotificationCenter(Path(self.temp.name) / 'notice.sqlite3',
            {'p': {'project': 'fleet-health', 'max_severity': 'emergency'}},default_quiet_hours=[])
        self.addCleanup(other._connection.close)
        second = DeliveryWorker(other, self.worker._telegram)
        claims=[self.claim(self.initial_id),self.claim(self.status_id)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker.deliver,claim) for worker,claim in zip((self.worker,second),claims)]
            for future in futures: future.result()
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))

    def test_latest_burst_flush_survives_restart_and_repairs_are_not_limited(self):
        self.worker.deliver(self.claim(self.initial_id))
        self.worker.deliver(self.claim(self.status_id))
        self.assertEqual(1,len(self.calls),'automatic edits wait at least 15 seconds')
        with self.center._lock,self.center._connection:
            for text in ('первый ответ','последний ответ'):
                self.center._connection.execute('UPDATE incidents SET native_projection_json=? WHERE id=?',
                    (json.dumps({'status':'running','assistant_text':text}),self.incident_id))
                self.center.queue_incident_card_refresh(self.incident_id)
        self.assertEqual(1,self.center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE json_extract(target_json,'$.card_refresh')=1").fetchone()[0])
        other=NotificationCenter(Path(self.temp.name)/'notice.sqlite3',{'p':{'project':'fleet-health','max_severity':'emergency'}},default_quiet_hours=[])
        self.addCleanup(other._connection.close)
        worker=DeliveryWorker(other,self.worker._telegram)
        future=time.time()+16
        with patch('notification_center.core.time.time',return_value=future):
            for delivery in worker.claim_due(limit=8,channel_group='message'): worker.deliver(delivery)
        self.assertEqual(1,sum(m=='editMessageText' for m,_ in self.calls))
        self.assertIn('последний ответ',self.calls[-1][1]['text'])
        self.assertNotIn('первый ответ',self.calls[-1][1]['text'])
        with patch('notification_center.core.time.time',return_value=future+16):
            for delivery in worker.claim_due(limit=8,channel_group='message'): worker.deliver(delivery)
        self.assertEqual(1,sum(m=='editMessageText' for m,_ in self.calls),'identical latest projection is coalesced without another edit')
        self.assertEqual(1,len(worker.claim_due(limit=2,channel_group='agent')))

    def test_grouping_preserves_individual_repairs_and_does_not_ack_every_host(self):
        # An explicit issue family permits grouping across hosts. Other checks
        # on the same signal/project remain separate without that identity.
        event={'schema':'notify.event.v1','project':'fleet-health','recipient':'me','kind':'incident','severity':'critical',
               'title':'Сервер перегружен','event_type':'health.degraded','signal_type':'load','issue_family':'cpu-pressure'}
        members=[]
        for host in ('100','88'):
            created=self.center.create_event('p','host-'+host,{**event,'dedup_key':'load:'+host,'source_id':'host:'+host,
                'host_id':host,'correlation_id':host+'-episode'})
            members.append(created)
        self.worker.deliver(self.claim(members[0]['initial_delivery_id']))
        self.worker.deliver(self.claim(members[1]['initial_delivery_id']))
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))
        payload=self.center.incident_card_payload(members[1]['incident_id'])
        self.assertEqual({'100','88'}, {m['host_id'] for m in payload['group_members']})
        self.assertEqual(members[0]['incident_id'],self.center.telegram_card_owner(members[1]['incident_id']))
        self.center.acknowledge(members[0]['incident_id'],'human')
        self.assertEqual('open',self.center.get_incident(members[1]['incident_id'])['state'])
        self.assertEqual(3,len(self.center.claim_due_deliveries(channel_group='agent')))
        self.center.set_incident_notifications_muted(members[0]['incident_id'],True,'human')
        self.assertTrue(self.center.get_incident(members[1]['incident_id'])['notifications_muted'])
        distinct=self.center.create_event('p','different',{**event,'issue_family':'disk-pressure','signal_type':'disk',
            'dedup_key':'disk:88','source_id':'host:88','host_id':'88','correlation_id':'disk-episode'})
        self.assertEqual(distinct['incident_id'],self.center.telegram_card_owner(distinct['incident_id']))
        self.assertFalse(self.center.get_incident(distinct['incident_id'])['notifications_muted'])

    def test_sound_is_explicit_for_new_messages_and_never_on_edits(self):
        self.worker.deliver(self.claim(self.initial_id))
        self.assertEqual('false',self.calls[0][1]['disable_notification'])
        with patch('notification_center.core.time.time',return_value=time.time()+16):
            self.worker.deliver(self.claim(self.status_id))
        for method,payload in self.calls:
            if method=='editMessageText': self.assertNotIn('disable_notification',payload)
        ordinary=self.center.create_event('p','routine',{'schema':'notify.event.v1','project':'fleet-health','recipient':'me',
            'kind':'incident','severity':'notice','title':'Обычное уведомление','dedup_key':'routine'})
        self.worker.deliver(self.claim(ordinary['initial_delivery_id']))
        self.assertEqual('true',self.calls[-1][1]['disable_notification'])

    def test_group_recovery_is_partial_in_both_member_orders_and_uses_same_card(self):
        """Focused integration: render + actual recovery delivery; <1s / max5s."""
        from notification_center.incident_card import render
        event={'schema':'notify.event.v1','project':'fleet-health','recipient':'me','kind':'incident','severity':'critical',
               'title':'Сервер перегружен','event_type':'health.degraded','signal_type':'load','issue_family':'group-recovery'}
        for recovered_host in ('100','88'):
            with self.subTest(recovered_host=recovered_host):
                members=[]
                for host in ('100','88'):
                    full={**event,'issue_family':'group-recovery-'+recovered_host,'dedup_key':recovered_host+':load:'+host,
                        'source_id':'host:'+host,'host_id':host,'correlation_id':recovered_host+':episode:'+host}
                    created=self.center.create_event('p',recovered_host+':host:'+host,full)
                    members.append((host,full,created))
                self.worker.deliver(self.claim(members[0][2]['initial_delivery_id']))
                canonical=self.center.original_telegram_card(members[0][2]['incident_id'])['message_id']
                host,full,recovered=next(item for item in members if item[0]==recovered_host)
                self.center.resolve_event('p','group-recover:'+recovered_host,{**full,'action':'resolve','event_type':'health.recovered'})
                recovery=self.center._connection.execute("SELECT id FROM deliveries WHERE incident_id=? AND json_extract(target_json,'$.health_outcome.status')='recovered'",(recovered['incident_id'],)).fetchone()[0]
                with patch('notification_center.core.time.time',return_value=time.time()+16): self.worker.deliver(self.claim(recovery))
                self.assertEqual(str(canonical),self.calls[-1][1]['message_id'])
                for _,_,member in members:
                    payload=self.center.incident_card_payload(member['incident_id'])
                    text,_=render(payload,self.worker._telegram._action_codec)
                    self.assertIn('Часть сервисов восстановилась',text)
                    self.assertIn('открытых инцидентов: 1',text)
                    self.assertNotIn('действий от вас не требуется',text)
                    self.assertIn('— проверяется',text)
                    self.assertIn('— восстановлен',text)
                    self.assertEqual(2,len({m['id'] for m in payload['group_members']}))
                active=next(item[2]['incident_id'] for item in members if item[0]!=recovered_host)
                self.assertEqual('open',self.center.get_incident(active)['state'])
        self.assertEqual(2,sum(method=='sendMessage' for method,_ in self.calls),'one original card per distinct family')

    def test_closed_parent_group_ack_targets_only_open_critical_child(self):
        """Focused integration: canonical keyboard -> real callback; <1s/max5s."""
        from notification_center.incident_card import render
        from notification_center.telegram_interactions import TelegramInteractionPoller
        event={'schema':'notify.event.v1','project':'fleet-health','recipient':'me','kind':'incident','severity':'critical',
               'title':'Сервер перегружен','event_type':'health.degraded','signal_type':'load','issue_family':'group-ack'}
        members=[]
        for host in ('100','88','44'):
            full={**event,'dedup_key':'group-ack:'+host,'source_id':'host:'+host,'host_id':host,'correlation_id':'ack-episode:'+host}
            members.append((full,self.center.create_event('p','group-ack:'+host,full)))
        parent=members[0][1]['incident_id'];child=members[1][1]['incident_id'];next_child=members[2][1]['incident_id']
        self.worker.deliver(self.claim(members[0][1]['initial_delivery_id']))
        card=self.center.original_telegram_card(parent)
        self.center.resolve_event('p','group-ack-recovery',{**members[0][0],'action':'resolve','event_type':'health.recovered'})
        recovery=self.center._connection.execute("SELECT id FROM deliveries WHERE incident_id=? AND json_extract(target_json,'$.health_outcome.status')='recovered'",(parent,)).fetchone()[0]
        self.worker.deliver(self.claim(recovery))
        payload=self.center.incident_card_payload(parent)
        self.assertEqual({'critical'},{m['severity'] for m in payload['group_members']})
        text,keyboard=render(payload,self.worker._telegram._action_codec)
        ack=next(b for row in keyboard['inline_keyboard'] for b in row if b.get('callback_data','').startswith('n:ack:'))
        self.assertEqual(('ack',child),self.worker._telegram._action_codec.decode(ack['callback_data']))
        self.assertIn('88',ack['text'])
        callbacks=[]
        def api(method,body): callbacks.append((method,body));return {'ok':True,'result':True}
        poller=TelegramInteractionPoller(self.center,'fake-token',{'42'},self.worker._telegram._action_codec,api=api)
        poller._handle_callback({'id':'group-ack','from':{'id':42},'data':ack['callback_data'],
            'message':{'chat':{'id':int(card['chat_id'])},'message_id':card['message_id'],'text':text}})
        self.assertEqual('resolved',self.center.get_incident(parent)['state'])
        self.assertEqual('acknowledged',self.center.get_incident(child)['state'])
        self.assertEqual('open',self.center.get_incident(next_child)['state'])
        edit=next(body for method,body in callbacks if method=='editMessageReplyMarkup')
        self.assertEqual(str(card['message_id']),edit['message_id'])
        next_ack=next(b for row in json.loads(edit['reply_markup'])['inline_keyboard'] for b in row if b.get('callback_data','').startswith('n:ack:'))
        self.assertEqual(('ack',next_child),self.worker._telegram._action_codec.decode(next_ack['callback_data']))
        self.assertFalse(any(method=='sendMessage' for method,_ in callbacks))
        self.assertEqual(1,sum(method=='sendMessage' for method,_ in self.calls))

    def test_unknown_early_creation_never_allows_a_new_send(self):
        with self.center._lock,self.center._connection:
            self.center._connection.execute("UPDATE deliveries SET status='uncertain',result_json=NULL WHERE id=?",(self.status_id,))
        self.worker.deliver(self.claim(self.initial_id))
        self.assertEqual([],self.calls)
        self.assertEqual('open',self.center.get_incident(self.incident_id)['state'])
        agent=self.center._connection.execute("SELECT status FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()
        self.assertEqual('queued',agent['status'],'Telegram uncertainty must not stop repair admission')

    def test_recovery_during_initial_transport_retains_the_first_actual_receipt(self):
        network=self.worker._telegram.send
        def close_during_send(payload):
            self.center.resolve_event('p','racing-probe',{'schema':'notify.event.v1','project':'fleet-health','recipient':'me',
                'dedup_key':'health:load:host','action':'resolve','event_type':'health.recovered','source_id':'host:load',
                'host_id':'host','signal_type':'load','correlation_id':'load-episode-1'})
            return network(payload)
        with patch.object(self.worker._telegram,'send',side_effect=close_during_send):
            self.worker.deliver(self.claim(self.initial_id))
        self.assertEqual(1,self.center.original_telegram_card(self.incident_id)['message_id'])
        for delivery in self.worker.claim_due(channel_group='message'): self.worker.deliver(delivery)
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))
        self.assertIn('Состояние восстановилось',self.calls[-1][1]['text'])

    def test_late_legacy_plans_cannot_replace_independently_closed_card(self):
        self.worker.deliver(self.claim(self.status_id))
        with self.center._lock,self.center._connection:
            late=self.center._schedule_delivery(self.incident_id,'telegram.main','legacy-late-plans',time.time(),
                {'health_outcome':{'status':'plans_ready','reason':'opaque English diagnostic'}})
        claimed=self.claim(late)
        self.center.resolve_event('p','independent-probe', {'schema':'notify.event.v1','project':'fleet-health','recipient':'me','dedup_key':'health:load:host','action':'resolve','event_type':'health.recovered','source_id':'host:load','host_id':'host','signal_type':'load','correlation_id':'load-episode-1'})
        self.worker.deliver(claimed)
        self.assertEqual('cancelled',self.center._connection.execute('SELECT status FROM deliveries WHERE id=?',(late,)).fetchone()['status'])
        with self.center._lock,self.center._connection:
            self.center._connection.execute('UPDATE incidents SET telegram_card_updated_at=0 WHERE id=?',(self.incident_id,))
        recovery=self.center._connection.execute("SELECT id FROM deliveries WHERE json_extract(target_json,'$.health_outcome.status')='recovered'").fetchone()['id']
        self.worker.deliver(self.claim(recovery))
        self.assertEqual(1,sum(m=='sendMessage' for m,_ in self.calls))
        latest=self.calls[-1][1]
        self.assertEqual('1',latest['message_id'])
        self.assertNotIn('выберите план',latest['text'].lower())
        self.assertNotIn('opaque English',latest['text'])
        self.assertEqual('resolved',self.center.get_incident(self.incident_id)['state'])
