import tempfile
import unittest
from pathlib import Path
from notification_center.consumer_lifecycle import SourceVerifier, VerificationPending


class SourceVerifierTests(unittest.TestCase):
    def request(self, signal='disk'):
        return {'incident_id': 'inc_1', 'request_id': 'check_1', 'generation': 2,
                'group': {'project': 'health', 'source_id': 'host:100', 'host_id': '100',
                          'signal_type': signal, 'problem_type': signal}}

    def test_probe_runs_after_pull_and_before_submit(self):
        calls = []
        def transport(method, path, body=None, key=None):
            calls.append(method)
            if method == 'GET':
                return {'requests': [self.request()]}
            self.assertEqual(body['observed_state'], 'degraded')
            self.assertTrue(key)
            return {'observed_state': 'degraded'}
        def probe(group):
            calls.append('probe:' + group['signal_type'])
            return {'observed_state': 'degraded', 'evidence_refs': ['fresh:disk:91']}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            client = SourceVerifier('http://test', 'secret', 'health', 'host:100', '100', directory, transport=transport)
            client.tick(probe)
            self.assertEqual(calls, ['GET', 'probe:disk', 'POST'])
            self.assertNotIn('secret', Path(directory, 'verification.json').read_text())

    def test_ambiguous_post_is_not_replayed(self):
        calls = []
        def transport(method, *args):
            calls.append(method)
            if method == 'GET':
                return {'requests': [self.request()]}
            raise TimeoutError('ambiguous')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            client = SourceVerifier('http://test', 'secret', 'health', 'host:100', '100', directory, transport=transport)
            with self.assertRaises(TimeoutError):
                client.tick(lambda _: {'observed_state': 'healthy', 'evidence_refs': ['fresh:disk:40']})
            with self.assertRaises(VerificationPending):
                client.tick(lambda _: self.fail('must not re-probe'))
            self.assertEqual(calls, ['GET', 'POST', 'GET'])

    def test_wrong_host_never_probes_or_posts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            client = SourceVerifier('http://test', 'secret', 'health', 'host:100', 'other', directory,
                                    transport=lambda *args: {'requests': [self.request()]})
            with self.assertRaises(ValueError):
                client.tick(lambda _: self.fail('wrong host'))

    def test_unknown_admission_reconciles_exact_server_receipt_without_replay(self):
        from notification_center.core import NotificationCenter
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            center = NotificationCenter(Path(directory) / 'center.db', {
                'source': {'project': 'health', 'max_severity': 'emergency',
                           'principal_id': 'probe', 'incident_role': 'verifier',
                           'verification_sources': ['host:100']},
                'executor': {'project': 'health', 'max_severity': 'emergency',
                             'principal_id': 'worker', 'incident_role': 'executor'}})
            self.addCleanup(center._connection.close)
            event = dict(schema='notify.event.v1', kind='incident', severity='notice',
                         project='health', recipient='health', title='Disk', body='Disk full',
                         dedup_key='disk', event_type='health.degraded', source_id='host:100',
                         host_id='100', signal_type='disk', correlation_id='episode')
            incident = center.create_event('source', 'open', event)['incident_id']
            center.record_work_finished('executor', incident, 'finish',
                                        dict(attempt_id='one', session_id='session', harness='codex'))
            posts = []
            def transport(method, path, body=None, key=None):
                if method == 'POST':
                    posts.append(key)
                    center.record_source_check('source', incident, key, body)
                    raise TimeoutError('server committed but response lost')
                if path.startswith('/v1/incidents/'):
                    return center.get_incident(incident)
                return {'requests': center.due_source_checks('source', 'health', 'host:100')}
            client = SourceVerifier('http://test', 'source', 'health', 'host:100', '100',
                                    Path(directory) / 'source', transport=transport)
            with self.assertRaises(TimeoutError):
                client.tick(lambda _: {'observed_state': 'healthy', 'evidence_refs': ['fresh:disk:20']})
            self.assertEqual('resolved', center.get_incident(incident)['state'])
            reconciled = client.tick(lambda _: self.fail('must not re-probe'))
            self.assertEqual('healthy', reconciled[0]['observed_state'])
            self.assertEqual(1, len(reconciled))
            self.assertEqual(1, len(posts))

    def test_lost_degraded_reply_reconciles_after_server_rotates_round_nonce(self):
        from notification_center.core import NotificationCenter
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            center=NotificationCenter(Path(directory)/'center.db',{
                'source':{'project':'health','max_severity':'emergency','principal_id':'probe','incident_role':'verifier','verification_sources':['host:100']},
                'executor':{'project':'health','max_severity':'emergency','principal_id':'worker','incident_role':'executor'}})
            self.addCleanup(center._connection.close)
            event=dict(schema='notify.event.v1',kind='incident',severity='notice',project='health',recipient='health',title='Disk',dedup_key='disk',event_type='health.degraded',source_id='host:100',host_id='100',signal_type='disk',correlation_id='episode')
            incident=center.create_event('source','open',event)['incident_id']
            initial=center.record_work_finished('executor',incident,'finish',dict(attempt_id='one',session_id='session',harness='codex'))
            posts=[]
            def transport(method,path,body=None,key=None):
                if method=='POST':
                    posts.append(key)
                    center.record_source_check('source',incident,key,body)
                    raise TimeoutError('accepted reply lost')
                if path.startswith('/v1/incidents/'):
                    return center.get_incident(incident)
                return {'requests':center.due_source_checks('source','health','host:100')}
            client=SourceVerifier('http://test','source','health','host:100','100',Path(directory)/'source',transport=transport)
            with self.assertRaises(TimeoutError):
                client.tick(lambda _:dict(observed_state='degraded',evidence_refs=['fresh:disk:91']))
            self.assertNotEqual(initial['request_id'],center.get_incident(incident)['lifecycle']['request_id'])
            result=client.tick(lambda _:self.fail('must reconcile without a new probe'))
            self.assertEqual('degraded',result[0]['observed_state'])
            self.assertEqual(1,len(posts))
            self.assertEqual('open',center.get_incident(incident)['state'])
