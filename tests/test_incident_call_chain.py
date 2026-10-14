"""Focused integration: ordered durable calls, no replay, exact budget authority.

No network/device/provider. Expected3s/emergency30s, CPU1/RAM512MiB/tasks128.
"""
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import patch
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import DeliveryWorker
from notification_center.incident_call_chain import ORDER, IncidentCallSender, receipt_projection

MISSED={'answered':False,'ended':True,'safe_to_fallback':True,'status':'unanswered'}


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.center=NotificationCenter(Path(self.temp.name)/'db',
            {'producer':{'project':'test','max_severity':'emergency'}},default_quiet_hours=[])
        self.calls=[]
        class Message:
            def send(this,payload): return {'message_id':17,'chat_id':'42'}
        class Sender:
            def __init__(this,name,result): this.name,this.result=name,result
            def send(this,payload,key):
                self.calls.append(this.name)
                if isinstance(this.result,Exception): raise this.result
                return this.result
        class Phone:
            can_phone_call=True
            def phone_call(this,payload): self.calls.append('phone');return {'callId':'fake-call'}
        self.matrix=Sender('matrix',dict(MISSED));self.telegram=Sender('telegram',dict(MISSED))
        self.worker=DeliveryWorker(self.center,Message(),matrix_call=self.matrix,
            telegram_call=self.telegram,android_phone=Phone(),incident_call_order=ORDER,
            android_phone_quiet_start_hour=0,android_phone_quiet_end_hour=0)
        self.event=dict(schema='notify.event.v1',project='test',recipient='me',kind='incident',
            severity='critical',title='Сервис недоступен',body='Требуется проверка',dedup_key='service')

    def tearDown(self): self.temp.cleanup()

    def start(self,event=None,key='initial'):
        created=self.center.create_event('producer',key,event or self.event)
        rows=self.center.claim_due_deliveries(now_epoch=10**12,channel_group='message')
        for row in rows:self.worker.deliver(row)
        return created['incident_id']

    def step(self):
        rows=self.center.claim_due_deliveries(now_epoch=10**12,channel_group='call')
        for row in rows:self.worker.deliver(row)
        return rows

    def rows(self): return self.center._connection.execute("SELECT * FROM deliveries WHERE channel LIKE '%.call' ORDER BY created_at,rowid").fetchall()

    def test_serial_fallback_uses_one_episode_and_retains_phone_ack_receipt(self):
        self.start();self.assertEqual(['matrix.call'],[r['channel'] for r in self.rows()])
        self.step();self.assertEqual(['matrix'],self.calls)
        self.step();self.assertEqual(['matrix','telegram'],self.calls)
        self.step();self.assertEqual(['matrix','telegram','phone'],self.calls)
        self.assertEqual(0,len(self.step()))
        self.assertEqual('fake-call',json.loads(self.rows()[-1]['result_json'])['callId'])

    def test_matrix_answer_stops_and_is_not_replaced_by_late_failure(self):
        self.matrix.result={'answered':True,'actor':'matrix:owner'}
        incident=self.start();first=self.step()[0]
        self.assertEqual('acknowledged',self.center.get_incident(incident)['state'])
        self.center.complete_delivery(first['id'],'failed',result=MISSED)
        self.assertEqual(['matrix'],self.calls)
        self.assertEqual(['matrix.call'],[r['channel'] for r in self.rows()])
        self.assertIs(True,json.loads(self.rows()[0]['result_json'])['answered'])

    def test_telegram_answer_stops_before_phone(self):
        self.telegram.result={'answered':True,'actor':'telegram:owner'}
        self.start();self.step();self.step();self.step()
        self.assertEqual(['matrix','telegram'],self.calls)

    def test_unknown_or_post_reservation_exception_never_advances_or_retries(self):
        for outcome in ({'answered':None,'uncertain':True},TimeoutError('lost ACK')):
            with self.subTest(outcome=type(outcome).__name__):
                self.matrix.result=outcome
                self.start(dict(self.event,dedup_key=str(len(self.calls))),key=str(len(self.calls)))
                self.step();self.assertEqual('uncertain',self.rows()[-1]['status'])
                self.assertEqual([],self.step())

    def test_false_without_teardown_proof_does_not_fallback(self):
        self.matrix.result={'answered':False}
        self.start();self.step();self.assertEqual(['matrix'],self.calls)
        self.assertEqual('uncertain',self.rows()[0]['status']);self.assertEqual([],self.step())

    def test_restart_continues_exact_next_stage_but_duplicate_completion_cannot_requeue(self):
        self.start();first=self.step()[0]
        self.center.complete_delivery(first['id'],'failed',result=MISSED)
        self.worker._center=NotificationCenter(Path(self.temp.name)/'db',
            {'producer':{'project':'test','max_severity':'emergency'}},default_quiet_hours=[])
        self.step();self.step();self.assertEqual(['matrix','telegram','phone'],self.calls)

    def test_new_episode_with_same_problem_is_limited_not_given_continuation_privilege(self):
        self.start();self.step();self.step();self.step()
        incident=self.center.get_incident(self.rows()[0]['incident_id'])
        self.center.resolve(incident['id'],'producer')
        self.start(key='new-episode');self.step()
        self.assertEqual(['matrix','telegram','phone'],self.calls)
        self.assertEqual('cancelled',self.rows()[-1]['status'])

    def test_ack_between_stages_cancels_next(self):
        incident=self.start();self.step();self.center.acknowledge_if_active(incident,'human')
        self.step();self.assertEqual(['matrix'],self.calls)

    def test_disabled_calls_cancel_root_without_other_channels(self):
        self.start();self.center.set_runtime_setting('automatic_calls_enabled','false')
        self.step();self.assertEqual([],self.calls);self.assertEqual('cancelled',self.rows()[0]['status'])

    def test_quiet_hours_stop_every_call_stage_before_external_reservation(self):
        # Focused integration expected1s/max10s: midnight must not reach an adapter.
        for index, channel in enumerate(ORDER):
            with self.subTest(channel=channel):
                incident = self.start(dict(self.event, dedup_key=channel), key=channel)
                for stage in range(index):
                    self.step()
                before = list(self.calls)
                self.center.set_runtime_setting('android_phone_quiet_start_hour', '0')
                self.center.set_runtime_setting('android_phone_quiet_end_hour', '12')
                with patch('notification_center.http_api.datetime') as clock:
                    clock.now.return_value = datetime(2026, 10, 15, 0, 40)
                    self.step()
                self.assertEqual(before, self.calls)
                self.assertEqual('cancelled', self.rows()[-1]['status'])
                self.assertEqual([], self.step())
                self.center.set_runtime_setting('android_phone_quiet_end_hour', '0')

    def test_emergency_can_override_global_quiet_hours(self):
        self.center.set_runtime_setting('android_phone_quiet_start_hour', '0')
        self.center.set_runtime_setting('android_phone_quiet_end_hour', '12')
        self.start(dict(self.event, severity='emergency'))
        with patch('notification_center.http_api.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 15, 0, 40)
            self.step()
        self.assertEqual(['matrix'], self.calls)

    def test_health_root_also_starts_matrix_not_phone(self):
        self.start(dict(self.event,event_type='health.degraded'))
        self.assertEqual(['matrix.call'],[r['channel'] for r in self.rows()])

    def test_projected_receipt_stays_valid_and_small(self):
        value=receipt_projection(dict(MISSED,actor='а'*10000,debug={'huge':'x'*100000}))
        encoded=json.dumps(value);self.assertLess(len(encoded),4000)
        self.assertTrue(json.loads(encoded)['safe_to_fallback'])

    def test_invalid_successor_never_acquires_an_ordinary_budget(self):
        for mode in ('zero_interval','missing_budget','expired_budget'):
            with self.subTest(mode=mode):
                self.start(dict(self.event,dedup_key=mode),key=mode)
                self.step()
                successor=self.rows()[-1]
                target=json.loads(successor['target_json'])
                target['incident_call_chain']['previous']='wrong-delivery'
                self.center._connection.execute('UPDATE deliveries SET target_json=? WHERE id=?',
                    (json.dumps(target),successor['id']))
                if mode=='zero_interval':self.center.set_runtime_setting('call_repeat_min_interval_seconds','0')
                if mode=='missing_budget':self.center._connection.execute('DELETE FROM call_problem_budgets')
                if mode=='expired_budget':self.center._connection.execute('UPDATE call_problem_budgets SET last_attempt_at=0')
                self.center._connection.commit()
                before=list(self.calls);self.step()
                self.assertEqual(before,self.calls)
                self.assertEqual('cancelled',self.rows()[-1]['status'])

    def test_call_receipts_are_not_telegram_message_migration_sources(self):
        self.start();self.step();self.step();self.step()
        for row in self.rows():
            self.center._connection.execute("UPDATE deliveries SET status='sent' WHERE id=?",(row['id'],))
            self.center._connection.commit()
            with self.assertRaises(ValidationError):
                self.center.record_health_delivery_migrated(row['id'],{'message_id':17})

    def test_malformed_chain_cancels_without_legacy_dispatch(self):
        for target in (None, [], 'invalid', {'incident_call_chain':{'index':99}}):
            with self.subTest(target=target):
                self.start(dict(self.event,dedup_key=str(target)),key=str(target))
                row=self.rows()[-1]
                self.center._connection.execute('UPDATE deliveries SET target_json=? WHERE id=?',
                    (json.dumps(target),row['id']))
                self.center._connection.commit();self.step()
                self.assertEqual('cancelled',self.rows()[-1]['status']);self.assertEqual([],self.calls)

    def test_legacy_matrix_unknown_never_schedules_neighbours(self):
        class Unknown:
            def send(this,payload):return {'answered':None,'uncertain':True}
        self.worker._matrix_call=Unknown()
        created=self.center.create_event('producer','legacy',self.event)
        for row in self.center.claim_due_deliveries(now_epoch=10**12,channel_group='message'):
            self.center.complete_delivery(row['id'],'sent',result={'message_id':17,'chat_id':'42'})
        self.center.schedule_escalation(created['incident_id'],'matrix.call',0)
        self.step();self.assertEqual('uncertain',self.rows()[-1]['status'])
        self.assertEqual(1,len(self.rows()));self.assertEqual([],self.step())


class SenderTests(unittest.TestCase):
    payload={'incident':{'id':'incident-test','title':'Проверка','body':'Связь','project':'test','severity':'critical'}}

    def test_legacy_matrix_signature_keeps_legacy_request_identity(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"ok":true,"answered":true,"target":"owner","channel":"matrix"}'
        requests=[]
        def run(request,timeout):requests.append(json.loads(request.data));return Response()
        result=IncidentCallSender('http://bridge','test',runner=run).send(self.payload)
        self.assertTrue(result['answered']);self.assertNotIn('delivery_key',requests[0])

    def test_only_proven_pre_call_unavailability_allows_fallback(self):
        for error in (urllib.error.HTTPError('http://bridge',401,'denied',{},None),
                      urllib.error.URLError(ConnectionRefusedError())):
            def run(*args,**kwargs):raise error
            result=IncidentCallSender('http://bridge','test',runner=run).send(self.payload,'test-key')
            self.assertIs(result['safe_to_fallback'],True)
        for error in (TimeoutError('lost ACK'),urllib.error.HTTPError('http://bridge',500,'failed',{},None)):
            def run(*args,**kwargs):raise error
            with self.assertRaises(type(error)):
                IncidentCallSender('http://bridge','test',runner=run).send(self.payload,'test-key')


def test_telegram_http_receipt_wait_covers_extended_native_startup():
    """Fast unit expected1s/max10s: cannot lose the receipt before UserIO535s bound."""
    sender=IncidentCallSender('https://example.invalid/v1/calls/telegram/noticeplace','test',450)
    assert sender.timeout > 535
    matrix=IncidentCallSender('https://example.invalid/v1/calls/matrix/noticeplace','test',450)
    assert matrix.timeout==450
