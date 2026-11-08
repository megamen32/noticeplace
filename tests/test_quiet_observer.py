"""Finite F02 observer; synthetic SQLite, no live services or timers."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]/'scripts/observe_quiet_operations.py'
spec = importlib.util.spec_from_file_location('quiet_observer', SOURCE)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObserverTests(unittest.TestCase):
    def database(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
        c.executescript('''CREATE TABLE incidents(id TEXT,kind TEXT,severity TEXT,event_type TEXT,state TEXT,body TEXT);
          CREATE TABLE deliveries(id TEXT,incident_id TEXT,status TEXT,attempt INT,created_at REAL,updated_at REAL,due_at REAL,channel TEXT,target_json TEXT,delivery_key TEXT,last_error TEXT,result_json TEXT);
          CREATE TABLE incident_lifecycle(incident_id TEXT,verification_state TEXT,source_id TEXT,verification_due REAL,group_key TEXT,session_id TEXT,harness TEXT);
          INSERT INTO incidents VALUES ('i','incident','important','health.degraded','open','SECRET_PAYLOAD');
          INSERT INTO deliveries VALUES ('d','i','queued',1,1,1,1,'gptadmin.agent:health-diagnosis','{}','key','SECRET_ERROR',NULL);
          INSERT INTO incident_lifecycle VALUES ('i','unknown','source',10,'group',NULL,NULL);
        ''')
        c.execute('PRAGMA query_only=ON'); self.addCleanup(c.close)
        return c

    def snapshot(self, at=100):
        r = observer.collect_store(self.database(), now=at)
        return {**r,'captured_at':at,'release':'release-one','latencies':{},'collector_sha256':observer.digest(SOURCE)}

    def test_readonly_projection_excludes_payloads_and_secrets(self):
        c = self.database(); snapshot = observer.collect_store(c, now=20)
        self.assertTrue(snapshot['scan_complete'])
        self.assertEqual(1, snapshot['verifier_backlog'][0]['count'])
        self.assertNotIn('SECRET', json.dumps(snapshot))
        with self.assertRaises(sqlite3.OperationalError): c.execute('DELETE FROM deliveries')

    def test_row_bound_is_explicit_not_false_complete(self):
        result = observer.collect_store(self.database(), now=20, max_rows=1)
        self.assertFalse(result['scan_complete']); self.assertEqual(1,len(result['deliveries']))

    def test_retry_deltas_ignore_deletion_and_report_coverage_gap(self):
        before = self.snapshot(); after = json.loads(json.dumps(before)); after['captured_at'] += observer.DAY
        after['deliveries'][0].update(attempt=4,status='sent')
        report = observer.compare(before,after)
        self.assertEqual(3,report['attempt_increments_observed']);self.assertEqual(3,report['retry_increments_observed'])
        after['deliveries'] = []
        report = observer.compare(before,after)
        self.assertEqual(1,report['missing_baseline_rows']);self.assertFalse(report['retained_row_delta_coverage'])
        self.assertEqual('REVIEW_REQUIRED',report['status'])

    def test_quiet_delivery_is_review_candidate_not_automatic_alarm(self):
        before=self.snapshot();after=json.loads(json.dumps(before));after['captured_at']+=observer.DAY
        after['deliveries'][0].update(status='sent',quiet_candidate=1,card_update_candidate=1,channel_class='message_or_other')
        r=observer.compare(before,after)
        self.assertEqual(['d'],r['quiet_delivery_candidate_ids']);self.assertEqual('REVIEW_REQUIRED',r['status'])

    def test_early_comparison_refuses_before_live_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'baseline.json';observer.write_once(path,self.snapshot(at=observer.time.time()))
            with patch.object(observer,'capture') as capture:
                with self.assertRaisesRegex(ValueError,'24 hours'):
                    observer.main(['compare','--baseline',str(path),'--output',tmp])
                capture.assert_not_called()

    def test_baseline_is_exclusive_and_requires_both_rollout_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'baseline.json';observer.write_once(p,{'original':True})
            with self.assertRaises(FileExistsError):observer.write_once(p,{'replacement':True})
            self.assertEqual({'original':True},json.loads(p.read_text()))
            gate=Path(tmp)/'gate.json';gate.write_text(json.dumps({'galileo':{'status':'pending'}}))
            with patch.object(observer,'capture') as capture:
                with self.assertRaises(ValueError):observer.main(['baseline','--gate',str(gate),'--output',tmp])
                capture.assert_not_called()

    def test_transient_timer_is_one_finite_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'baseline.json';observer.write_once(p,self.snapshot(at=observer.time.time()))
            args=observer.timer_command(p,tmp)
            self.assertEqual('systemd-run',args[0]);self.assertIn('compare',args)
            self.assertFalse(any('OnUnitActiveSec' in v or 'Persistent' in v for v in args))
            self.assertTrue(any(v.startswith('--on-active=') for v in args))

    def test_preview_does_not_touch_runtime(self):
        with patch.object(observer,'capture') as capture, patch('builtins.print'):
            observer.main([])
            capture.assert_not_called()

    def test_rollout_gate_hash_and_release_bind_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'receipt.json';receipt.write_text('{}')
            ready={'status':'complete','receipt':str(receipt),'sha256':observer.digest(receipt)}
            gate=root/'gate.json';gate.write_text(json.dumps({'galileo':ready,'kepler':ready,'installed_release':'release-one'}))
            with patch.object(observer,'capture',return_value=self.snapshot()),patch('builtins.print'):
                observer.main(['baseline','--gate',str(gate),'--output',str(root/'out')])
            saved=json.loads((root/'out/baseline.json').read_text())
            self.assertEqual(observer.digest(gate),saved['rollout_gate_sha256'])
            receipt.write_text('{"changed":true}')
            with self.assertRaises(ValueError):observer.rollout_gate(gate)

    def test_duplicate_increase_and_latency_failure_require_review(self):
        before=self.snapshot();after=json.loads(json.dumps(before));after['captured_at']+=observer.DAY
        after['duplicate_card_receipt_candidates']=1
        after['latencies']={'api':{'errors':['TimeoutError']}}
        r=observer.compare(before,after)
        self.assertEqual('REVIEW_REQUIRED',r['status'])
        self.assertEqual(1,r['duplicate_candidate_deltas']['duplicate_card_receipt_candidates'])
        self.assertTrue(r['latency_probe_errors']);self.assertFalse(r['full_window_coverage'])
