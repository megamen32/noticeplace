"""A fresh manual attempt is allowed only after proven prelaunch non-admission."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from notification_center.core import NotificationCenter
from notification_center.agent_job_helper import HerderOwnerStateBlocked

class ManualPrelaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.center=NotificationCenter(Path(self.tmp.name)/'db',{'source':{'project':'test','max_severity':'critical'}})
        self.addCleanup(self.center._connection.close)
        self.inc=self.center.create_event('source','open',dict(schema='notify.event.v1',project='test',recipient='me',kind='incident',severity='notice',title='Failure',dedup_key='test'))['incident_id']
        self.first=self.center.apply_telegram_action(self.inc,'ai','telegram:42')['agent_job_delivery_id']
        self.center.complete_delivery(self.first,'sent')

    def receipt(self,**changes):
        inner=dict(status='source_blocked',source='agent-herder-owner-disarm',reason='herder_owner_state_unknown',admission='not_attempted',operation_id='operation-before-launch',incident_id=self.inc,human_held=True,retryable=False,non_retryable=True)
        inner.update(changes)
        return dict(status='completed',job_id='trusted-hub-job',route_id='trusted-route',agent_receipt=inner)

    def test_verified_prelaunch_negative_creates_one_fresh_manual_attempt(self):
        self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',self.receipt())
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            restarted=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
            repeated=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
        self.assertNotEqual(self.first,restarted['agent_job_delivery_id'])
        self.assertEqual(restarted['agent_job_delivery_id'],repeated['agent_job_delivery_id'])
        self.assertTrue(restarted['restarted'])
        self.assertTrue(repeated['idempotent'])
        guard.assert_called_once()
        rows=self.center._connection.execute("SELECT status,result_json FROM deliveries WHERE incident_id=? AND channel='gptadmin.agent:health-diagnosis' ORDER BY rowid",(self.inc,)).fetchall()
        self.assertEqual(['sent','queued'],[row['status'] for row in rows])
        self.assertTrue(rows[0]['result_json'])

    def test_blank_unknown_accepted_or_inconsistent_receipt_cannot_restart(self):
        cases=[None,self.receipt(admission='unknown'),self.receipt(delivery='accepted'),self.receipt(outcome='admission_unknown'),self.receipt(outcome='unknown'),self.receipt(delivery='in_flight'),self.receipt(session_id='accepted-session'),self.receipt(created=True),self.receipt(reason='herder_owner_stop'),self.receipt(incident_id='foreign')]
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            for receipt in cases:
                if receipt:
                    self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',receipt)
                result=self.center.apply_telegram_action(self.inc,'ai','source:forged-clear')
                self.assertEqual(self.first,result['agent_job_delivery_id'])
        guard.assert_not_called()

    def test_genuine_stop_or_unknown_guard_cannot_be_cleared_by_actor(self):
        self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',self.receipt())
        for reason in ('herder_owner_stop','herder_owner_state_unknown'):
            with patch('notification_center.agent_job_helper.require_herder_owner_running',side_effect=HerderOwnerStateBlocked(reason)):
                result=self.center.apply_telegram_action(self.inc,'ai','source:claimed-owner')
            self.assertEqual(self.first,result['agent_job_delivery_id'])
        self.assertEqual('sent',self.center._connection.execute('SELECT status FROM deliveries WHERE id=?',(self.first,)).fetchone()[0])

    def test_nonnull_accepted_receipt_is_never_overwritten_by_late_negative(self):
        accepted=dict(status='completed',operation_id='accepted-operation',delivery='accepted')
        self.center.complete_delivery(self.first,'sent',result=accepted)
        self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',self.receipt())
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            result=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
        self.assertEqual(self.first,result['agent_job_delivery_id'])
        stored=self.center._connection.execute('SELECT result_json FROM deliveries WHERE id=?',(self.first,)).fetchone()[0]
        self.assertEqual(accepted,json.loads(stored))
        guard.assert_not_called()

    def test_outer_unknown_or_failed_job_cannot_supply_negative_authority(self):
        for status in ('running','unknown','failed'):
            self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',{**self.receipt(),'status':status})
        self.assertIsNone(self.center._connection.execute('SELECT result_json FROM deliveries WHERE id=?',(self.first,)).fetchone()[0])
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            result=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
        self.assertEqual(self.first,result['agent_job_delivery_id'])
        guard.assert_not_called()

    def test_stored_proof_is_bound_to_the_exact_delivery(self):
        self.center.record_agent_job_result(self.inc,self.first,'health-diagnosis',self.receipt())
        proof=json.loads(self.center._connection.execute('SELECT result_json FROM deliveries WHERE id=?',(self.first,)).fetchone()[0])
        proof['delivery_id']='foreign'
        self.center._connection.execute('UPDATE deliveries SET result_json=? WHERE id=?',(json.dumps(proof),self.first))
        self.center._connection.commit()
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            result=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
        self.assertEqual(self.first,result['agent_job_delivery_id'])
        guard.assert_not_called()

    def test_malformed_stored_proof_never_opens_the_guard_or_retries(self):
        with patch('notification_center.agent_job_helper.require_herder_owner_running') as guard:
            for malformed in ('not-json','[]','{}',json.dumps({'schema':'notify.agent-prelaunch.v1','delivery_id':self.first})):
                self.center._connection.execute('UPDATE deliveries SET result_json=? WHERE id=?',(malformed,self.first))
                self.center._connection.commit()
                result=self.center.apply_telegram_action(self.inc,'ai','telegram:42')
                self.assertEqual(self.first,result['agent_job_delivery_id'])
        guard.assert_not_called()
