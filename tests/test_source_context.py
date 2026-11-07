import json
import tempfile
import time
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError
from notification_center.gptadmin_agent import _agent_job_event
from notification_center.agent_job_helper import _repair_first_message


class SourceContextTests(unittest.TestCase):
    def test_context_reaches_executor_without_secret_or_authority(self):
        with tempfile.TemporaryDirectory() as root:
            center = NotificationCenter(Path(root)/'test.sqlite', {'p': {
                'project':'health-monitor','max_severity':'emergency','agent_jobs':['health-diagnosis']}}, default_quiet_hours=[])
            self.addCleanup(center._connection.close)
            context = {'schema':'notify.source-context.v1','service':'Smart Edge',
                       'impact':'Недоступны сайты через роутер', 'symptoms':['HTTPS timeout'],
                       'diagnostics':['token=fixture-secret'], 'observed_at':time.time(),
                       'freshness_seconds':180,'verifier_ref':'router-haproxy-watchdog',
                       'allowed_actions':['Проверить HAProxy']}
            event = {'kind':'incident','project':'health-monitor','recipient':'me','severity':'important',
                     'title':'Сбой маршрута','body':'Проверка не проходит','dedup_key':'route',
                     'event_type':'health.degraded','source_id':'probe','host_id':'router',
                     'signal_type':'https','correlation_id':'episode','source_context':context}
            center.create_event('p','context-1',event)
            delivery = center.claim_due_deliveries(channel_group='agent')[0]
            transported = _agent_job_event('health-diagnosis',center.delivery_payload(delivery),delivery['delivery_key'])
            received = transported['health']['source_context']
            self.assertEqual('complete', received['context_status'])
            self.assertEqual(context['impact'], received['impact'])
            stored = center._connection.execute('SELECT payload_json FROM events').fetchone()[0]
            self.assertNotIn('fixture-secret', stored)
            prompt = _repair_first_message({'cwd':'/tmp/qa'},transported,transported['incident'])
            self.assertIn(context['impact'],prompt)
            self.assertNotIn('fixture-secret',prompt)
            self.assertNotIn('command',transported)
            replay = center.create_event('p','context-1',event)
            self.assertTrue(replay['idempotent'])

    def test_missing_and_stale_context_are_explicit(self):
        from notification_center.source_context import context_for_delivery
        self.assertEqual('incomplete',context_for_delivery(None)['context_status'])
        context = {'schema':'notify.source-context.v1','service':'s','impact':'i','symptoms':['x'],
                   'observed_at':1,'freshness_seconds':60,'verifier_ref':'probe'}
        self.assertEqual('stale',context_for_delivery(context,now=100)['context_status'])

    def test_unknown_authority_and_oversized_context_rejected(self):
        from notification_center.source_context import normalize_source_context
        for context in [{'command':'reboot'}, {'impact':'x'*20000}, {'observed_at':float('nan')},
                        {'diagnostics':{'token':'fixture'}}, {'freshness_seconds':True}]:
            with self.assertRaises(ValueError): normalize_source_context(context)
