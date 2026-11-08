"""DB-authenticated exact-source diagnosis policy; no adapters or external calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('grant_credentials', ROOT/'deploy/consumer-lifecycle-credentials.py')
credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credentials)


class ConsumerGrantDiagnosisPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name)/'state.db', {}, consumer_verifier_grants=[], default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.consumer = self.center.create_consumer('openwrt-router-health', 'Isolated fixture',
            [dict(id='root', platform='telegram', action='message', target={'chat_id': -100},
                  retry_interval_seconds=60, max_repeats=1)], max_severity='important', quiet_hours=[])
        self.token = self.consumer['intake_token']
        self.base = dict(consumer_id=self.consumer['id'], project='openwrt-router-health',
            incident_role='verifier', verification_owned_sources=False, verification_sources=['router-health-lite'])
        self.grant = dict(self.base, agent_jobs=['health-diagnosis'], automatic_health_diagnosis=True, suppress_owner_delivery=True)
        self.event = dict(schema='notify.event.v1', kind='incident', severity='important', project='openwrt-router-health',
            recipient='health', title='Isolated failure', dedup_key='isolated', event_type='health.degraded',
            source_id='router-health-lite', host_id='fixture100', signal_type='service', correlation_id='isolated')

    def prepare(self, grant):
        return credentials.prepare_consumer_grants(dict(registered_consumers=[dict(consumer_id=grant['consumer_id'], project=grant['project'])], consumer_grants=[grant]))

    def load(self, grants):
        self.center._consumer_verifier_grants = self.center._load_consumer_verifier_grants(grants)

    def test_exact_db_consumer_grant_schedules_one_quiet_diagnosis(self):
        self.load([self.grant])
        scope = self.center._token_scope(self.token)
        self.assertEqual('important', scope['max_severity'])
        self.assertEqual('consumer:'+self.consumer['id'], scope['principal_id'])
        first = self.center.create_event(self.token, 'one', self.event)
        repeat = self.center.create_event(self.token, 'two', self.event)
        self.assertTrue(first['agent_job_delivery_id'])
        self.assertEqual(first['agent_job_delivery_id'], repeat['agent_job_delivery_id'])
        rows = self.center._connection.execute('SELECT channel FROM deliveries').fetchall()
        self.assertEqual(['gptadmin.agent:health-diagnosis'], [r['channel'] for r in rows])
        self.assertFalse(first['initial_delivery_id'])
        with self.assertRaises(AuthorizationError):
            self.center.create_event(self.token, 'ceiling', dict(self.event, severity='critical'))
        self.center._connection.execute('UPDATE consumers SET token_hash=? WHERE id=?', ('revoked', self.consumer['id']))
        with self.assertRaises(AuthorizationError): self.center._token_scope(self.token)

    def test_helper_roundtrip_preserves_policy_and_old_verifier(self):
        for grant in (self.base, self.grant, dict(self.grant, automatic_health_diagnosis=False, suppress_owner_delivery=False),
                      dict(self.grant, agent_jobs=[])):
            with self.subTest(policy=sorted(grant)):
                prepared=self.prepare(grant)
                self.assertEqual([grant], prepared)
                encoded=credentials.replace_consumer_grants('OTHER=untouched\n',prepared)
                loaded=self.center._load_consumer_verifier_grants(credentials.read_consumer_grants(encoded))
                self.assertEqual(prepared,loaded)

    def test_foreign_source_including_explicit_job_cannot_launch(self):
        self.load([self.grant])
        for explicit in ({}, {'agent_job':'health-diagnosis'}):
            result=self.center.create_event(self.token, 'foreign'+str(bool(explicit)), dict(self.event, source_id='foreign', **explicit))
            self.assertIsNone(result['agent_job_delivery_id'])
        self.assertEqual(0,self.center._connection.execute('SELECT count(*) FROM deliveries').fetchone()[0])

    def test_foreign_consumer_project_and_ambiguous_grants_cannot_launch(self):
        for grants in ([dict(self.grant,consumer_id='other')], [dict(self.grant,project='foreign')], [self.grant,self.grant]):
            with self.subTest(grants=len(grants)):
                self.load(grants)
                self.assertNotIn('agent_jobs',self.center._token_scope(self.token))
        self.load([self.base])
        result=self.center.create_event(self.token,'untrusted-payload',dict(self.event,agent_jobs=['health-diagnosis'],automatic_health_diagnosis=True,suppress_owner_delivery=True))
        self.assertIsNone(result['agent_job_delivery_id'])

    def test_strict_policy_validation_matches_helper(self):
        invalid = [dict(agent_jobs='health-diagnosis'),dict(agent_jobs=['health-remediation']),
            dict(agent_jobs=['health-diagnosis','health-diagnosis']),dict(agent_jobs=[1]),
            dict(automatic_health_diagnosis='true'),dict(automatic_health_diagnosis=1),
            dict(suppress_owner_delivery='false'),dict(suppress_owner_delivery=0),
            dict(incident_role='executor'),dict(verification_sources=[]),dict(verification_sources=['*']),
            dict(verification_sources=['source\n']),dict(consumer_id=' '),dict(project='project\x7f')]
        for update in invalid:
            grant=dict(self.grant,**update)
            with self.subTest(update=update):
                with self.assertRaises(ValidationError):self.load([grant])
                with self.assertRaises(ValueError):self.prepare(grant)

    def test_explicit_false_disables_auto_and_old_executor_grant_stays_ignored(self):
        self.load([dict(self.grant,automatic_health_diagnosis=False)])
        result=self.center.create_event(self.token,'disabled',dict(self.event,agent_job='health-diagnosis'))
        self.assertIsNone(result['agent_job_delivery_id'])
        self.load([dict(self.base,incident_role='executor')])
        self.assertNotIn('agent_jobs',self.center._token_scope(self.token))
        self.assertNotIn('incident_role',self.center._token_scope(self.token))

if __name__=='__main__':unittest.main()
