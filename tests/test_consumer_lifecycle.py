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
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
            client = SourceVerifier('http://test', 'secret', 'health', 'host:100', '100', directory, transport=transport)
            with self.assertRaises(TimeoutError):
                client.tick(lambda _: {'observed_state': 'healthy', 'evidence_refs': ['fresh:disk:40']})
            with self.assertRaises(VerificationPending):
                client.tick(lambda _: self.fail('must not re-probe'))
            self.assertEqual(calls, ['GET', 'POST'])

    def test_wrong_host_never_probes_or_posts(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SourceVerifier('http://test', 'secret', 'health', 'host:100', 'other', directory,
                                    transport=lambda *args: {'requests': [self.request()]})
            with self.assertRaises(ValueError):
                client.tick(lambda _: self.fail('wrong host'))

