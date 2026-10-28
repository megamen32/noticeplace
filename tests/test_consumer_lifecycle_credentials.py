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

    def test_reviewed_missing_integration_baseline_is_narrow(self):
        scopes, _, _ = self.fixture()
        scopes['existing-integration'] = dict(project='integration', max_severity='important', agent_jobs=None)
        accepted = credentials.permitted_baselines(scopes, ['integration'])
        reduced = {t: s for t, s in scopes.items() if s['project'] != 'integration'}
        self.assertEqual({credentials.scope_hash(scopes), credentials.scope_hash(reduced)}, set(accepted))
        with self.assertRaises(ValueError):
            credentials.permitted_baselines(scopes, ['project'])
        for update in [dict(agent_jobs=['health-diagnosis']), dict(max_severity='critical'),
                       dict(automatic_health_diagnosis=True), dict(verification_owned_sources=True)]:
            changed = dict(scopes, **{'existing-integration': dict(scopes['existing-integration'], **update)})
            with self.assertRaises(ValueError):
                credentials.permitted_baselines(changed, ['integration'])

    def test_same_candidate_normalizes_only_reviewed_missing_seed(self):
        scopes, callback, plan = self.fixture()
        canonical = dict(scopes, integration_token=dict(project='integration', max_severity='important', agent_jobs=None))
        plan['projects']['integration'] = dict(source_principal='integration-source', executor_principal='integration-executor', verification_sources=[])
        callback['tokens']['integration'] = 'integration_token'
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            base = Path(directory)
            env100, env44, cb, review = [base / name for name in ('100env', '44env', 'cb', 'plan')]
            for env, mapping in [(env100, canonical), (env44, scopes)]:
                env.write_text('LOCAL_PORT=' + env.name + '\nNOTIFY_CENTER_TOKENS_JSON=' + json.dumps(mapping) + '\n')
                env.chmod(0o600)
            cb.write_text(json.dumps(callback)); cb.chmod(0o600)
            review.write_text(json.dumps(plan))
            helper = [sys.executable, str(ROOT / 'deploy/consumer-lifecycle-credentials.py')]
            subprocess.run(helper + ['--env', str(env100), '--callback', str(cb), '--plan', str(review),
                '--allow-missing-base-project', 'integration', '--prepare-dir', str(base / 'candidate')], check=True, capture_output=True)
            subprocess.run(helper + ['--env', str(env44), '--scope-only', '--check-prepared', str(base / 'candidate')], check=True, capture_output=True)
            # Any extra scope mutation still fails, despite the reviewed seed exception.
            original44 = env44.read_text()
            changed = {'original': dict(scopes['original'], max_severity='critical')}
            env44.write_text(credentials.replace_env(original44, changed))
            collision = subprocess.run(helper + ['--env', str(env44), '--scope-only', '--check-prepared', str(base / 'candidate')], capture_output=True)
            self.assertNotEqual(0, collision.returncode)
            self.assertIn(b'source collision', collision.stderr)
            env44.write_text(original44)
            subprocess.run(helper + ['--env', str(env44), '--scope-only', '--apply-prepared', str(base / 'candidate'),
                '--backup-dir', str(base / 'backup44')], check=True, capture_output=True)
            prepared = json.loads((base / 'candidate/candidate.json').read_text())
            self.assertEqual(prepared['scopes'], credentials.read_env(env44.read_text()))
            self.assertTrue(env44.read_text().startswith('LOCAL_PORT=44env\n'))

    def test_registered_consumers_require_exact_reviewed_project_and_never_reach_executor(self):
        scopes, callback, plan = self.fixture()
        consumer = 'consumer_b85a89f421694cdd9213c7cdf873e991'
        plan['registered_consumers'] = [{'consumer_id': consumer, 'project': 'project'}]
        plan['projects']['project']['verification_consumer_ids'] = [consumer]
        output, _, _ = credentials.prepare(scopes, callback, plan, lambda: 'fresh')
        self.assertEqual([consumer], output['original']['verification_consumer_ids'])
        self.assertEqual('verifier', output['original']['incident_role'])
        self.assertNotIn('verification_consumer_ids', output['fresh'])
        plan['registered_consumers'][0]['project'] = 'foreign'
        with self.assertRaises(ValueError):
            credentials.prepare(scopes, callback, plan, lambda: 'fresh')
        plan['registered_consumers'][0]['project'] = 'project'
        plan['projects']['project']['verification_consumer_ids'] = ['*']
        with self.assertRaises(ValueError):
            credentials.prepare(scopes, callback, plan, lambda: 'fresh')

    def test_callback_sync_rejects_executor_with_consumer_verification_grant(self):
        scopes, callback, plan = self.fixture()
        migrated, updated, _ = credentials.prepare(scopes, callback, plan, lambda: 'fresh')
        migrated['fresh']['verification_consumer_ids'] = ['consumer_bad']
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            env, cb = Path(directory) / 'env', Path(directory) / 'cb'
            env.write_text('NOTIFY_CENTER_TOKENS_JSON=' + json.dumps(migrated) + '\n')
            cb.write_text(json.dumps(updated)); cb.chmod(0o600)
            before = cb.read_bytes()
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/sync-health-callback-tokens.py'), str(env), str(cb)], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, cb.read_bytes())

    def test_supplemental_registered_policy_uses_no_bearer_and_requires_exact_review(self):
        plan = json.loads((ROOT / 'docs/consumer-lifecycle-registration-plan.json').read_text())
        grants = credentials.prepare_consumer_grants(plan)
        self.assertEqual('autotelegramviews', grants[0]['project'])
        self.assertNotIn('principal_id', grants[0])
        self.assertEqual('consumer_b85a89f421694cdd9213c7cdf873e991', grants[0]['consumer_id'])
        for update in [dict(project='foreign'), dict(incident_role='executor'),
                       dict(consumer_id='consumer_unreviewed'), dict(principal_id='claimed')]:
            changed = dict(plan, consumer_grants=[dict(plan['consumer_grants'][0], **update)])
            with self.assertRaises(ValueError):
                credentials.prepare_consumer_grants(changed)
        prior = dict(grants[0], consumer_id='consumer_existing')
        self.assertEqual([prior, grants[0]], credentials.merge_consumer_grants([prior], grants))
        with self.assertRaises(ValueError):
            credentials.merge_consumer_grants([dict(grants[0], verification_sources=['different'])], grants)

    def test_prepared_policy_hash_fences_unrelated_local_policy_change(self):
        scopes, callback, plan = self.fixture()
        fragment = json.loads((ROOT / 'docs/consumer-lifecycle-registration-plan.json').read_text())
        plan.update(registered_consumers=fragment['registered_consumers'], consumer_grants=fragment['consumer_grants'], consumer_executor_projects=fragment['consumer_executor_projects'])
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            base = Path(directory)
            env, cb, review = base / 'env', base / 'cb', base / 'plan'
            env.write_text('PORT=19400\nNOTIFY_CENTER_TOKENS_JSON=' + json.dumps(scopes) + '\n')
            env.chmod(0o600); cb.write_text(json.dumps(callback)); cb.chmod(0o600)
            review.write_text(json.dumps(plan))
            helper = [sys.executable, str(ROOT / 'deploy/consumer-lifecycle-credentials.py'), '--env', str(env), '--callback', str(cb)]
            subprocess.run(helper + ['--plan', str(review), '--prepare-dir', str(base / 'candidate')], check=True, capture_output=True)
            prepared = json.loads((base / 'candidate/candidate.json').read_text())
            self.assertEqual(fragment['consumer_grants'], prepared['consumer_grants'])
            self.assertNotIn('consumer_grants', prepared['callback_tokens'])
            original = env.read_text()
            env.write_text(credentials.replace_consumer_grants(original, [dict(fragment['consumer_grants'][0], project='foreign')]))
            result = subprocess.run(helper + ['--check-prepared', str(base / 'candidate')], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn(b'consumer grants differ', result.stderr)
            env.write_text(original)
            subprocess.run(helper + ['--apply-prepared', str(base / 'candidate'), '--backup-dir', str(base / 'backup')], check=True, capture_output=True)
            self.assertEqual(fragment['consumer_grants'], credentials.read_consumer_grants(env.read_text()))
            self.assertNotIn('consumer_grants', json.loads(cb.read_text()))
            self.assertTrue(env.read_text().startswith('PORT=19400\n'))

    def test_registered_only_project_has_separate_executor_callback(self):
        scopes, callback, plan = self.fixture()
        fragment = json.loads((ROOT / 'docs/consumer-lifecycle-registration-plan.json').read_text())
        plan.update(registered_consumers=fragment['registered_consumers'], consumer_grants=fragment['consumer_grants'], consumer_executor_projects=fragment['consumer_executor_projects'])
        plan['consumer_executor_projects'] = {'autotelegramviews': {'principal_id': 'executor:autotelegramviews', 'max_severity': 'notice'}}
        tokens = iter(['legacy-executor', 'registered-executor'])
        output, updated, _ = credentials.prepare(scopes, callback, plan, lambda: next(tokens))
        self.assertEqual('registered-executor', updated['tokens']['autotelegramviews'])
        executor = output['registered-executor']
        self.assertEqual('executor', executor['incident_role'])
        self.assertEqual('autotelegramviews', executor['project'])
        self.assertFalse(any(key.startswith('verification_') for key in executor))
        self.assertEqual({'original'}, {token for token, scope in output.items() if scope['incident_role'] != 'executor'})

    def test_registered_only_executor_real_callback_session_and_finish(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from unittest import mock
        from notification_center.core import NotificationCenter
        from notification_center.http_api import build_handler
        from notification_center.agent_job_helper import post_health_session_started
        scopes, callback, plan = self.fixture()
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            base = Path(directory)
            database = base / 'fixture.db'
            initial = NotificationCenter(database, {}, consumer_verifier_grants=[])
            consumer = initial.create_consumer(project='autotelegramviews', name='Isolated fixture',
                policy=[dict(id='root', platform='telegram', action='message', target={'chat_id': -100}, retry_interval_seconds=60, max_repeats=1)],
                max_severity='notice')
            initial._connection.close()
            grant = dict(consumer_id=consumer['id'], project='autotelegramviews', incident_role='verifier', verification_owned_sources=True)
            plan.update(registered_consumers=[dict(consumer_id=consumer['id'], project='autotelegramviews')], consumer_grants=[grant],
                consumer_executor_projects={'autotelegramviews': dict(principal_id='executor:atv', max_severity='notice')})
            tokens = iter(['legacy-executor', 'registered-executor'])
            prepared, updated, _ = credentials.prepare(scopes, callback, plan, lambda: next(tokens))
            center = NotificationCenter(database, prepared, consumer_verifier_grants=credentials.prepare_consumer_grants(plan))
            self.addCleanup(center._connection.close)
            event = dict(schema='notify.event.v1', kind='incident', severity='notice', project='autotelegramviews', recipient='health',
                title='Fixture failure', body='Fixture failure', dedup_key='probe', event_type='health.degraded', source_id='fixture-probe',
                host_id='fixture-host', signal_type='service', correlation_id='fixture-episode')
            incident = center.create_event(consumer['intake_token'], 'fixture-open', event)['incident_id']
            server = ThreadingHTTPServer(('127.0.0.1', 0), build_handler(center, 'fixture-probe', 'fixture-mcp'))
            self.assertGreater(server.server_port, 18100)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.addCleanup(server.server_close); self.addCleanup(thread.join); self.addCleanup(server.shutdown)
            updated['url'] = 'http://127.0.0.1:' + str(server.server_port)
            path = base / 'callback.json'; path.write_text(json.dumps(updated)); path.chmod(0o600)
            calls = []
            def runner(request, **options):
                calls.append(request.full_url)
                return urllib.request.urlopen(request, **options)
            with mock.patch.dict(os.environ, {'GPTADMIN_HEALTH_CALLBACK_FILE': str(path)}):
                result = post_health_session_started({'job_id': 'health-diagnosis', 'incident': {'id': incident, 'project': 'autotelegramviews'}},
                    {'harness': 'codex', 'session_id': 'fixture-native', 'profile': 'health-diagnosis'}, runner=runner)
            self.assertEqual('session_link_attached', result['status'])
            self.assertEqual([updated['url'] + '/v1/incidents/' + incident + '/health/session'], calls)
            body = dict(attempt_id='fixture-delivery', session_id='fixture-native', harness='codex', evidence_refs=['fixture-change'])
            request = urllib.request.Request(updated['url'] + '/v1/incidents/' + incident + '/health/work-finished',
                data=json.dumps(body).encode(), headers={'Authorization': 'Bearer registered-executor', 'Content-Type': 'application/json', 'Idempotency-Key': 'fixture-finish'}, method='POST')
            with urllib.request.urlopen(request, timeout=5) as response:
                finished = json.load(response)
            self.assertTrue(finished['request_id'])
            self.assertEqual('open', center.get_incident(incident)['state'])
            self.assertEqual('notice', center._token_scope(consumer['intake_token'])['max_severity'])
            self.assertEqual('consumer:' + consumer['id'], center._token_scope(consumer['intake_token'])['principal_id'])

    def test_reviewed_source_hash_resolution_is_exact_and_readonly(self):
        import hashlib
        import sqlite3
        scopes, callback, plan = self.fixture()
        source = 'monitor:https://private.invalid/path?opaque=fixture'
        wanted = hashlib.sha256(source.encode()).hexdigest()
        plan['projects']['project']['verification_source_sha256_refs'] = [wanted]
        with self.assertRaises(ValueError):
            credentials.prepare(scopes, callback, plan)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            database = Path(directory) / 'fixture.db'
            connection = sqlite3.connect(database)
            connection.executescript('CREATE TABLE incidents(id TEXT,project TEXT); CREATE TABLE events(incident_id TEXT,event_type TEXT,payload_json TEXT);')
            connection.execute('INSERT INTO incidents VALUES (?,?)', ('one', 'project'))
            for identity in [source, 'unreviewed-source']:
                connection.execute('INSERT INTO events VALUES (?,?,?)', ('one', 'health.degraded', json.dumps({'source_id': identity})))
            connection.commit(); connection.close()
            before = database.read_bytes()
            hydrated = credentials.resolve_reviewed_source_refs(plan, str(database))
            self.assertEqual(before, database.read_bytes())
            self.assertIn(source, hydrated['projects']['project']['verification_sources'])
            self.assertNotIn('unreviewed-source', hydrated['projects']['project']['verification_sources'])
            self.assertNotIn(source, json.dumps(plan))
            output, _, _ = credentials.prepare(scopes, callback, hydrated, lambda: 'fresh')
            self.assertIn(source, output['original']['verification_sources'])
            self.assertNotIn('verification_sources', output['fresh'])
