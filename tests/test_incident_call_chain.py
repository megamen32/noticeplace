"""Focused integration: ordered durable calls, no replay, exact budget authority.

No network/device/provider. Expected3s/emergency30s, CPU1/RAM512MiB/tasks128.
"""
import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import patch
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import DeliveryWorker
from notification_center.incident_call_chain import (
    HTTP_ERROR_BODY_LIMIT,
    ORDER,
    IncidentCallSender,
    receipt_projection,
)

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

    def test_answered_matrix_call_stops_chain_even_without_spoken_ack(self):
        self.matrix.result={'ok':True,'answered':True,'acknowledged':False,'ended':False,
            'safe_to_fallback':False,'status':'active','target':'owner','channel':'matrix','actor':'matrix:owner'}
        self.start();self.step()
        self.assertEqual('sent',self.rows()[0]['status'])
        self.assertEqual(['matrix'],self.calls)
        self.assertEqual(1,len(self.rows()))

    def test_answered_matrix_call_with_explicit_ack_stops_fallback(self):
        self.matrix.result={'ok':True,'answered':True,'acknowledged':True,'ended':False,
            'safe_to_fallback':False,'status':'active','target':'owner','channel':'matrix','actor':'matrix:owner'}
        self.start();self.step()
        self.assertEqual(['matrix'],self.calls)
        self.assertEqual('sent',self.rows()[0]['status'])
        self.assertEqual(1,len(self.rows()))

    def test_matrix_answer_stops_and_is_not_replaced_by_late_failure(self):
        self.matrix.result={'answered':True,'acknowledged':True,'actor':'matrix:owner'}
        incident=self.start();first=self.step()[0]
        self.assertEqual('acknowledged',self.center.get_incident(incident)['state'])
        self.center.complete_delivery(first['id'],'failed',result=MISSED)
        self.assertEqual(['matrix'],self.calls)
        self.assertEqual(['matrix.call'],[r['channel'] for r in self.rows()])
        self.assertIs(True,json.loads(self.rows()[0]['result_json'])['answered'])

    def test_telegram_answer_stops_before_phone(self):
        self.telegram.result={'answered':True,'acknowledged':True,'actor':'telegram:owner'}
        self.start();self.step();self.step();self.step()
        self.assertEqual(['matrix','telegram'],self.calls)

    def test_unknown_or_post_reservation_exception_never_advances_or_retries(self):
        for outcome in ({'answered':None,'uncertain':True},TimeoutError('lost ACK')):
            with self.subTest(outcome=type(outcome).__name__):
                self.matrix.result=outcome
                self.start(dict(self.event,dedup_key=str(len(self.calls))),key=str(len(self.calls)))
                self.step();self.assertEqual('uncertain',self.rows()[-1]['status'])
                self.assertEqual([],self.step())

    def test_http_error_receipt_is_diagnostic_but_stays_uncertain_without_successor(self):
        body=json.dumps({
            'error':'provider_unavailable',
            'code':'UPSTREAM_BUSY',
            'message':'Bearer secret-token; caller +1 (202) 555-0147; token=private-token; opaque AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
            'access_token':'private-token',
            'phone_number':'+1 (202) 555-0147',
        }).encode()
        error=urllib.error.HTTPError('http://bridge',502,'bad gateway',{},io.BytesIO(body))
        def run(*args,**kwargs): raise error
        self.worker._matrix_call=IncidentCallSender('http://bridge','test',runner=run)

        incident=self.start()
        self.step()

        matrix=self.rows()
        self.assertEqual(1,len(matrix))
        self.assertEqual('matrix.call',matrix[0]['channel'])
        self.assertEqual('uncertain',matrix[0]['status'])
        receipt=json.loads(matrix[0]['result_json'])
        self.assertEqual(502,receipt['http_status'])
        self.assertTrue(receipt['uncertain'])
        self.assertIsNone(receipt['answered'])
        self.assertEqual({'code':'UPSTREAM_BUSY','error':'provider_unavailable',
                          'message':'Bearer [redacted]; caller [number redacted]; [secret redacted]; opaque [token redacted]'},
                         receipt['response_metadata'])
        serialized=json.dumps(receipt)
        for private in (body.decode(),'secret-token','private-token','202','555-0147','A'*40):
            self.assertNotIn(private,serialized)
        self.assertEqual([],self.step())
        self.assertEqual([],self.calls)
        self.assertEqual('open',self.center.get_incident(incident)['state'])

    def test_matrix_active_call_conflict_is_confirmed_pre_admission_and_chain_continues(self):
        error=urllib.error.HTTPError(
            'http://userio/v1/calls/matrix/noticeplace',400,'bad request',{},
            io.BytesIO(json.dumps({'error':'another Matrix voice call is active'}).encode()),
        )
        def reject(*args,**kwargs):
            self.calls.append('matrix')
            raise error
        self.worker._matrix_call=IncidentCallSender(
            'http://userio/v1/calls/matrix/noticeplace','test',runner=reject)

        self.start()
        self.step()
        self.assertEqual('failed',self.rows()[0]['status'])
        self.assertTrue(json.loads(self.rows()[0]['result_json'])['safe_to_fallback'])
        self.assertEqual(['matrix'],self.calls)

        self.telegram.result=dict(MISSED)
        self.step()
        self.assertEqual(['matrix','telegram'],self.calls)
        self.assertEqual(['matrix.call','telegram.call','android.phone.call'],
                         [row['channel'] for row in self.rows()])
        self.assertEqual('queued',self.rows()[-1]['status'])

        self.step()
        self.assertEqual(['matrix','telegram','phone'],self.calls)

    def test_other_matrix_http_400_remains_uncertain(self):
        error=urllib.error.HTTPError(
            'http://userio/v1/calls/matrix/noticeplace',400,'bad request',{},
            io.BytesIO(json.dumps({'error':'invalid call bounds'}).encode()),
        )
        def reject(*args,**kwargs): raise error
        sender=IncidentCallSender('http://userio/v1/calls/matrix/noticeplace','test',runner=reject)
        result=sender.send({'incident':{'id':'incident-test','title':'test'}})
        self.assertIsNone(result['answered'])
        self.assertTrue(result['uncertain'])
        self.assertNotIn('safe_to_fallback',result)

    def test_matrix_conflict_error_from_other_channel_remains_uncertain(self):
        error=urllib.error.HTTPError(
            'http://userio/v1/calls/telegram/noticeplace',400,'bad request',{},
            io.BytesIO(json.dumps({'error':'another Matrix voice call is active'}).encode()),
        )
        def reject(*args,**kwargs): raise error
        sender=IncidentCallSender('http://userio/v1/calls/telegram/noticeplace','test',runner=reject)
        result=sender.send({'incident':{'id':'incident-test','title':'test'}})
        self.assertIsNone(result['answered'])
        self.assertTrue(result['uncertain'])
        self.assertNotIn('safe_to_fallback',result)

    def test_http_error_unparseable_body_stores_only_bounded_length_and_digest(self):
        body=b'<html>Bearer private-token; caller +1 202 555 0147</html>'
        error=urllib.error.HTTPError('http://bridge',503,'unavailable',{},io.BytesIO(body))
        def run(*args,**kwargs): raise error
        result=IncidentCallSender('http://bridge','test',runner=run).send(
            {'incident':{'id':'incident-test','title':'Проверка','body':'Связь','project':'test','severity':'critical'}},
            'test-key')

        self.assertEqual(503,result['http_status'])
        self.assertTrue(result['uncertain'])
        self.assertIsNone(result['answered'])
        self.assertEqual(len(body),result['body_length'])
        self.assertEqual(hashlib.sha256(body).hexdigest(),result['body_sha256'])
        self.assertFalse(result['body_truncated'])
        self.assertNotIn('body',result)
        serialized=json.dumps(result)
        for private in (body.decode(),'private-token','202 555 0147'):
            self.assertNotIn(private,serialized)

    def test_http_error_large_body_is_hashed_with_explicit_truncation(self):
        body=b'x'*(HTTP_ERROR_BODY_LIMIT+50)
        error=urllib.error.HTTPError('http://bridge',504,'gateway timeout',{},io.BytesIO(body))
        def run(*args,**kwargs): raise error
        result=IncidentCallSender('http://bridge','test',runner=run).send(
            {'incident':{'id':'incident-test','title':'Проверка','body':'Связь','project':'test','severity':'critical'}},
            'test-key')

        self.assertEqual(HTTP_ERROR_BODY_LIMIT,result['body_length'])
        self.assertEqual(hashlib.sha256(body[:HTTP_ERROR_BODY_LIMIT]).hexdigest(),result['body_sha256'])
        self.assertTrue(result['body_truncated'])
        self.assertNotIn(body.decode(),json.dumps(result))

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


    def test_answered_matrix_call_for_a_matrix_only_consumer_leaves_no_phone_or_telegram(self):
        """L10 shape: an answered Matrix call acknowledges the incident and stops there."""
        class MatrixCall:
            def __init__(this): this.calls=[]
            def send(this, payload):
                this.calls.append(payload)
                return {'ok':True,'answered':True,'acknowledged':True,'ended':False,
                        'safe_to_fallback':False,'status':'active',
                        'target':'@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',
                        'channel':'matrix','actor':'matrix:@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com'}
        consumer=self.center.create_consumer(
            project='test',name='Matrix only',
            policy=[{'id':'step-1','platform':'matrix','action':'call','target':{},
                     'retry_interval_seconds':60,'max_repeats':1}],
        )
        created=self.center.create_event(consumer['intake_token'],'matrix-only-call',self.event)
        incident_id=created['incident_id']
        rows=self.center._connection.execute(
            "SELECT id,channel FROM deliveries WHERE incident_id=? ORDER BY due_at",(incident_id,)).fetchall()
        self.assertEqual(['matrix.call'],[row['channel'] for row in rows])

        sender=MatrixCall()
        self.worker._matrix_call=sender
        for row in self.center.claim_due_deliveries(now_epoch=10**12,channel_group='call'):
            self.worker.deliver(row)

        self.assertEqual(1,len(sender.calls))
        incident=self.center.get_incident(incident_id)
        self.assertEqual('acknowledged',incident['state'])
        self.assertIsNotNone(incident['acknowledged_at'])
        audit=self.center._connection.execute(
            "SELECT actor FROM audit_events WHERE incident_id=? ORDER BY created_at",(incident_id,)).fetchall()
        self.assertTrue(any('matrix:@lhc_voice_tester_b_20261021' in str(row['actor']) for row in audit))
        after=self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id=? ORDER BY due_at",(incident_id,)).fetchall()
        self.assertEqual(['matrix.call'],[row['channel'] for row in after])
        self.assertEqual([],self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id=? AND "
            "(channel LIKE 'telegram.%' OR channel LIKE 'android.%' OR channel LIKE 'phone.%')",
            (incident_id,)).fetchall())

    def test_matrix_only_consumer_policy_denies_the_voice_chain_after_a_rejected_matrix_call(self):
        """A rejected Matrix call ends the episode for a Matrix only consumer.

        Without the policy guard a proven miss advances the episode to
        telegram.call and then android.phone.call. An explicit Matrix only
        policy forbids that whole rung, not only its first step.
        """
        consumer = self.center.create_consumer(
            project='test', name='Matrix only call',
            policy=[{'id': 'step-1', 'platform': 'matrix', 'action': 'call', 'target': {},
                     'retry_interval_seconds': 60, 'max_repeats': 1}],
        )
        created = self.center.create_event(consumer['intake_token'], 'matrix-only-call', self.event)
        incident_id = created['incident_id']
        self.center.schedule_incident_call_chain_if_active(incident_id, 0)
        rows = self.center._connection.execute(
            "SELECT id, channel FROM deliveries WHERE incident_id=? ORDER BY created_at, rowid", (incident_id,)
        ).fetchall()
        self.assertEqual(['matrix.call', 'matrix.call'], [row['channel'] for row in rows])

        claimed = self.center.claim_due_deliveries(now_epoch=10**12, channel_group='call')
        self.assertEqual(2, len(claimed))
        for row in claimed:
            self.center.complete_delivery(row['id'], 'failed', result=MISSED)

        after = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id=? ORDER BY created_at, rowid", (incident_id,)
        ).fetchall()
        self.assertEqual(['matrix.call', 'matrix.call'], [row['channel'] for row in after])
        self.assertEqual([], [row['channel'] for row in after if not row['channel'].startswith('matrix.')])
        self.assertEqual([], [row['channel'] for row in after
                              if row['status'] in ('queued', 'claimed', 'sending')
                              and not row['channel'].startswith('matrix.')])
        stages = self.center._connection.execute(
            "SELECT platform, action FROM consumer_policy_stages WHERE consumer_id=? AND enabled=1",
            (consumer['id'],),
        ).fetchall()
        self.assertEqual([('matrix', 'call')], [(row['platform'], row['action']) for row in stages])
        denied = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE incident_id=? AND type='chain_advance_denied'",
            (incident_id,),
        ).fetchone()
        self.assertEqual(1, denied['count'])

    def test_matrix_only_consumer_policy_denies_a_phone_rung_after_an_answered_matrix_call(self):
        """An answered Matrix call acknowledges the incident and schedules nothing else."""
        class MatrixCall:
            def __init__(this): this.calls = []
            def send(this, payload):
                this.calls.append(payload)
                return {'ok': True, 'answered': True, 'acknowledged': True, 'ended': False,
                        'safe_to_fallback': False, 'status': 'active',
                        'target': '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',
                        'channel': 'matrix', 'actor': 'matrix:@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com'}
        consumer = self.center.create_consumer(
            project='test', name='Matrix only answered call',
            policy=[{'id': 'step-1', 'platform': 'matrix', 'action': 'call', 'target': {},
                     'retry_interval_seconds': 60, 'max_repeats': 1}],
        )
        created = self.center.create_event(consumer['intake_token'], 'matrix-only-answered', self.event)
        incident_id = created['incident_id']
        sender = MatrixCall()
        self.worker._matrix_call = sender
        claimed = self.center.claim_due_deliveries(now_epoch=10**12, channel_group='call')
        self.assertEqual(1, len(claimed))
        for row in claimed:
            self.worker.deliver(row)

        self.assertEqual(1, len(sender.calls))
        self.assertEqual('acknowledged', self.center.get_incident(incident_id)['state'])
        after = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id=? ORDER BY created_at, rowid", (incident_id,)
        ).fetchall()
        self.assertEqual([], [row['channel'] for row in after if not row['channel'].startswith('matrix.')])
        self.assertEqual([], [row['channel'] for row in after
                              if row['status'] in ('queued', 'claimed', 'sending')
                              and not row['channel'].startswith('matrix.')])
        stages = self.center._connection.execute(
            "SELECT platform, action FROM consumer_policy_stages WHERE consumer_id=? AND enabled=1",
            (consumer['id'],),
        ).fetchall()
        self.assertEqual([('matrix', 'call')], [(row['platform'], row['action']) for row in stages])

    def test_scoped_matrix_route_denies_the_unscoped_voice_chain_episode(self):
        """A scoped consumer cannot leave its explicit Matrix route through the chain."""
        consumer = self.center.create_consumer(
            project='test', name='Scoped Matrix call',
            policy=[{'id': 'step-1', 'platform': 'matrix', 'action': 'call',
                     'target': {'room_id': '!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com',
                                'participants': ['@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com',
                                                '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com'],
                                'target': '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com'},
                     'retry_interval_seconds': 60, 'max_repeats': 1}],
        )
        created = self.center.create_event(consumer['intake_token'], 'scoped-call', self.event)
        incident_id = created['incident_id']
        rows = self.center._connection.execute(
            "SELECT channel, target_json FROM deliveries WHERE incident_id=? ORDER BY created_at, rowid", (incident_id,)
        ).fetchall()
        self.assertEqual(['matrix.call'], [row['channel'] for row in rows])
        self.assertIn('!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com', rows[0]['target_json'])

        # The chain episode carries no room: it may not start for a scoped route.
        self.assertIsNone(self.center.schedule_incident_call_chain_if_active(incident_id, 0))

        claimed = self.center.claim_due_deliveries(now_epoch=10**12, channel_group='call')
        self.assertEqual(1, len(claimed))
        for row in claimed:
            self.center.complete_delivery(row['id'], 'failed', result=MISSED)

        after = self.center._connection.execute(
            "SELECT channel, target_json FROM deliveries WHERE incident_id=? ORDER BY created_at, rowid", (incident_id,)
        ).fetchall()
        self.assertEqual(['matrix.call'], [row['channel'] for row in after])
        self.assertEqual([], [row['channel'] for row in after if not row['channel'].startswith('matrix.')])
        for row in after:
            self.assertIn('!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com', row['target_json'])
        denied = self.center._connection.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE incident_id=? AND type='delivery_denied' "
            "AND json_extract(payload_json, '$.reason')='matrix_route_scope'", (incident_id,)
        ).fetchone()
        self.assertEqual(1, denied['count'])

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
        error=TimeoutError('lost ACK')
        def run(*args,**kwargs):raise error
        with self.assertRaises(TimeoutError):
            IncidentCallSender('http://bridge','test',runner=run).send(self.payload,'test-key')

        http_error=urllib.error.HTTPError('http://bridge',500,'failed',{},io.BytesIO(b''))
        def run_http_error(*args,**kwargs):raise http_error
        result=IncidentCallSender('http://bridge','test',runner=run_http_error).send(self.payload,'test-key')
        self.assertIsNone(result['answered'])
        self.assertIs(result['uncertain'],True)
        self.assertEqual(500,result['http_status'])
        self.assertNotIn('safe_to_fallback',result)

    def test_answered_call_without_spoken_ack_is_still_a_successful_answer(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps({'ok':True,'answered':True,'acknowledged':False,
                'ended':False,'safe_to_fallback':False,'status':'active','target':'owner','channel':'matrix'}).encode()
        result=IncidentCallSender('http://bridge','test',runner=lambda *a,**k:Response()).send(self.payload,'chain-key')
        self.assertTrue(result['answered'])
        self.assertFalse(result['acknowledged'])
        self.assertFalse(result['safe_to_fallback'])


class ScopedCallTests(unittest.TestCase):
    """The scoped Matrix call carries the typed destination and correlates its receipt."""

    payload = {
        'incident': {'id': 'incident-call', 'title': 'Проверка', 'body': 'Связь', 'project': 'test', 'severity': 'critical'},
        'delivery': {'delivery_key': 'incident-call:matrix.call:step:step-1:repeat:1'},
        'destination': {
            'account_ref': 'matrix:37698708b623db45e1bfe61f4d01',
            'room_id': '!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com',
            'target': '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',
            'participants': [
                '@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com',
                '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',
            ],
        },
    }

    def _receipt(self, **changes):
        receipt = {
            'call_id': 'call-1', 'channel': 'matrix', 'status': 'completed', 'ended': True,
            'answered': True, 'acknowledged': True, 'safe_to_fallback': False,
            'account_ref': self.payload['destination']['account_ref'],
            'room_id': self.payload['destination']['room_id'],
            'destination': dict(self.payload['destination']),
            'request_id': 'noticeplace:scoped',
            'actor': '@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',
            'ok': True,
        }
        receipt.update(changes)
        return receipt

    def _sender(self, receipt, requests):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return json.dumps(receipt).encode()

        def run(request, timeout):
            requests.append(json.loads(request.data))
            return Response()
        return IncidentCallSender('http://bridge/v1/calls/matrix/noticeplace', 'test', runner=run)

    def test_scoped_call_sends_the_typed_destination(self):
        requests = []
        result = self._sender(self._receipt(), requests).send(self.payload, 'incident-call:matrix.call:step:step-1:repeat:1')
        self.assertTrue(result['answered'])
        self.assertEqual('matrix:@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com', result['actor'])
        self.assertEqual(self.payload['destination'], requests[0]['destination'])

    def test_scoped_call_receipt_must_match_the_destination(self):
        for changes in (
            {'actor': '@bezrabotnyi:chat.bezrabotnyi.com'},
            {'room_id': '!SGgCRDRJRnTYTQvKAG:chat.bezrabotnyi.com'},
            {'account_ref': 'matrix:3a38caa1e8ac2eeb77443b275466'},
            {'call_id': ''},
            {'destination': {**self.payload['destination'], 'target': '@bezrabotnyi:chat.bezrabotnyi.com'}},
        ):
            with self.subTest(changes=changes):
                requests = []
                result = self._sender(self._receipt(**changes), requests).send(self.payload, 'key')
                self.assertIsNone(result['answered'])
                self.assertTrue(result['uncertain'])
                self.assertNotIn('safe_to_fallback', result)

    def test_scoped_call_without_a_complete_destination_is_not_placed(self):
        payload = dict(self.payload)
        payload['destination'] = {'room_id': '!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com'}
        requests = []
        result = self._sender(self._receipt(), requests).send(payload, 'key')
        self.assertIsNone(result['answered'])
        self.assertTrue(result['uncertain'])
        self.assertEqual([], requests)


def test_telegram_http_receipt_wait_covers_extended_native_startup():
    """Fast unit expected1s/max10s: cannot lose the receipt before UserIO535s bound."""
    sender=IncidentCallSender('https://example.invalid/v1/calls/telegram/noticeplace','test',450)
    assert sender.timeout > 535
    matrix=IncidentCallSender('https://example.invalid/v1/calls/matrix/noticeplace','test',450)
    assert matrix.timeout==450
