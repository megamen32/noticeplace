import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('credentials', ROOT / 'deploy/consumer-lifecycle-credentials.py')
credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credentials)


class CredentialTests(unittest.TestCase):
    def fixture(self):
        scopes = {'original': {'project': 'project', 'consumer_id': 'consumer',
                              'max_severity': 'emergency', 'automatic_health_diagnosis': False,
                              'agent_jobs': ['health-diagnosis']}}
        callback = {'url': 'http://127.0.0.1:19400', 'tokens': {'project': 'original'}}
        plan = {'projects': {'project': {'source_principal': 'source', 'executor_principal': 'executor',
                                        'verification_sources': ['exact:source']}}}
        return scopes, callback, plan

    def test_principal_split_preserves_producer_policy(self):
        scopes, callback, plan = self.fixture()
        output, updated, summary = credentials.prepare(scopes, callback, plan, lambda: 'fresh')
        self.assertFalse(output['original']['automatic_health_diagnosis'])
        self.assertEqual('verifier', output['original']['incident_role'])
        self.assertEqual(['exact:source'], output['original']['verification_sources'])
        self.assertEqual('consumer', output['fresh']['consumer_id'])
        self.assertEqual('executor', output['fresh']['incident_role'])
        self.assertNotIn('verification_sources', output['fresh'])
        self.assertEqual({'project': 'fresh'}, updated['tokens'])
        self.assertNotIn('fresh', json.dumps(summary))

    def test_prepared_scopes_enforce_executor_cannot_verify(self):
        from notification_center.core import NotificationCenter, AuthorizationError
        scopes, callback, plan = self.fixture()
        output, _, _ = credentials.prepare(scopes, callback, plan, lambda: 'fresh')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            center = NotificationCenter(Path(directory) / 'db', output)
            self.addCleanup(center._connection.close)
            event = dict(schema='notify.event.v1', kind='incident', severity='notice',
                         project='project', recipient='health', title='Failure', body='Failure',
                         dedup_key='exact', event_type='health.degraded', source_id='exact:source',
                         host_id='host', signal_type='service', correlation_id='episode')
            incident = center.create_event('original', 'open', event)['incident_id']
            request = center.record_work_finished('fresh', incident, 'finish',
                        dict(attempt_id='one', session_id='session', harness='codex'))
            body = dict(request_id=request['request_id'], generation=request['generation'],
                        observed_state='healthy', evidence_refs=['fresh:source'])
            with self.assertRaises(AuthorizationError):
                center.record_source_check('fresh', incident, 'wrong', body)
            center.record_source_check('original', incident, 'correct', body)
            self.assertEqual('resolved', center.get_incident(incident)['state'])

    def test_wildcards_and_identity_reuse_rejected(self):
        scopes, callback, plan = self.fixture()
        plan['projects']['project']['verification_sources'] = ['*']
        with self.assertRaises(ValueError): credentials.prepare(scopes, callback, plan)
        plan['projects']['project']['verification_sources'] = ['exact']
        plan['projects']['project']['executor_principal'] = 'source'
        with self.assertRaises(ValueError): credentials.prepare(scopes, callback, plan)

    def test_apply_sync_rollback_on_fixture_only(self):
        scopes, callback, plan = self.fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            base = Path(directory)
            env, cb, review = base / 'env', base / 'callback', base / 'review.json'
            env.write_text('OTHER=preserved\nNOTIFY_CENTER_TOKENS_JSON=' + json.dumps(scopes) + '\n')
            cb.write_text(json.dumps(callback))
            review.write_text(json.dumps(plan))
            env.chmod(0o600); cb.chmod(0o600)
            original = [env.read_bytes(), cb.read_bytes()]
            command = [sys.executable, str(ROOT / 'deploy/consumer-lifecycle-credentials.py'),
                       '--env', str(env), '--callback', str(cb)]
            result = subprocess.run(command + ['--plan', str(review)], check=True, capture_output=True, text=True)
            self.assertNotIn('original', result.stdout)
            self.assertEqual(original, [env.read_bytes(), cb.read_bytes()])
            subprocess.run(command + ['--plan', str(review), '--prepare-dir', str(base / 'candidate')], check=True, capture_output=True)
            subprocess.run(command + ['--apply-prepared', str(base / 'candidate'), '--backup-dir', str(base / 'backup')], check=True, capture_output=True)
            migrated = credentials.read_env(env.read_text())
            executor = next(t for t, s in migrated.items() if s['incident_role'] == 'executor')
            subprocess.run([sys.executable, str(ROOT / 'scripts/sync-health-callback-tokens.py'), str(env), str(cb)], check=True, capture_output=True)
            self.assertEqual(executor, json.loads(cb.read_text())['tokens']['project'])
            subprocess.run(command + ['--rollback', '--backup-dir', str(base / 'backup')], check=True, capture_output=True)
            self.assertEqual(original, [env.read_bytes(), cb.read_bytes()])

    def test_dynamic_sources_belong_only_to_authenticated_source_principal(self):
        from notification_center.core import NotificationCenter, AuthorizationError
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            common = dict(project='project', max_severity='emergency', incident_role='verifier',
                          verification_owned_sources=True)
            tokens = {'one': dict(common, principal_id='one'),
                      'two': dict(common, principal_id='two', verification_sources=['dynamic:video']),
                      'executor': dict(common, principal_id='executor', incident_role='executor')}
            center = NotificationCenter(Path(directory) / 'db', tokens)
            self.addCleanup(center._connection.close)
            event = dict(schema='notify.event.v1', kind='incident', severity='notice', project='project',
                         recipient='health', title='Failure', body='Failure', dedup_key='first',
                         event_type='health.degraded', source_id='dynamic:video', host_id='host',
                         signal_type='service', correlation_id='episode', actor='two')
            incident = center.create_event('one', 'first', event)['incident_id']
            request = center.record_work_finished('executor', incident, 'finish',
                        dict(attempt_id='one', session_id='session', harness='codex'))
            self.assertEqual(1, len(center.due_source_checks('one', 'project', 'dynamic:video')))
            self.assertEqual([], center.due_source_checks('two', 'project', 'dynamic:video'))
            body = dict(request_id=request['request_id'], generation=request['generation'],
                        observed_state='healthy', evidence_refs=['fresh:source'], actor='one')
            with self.assertRaises(AuthorizationError):
                center.record_source_check('two', incident, 'foreign', body)
            with self.assertRaises(AuthorizationError):
                center.record_source_check('executor', incident, 'executor', body)
            with self.assertRaises(AuthorizationError):
                center.due_source_checks('executor', 'project', 'dynamic:video')
            # A new incident with reused source ID is still owned by its own
            # authenticated producer, never by the body actor/source claim.
            second = center.create_event('two', 'second', dict(event, dedup_key='second', actor='one'))['incident_id']
            center.record_work_finished('executor', second, 'finish-second',
                        dict(attempt_id='second', session_id='other', harness='codex'))
            self.assertEqual([incident], [r['incident_id'] for r in center.due_source_checks('one', 'project', 'dynamic:video')])
            self.assertEqual([second], [r['incident_id'] for r in center.due_source_checks('two', 'project', 'dynamic:video')])
            center.record_source_check('one', incident, 'own', body)
            self.assertEqual('resolved', center.get_incident(incident)['state'])
            self.assertEqual('open', center.get_incident(second)['state'])

    def test_one_candidate_on_two_replicas_preserves_host_env_and_fences_collision(self):
        scopes, callback, plan = self.fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            base = Path(directory)
            env1, env2, cb, review = [base / name for name in ('env1', 'env2', 'cb', 'plan')]
            for i, env in enumerate((env1, env2)):
                env.write_text('PORT=' + str(19400 + i) + '\nNOTIFY_CENTER_TOKENS_JSON=' + json.dumps(scopes) + '\n')
                env.chmod(0o600)
            cb.write_text(json.dumps(callback)); cb.chmod(0o600)
            review.write_text(json.dumps(plan))
            helper = [sys.executable, str(ROOT / 'deploy/consumer-lifecycle-credentials.py')]
            subprocess.run(helper + ['--env', str(env1), '--callback', str(cb), '--plan', str(review),
                                    '--prepare-dir', str(base / 'candidate')], check=True, capture_output=True)
            before_preflight = env2.read_bytes()
            subprocess.run(helper + ['--env', str(env2), '--scope-only', '--check-prepared', str(base / 'candidate')], check=True, capture_output=True)
            self.assertEqual(before_preflight, env2.read_bytes())
            subprocess.run(helper + ['--env', str(env1), '--callback', str(cb), '--apply-prepared', str(base / 'candidate'),
                                    '--backup-dir', str(base / 'backup1')], check=True, capture_output=True)
            subprocess.run(helper + ['--env', str(env2), '--scope-only', '--apply-prepared', str(base / 'candidate'),
                                    '--backup-dir', str(base / 'backup2')], check=True, capture_output=True)
            self.assertEqual(credentials.read_env(env1.read_text()), credentials.read_env(env2.read_text()))
            self.assertTrue(env1.read_text().startswith('PORT=19400\n'))
            self.assertTrue(env2.read_text().startswith('PORT=19401\n'))
            # Already-applied or diverged scope maps cannot silently regenerate.
            before = env2.read_bytes()
            result = subprocess.run(helper + ['--env', str(env2), '--scope-only', '--apply-prepared', str(base / 'candidate'),
                                             '--backup-dir', str(base / 'backup3')], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, env2.read_bytes())
