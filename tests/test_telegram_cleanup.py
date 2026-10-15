"""Focused integration: opt-in recovery removes only its confirmed Telegram card.

Expected 3s, maximum30s. Actual center/worker/sender, isolated SQLite/fake network.
"""
import io
import json
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, TelegramSender
from notification_center.telegram_cleanup import SETTING, should_delete


class TelegramCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name)/'db.sqlite',
            {'token': {'project': 'dns', 'max_severity': 'emergency'}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.calls = []; self.fail_delete = False
        self.worker = DeliveryWorker(self.center, TelegramSender('fake', '-100', center=self.center))
        self.clock = time.time()
        def network(request, **kwargs):
            method = request.full_url.rsplit('/', 1)[-1]
            data = {k:v[0] for k,v in urllib.parse.parse_qs(request.data.decode()).items()}
            self.calls.append((method, data))
            if method == 'deleteMessage' and self.fail_delete:
                raise TimeoutError('unknown delete')
            result = True if method == 'deleteMessage' else {
                'message_id': int(data.get('message_id') or len(self.calls)), 'chat': {'id': -100}}
            response = io.BytesIO(json.dumps({'ok':True, 'result':result}).encode()); response.status=200
            return response
        p = patch('notification_center.http_api.urllib.request.urlopen', side_effect=network)
        p.start(); self.addCleanup(p.stop)

    def create(self, dedup='dns-one'):
        return self.center.create_event('token', dedup, {'schema':'notify.event.v1', 'kind':'incident',
            'project':'dns','recipient':'me','severity':'notice','dedup_key':dedup,
            'title':'DNS','body':'Проверка'})['incident_id']

    def tick(self):
        self.clock += 30
        with patch('notification_center.core.time.time', return_value=self.clock):
            return self.worker.run_once()

    def test_disabled_by_default_preserves_card(self):
        i = self.create(); self.tick(); self.center.resolve(i, 'monitor'); self.tick()
        self.assertEqual(['sendMessage'], [m for m,_ in self.calls])
        self.assertIsNotNone(self.center.original_telegram_card(i))

    def test_enabled_deletes_once_keeps_history_and_late_refresh_does_not_resurrect(self):
        i = self.create(); self.tick()
        original = self.center.original_telegram_card(i)
        self.center.set_runtime_setting(SETTING+'dns', 'true')
        self.center.resolve(i, 'monitor'); self.tick(); self.tick()
        self.assertEqual(['sendMessage','deleteMessage'], [m for m,_ in self.calls])
        self.assertEqual(str(original['message_id']), self.calls[-1][1]['message_id'])
        self.assertIsNone(self.center.original_telegram_card(i))
        self.assertEqual('resolved', self.center.get_incident(i)['state'])
        self.center.queue_incident_card_refresh(i); self.tick(); self.tick()
        self.assertEqual(2, len(self.calls))
        self.assertEqual(1, self.center._connection.execute(
            "select count(*) from audit_events where incident_id=? and type='telegram_card_deleted'", (i,)).fetchone()[0])

    def test_unknown_delete_not_replayed(self):
        i = self.create(); self.tick(); self.center.set_runtime_setting(SETTING+'dns', 'true')
        self.fail_delete=True; self.center.resolve(i,'monitor'); self.tick(); self.tick()
        self.assertEqual(['sendMessage','deleteMessage'], [m for m,_ in self.calls])
        self.assertIsNotNone(self.center.original_telegram_card(i))
        self.assertEqual(1,self.center._connection.execute("select count(*) from deliveries where status='uncertain'").fetchone()[0])

    def test_shared_card_stays_until_last_member_recovers(self):
        first=self.create(); self.tick(); second=self.create('dns-two')
        with self.center._lock,self.center._connection:
            self.center._connection.execute('update incidents set telegram_card_owner_id=? where id=?',(first,second))
        self.center.set_runtime_setting(SETTING+'dns','true')
        self.center.resolve(first,'monitor'); self.tick(); self.tick()
        self.assertFalse(any(m=='deleteMessage' for m,_ in self.calls))
        self.center.resolve(second,'monitor'); self.tick(); self.tick()
        self.assertEqual(1,sum(m=='deleteMessage' for m,_ in self.calls))

    def test_open_group_member_or_question_or_phase_change_blocks_deletion(self):
        i = self.create(); self.center.set_runtime_setting(SETTING+'dns', 'true')
        payload={'incident':{**self.center.get_incident(i),'state':'resolved'}}
        for extra in ({'group_members':[{'state':'open'}]}, {'human_questions':[{'state':'pending'}]},
                      {'health_outcome':{'status':'phase_changed'}}):
            with self.subTest(extra=extra):self.assertFalse(should_delete(self.center,{**payload,**extra}))
        self.assertTrue(should_delete(self.center,payload))

    def test_project_scope_and_malformed_setting_never_delete(self):
        i=self.create(); payload={'incident':{**self.center.get_incident(i),'state':'resolved'}}
        self.center.set_runtime_setting(SETTING+'other','true')
        self.assertFalse(should_delete(self.center,payload))
        for value in ('["dns"]','1','{"dns":true}','broken'):
            self.center.set_runtime_setting(SETTING+'dns',value)
            self.assertFalse(should_delete(self.center,payload))
