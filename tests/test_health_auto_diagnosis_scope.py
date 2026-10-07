import json
from pathlib import Path
import tempfile
import unittest

from notification_center.admin import AdminConfigStore
from notification_center.core import AuthorizationError, NotificationCenter, ValidationError
from notification_center.health_workflow import HealthWorkflow


class AutomaticDiagnosisScopeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def center(self, setting=None, allowed=True):
        scope = {'project': 'codex-daemon.server-100', 'max_severity': 'important',
                 'agent_jobs': ['health-diagnosis'] if allowed else []}
        if setting is not None:
            scope['automatic_health_diagnosis'] = setting
        center = NotificationCenter(self.root / 'center.sqlite3', {'fixture-token': scope},
                                    default_quiet_hours=[])
        self.addCleanup(center._connection.close)
        return center

    def signal(self):
        return {'schema': 'notify.event.v1', 'project': 'codex-daemon.server-100',
                'recipient': 'me', 'kind': 'incident', 'severity': 'important',
                'event_type': 'health.degraded', 'title': 'Проверка подключения ChatGPT',
                'body': 'Синтетическая проверка контракта без обращения к серверу.',
                'dedup_key': 'fixture:codex-control', 'source_id': 'codex-daemon-watchdog',
                'host_id': 'server-100', 'signal_type': 'control-rpc-timeout',
                'correlation_id': 'fixture-correlation'}

    def test_external_diagnosis_does_not_launch_duplicate_or_suppress_notification(self):
        center = self.center(False)
        created = HealthWorkflow(center).intake_signal('fixture-token', 'fixture-create', self.signal())
        self.assertIsNone(created['agent_job_delivery_id'])
        self.assertIsNotNone(created['initial_delivery_id'])
        self.assertFalse(center.health_notification_review_required(created['incident_id']))

    def test_default_existing_producers_keep_automatic_diagnosis_and_review(self):
        center = self.center()
        created = HealthWorkflow(center).intake_signal('fixture-token', 'fixture-create', self.signal())
        self.assertIsNotNone(created['agent_job_delivery_id'])
        self.assertIsNone(created['initial_delivery_id'])
        self.assertTrue(center.health_notification_review_required(created['incident_id']))

    def test_explicit_authorized_manual_diagnosis_remains_allowed(self):
        center = self.center(False)
        event = self.signal()
        event['agent_job'] = 'health-diagnosis'
        created = center.create_event('fixture-token', 'fixture-manual', event)
        self.assertIsNotNone(created['agent_job_delivery_id'])
        self.assertIsNotNone(created['initial_delivery_id'])

    def test_explicit_unscoped_job_stays_forbidden(self):
        center = self.center(False, allowed=False)
        event = self.signal()
        event['agent_job'] = 'health-diagnosis'
        with self.assertRaises(AuthorizationError):
            center.create_event('fixture-token', 'fixture-forbidden', event)

    def test_manual_ai_button_remains_available(self):
        center = self.center(False)
        created = HealthWorkflow(center).intake_signal('fixture-token', 'fixture-create', self.signal())
        action = center.apply_telegram_action(created['incident_id'], 'ai', 'fixture-human')
        self.assertIsNotNone(action['agent_job_delivery_id'])

    def test_old_waiting_review_audit_does_not_suppress_external_project(self):
        center = self.center(False)
        created = HealthWorkflow(center).intake_signal('fixture-token', 'fixture-create', self.signal())
        with center._connection:
            center._audit(created['incident_id'], 'health_notification_waiting_ai', 'fixture', {})
        self.assertFalse(center.health_notification_review_required(created['incident_id']))

    def test_scope_policy_survives_native_admin_normalization(self):
        primary = self.root / 'primary.env'
        primary.write_text('NOTIFY_CENTER_TOKENS_JSON=' + json.dumps({'fixture-token': {
            'project': 'codex-daemon.server-100', 'max_severity': 'important',
            'agent_jobs': ['health-diagnosis'], 'automatic_health_diagnosis': False}}) + '\n')
        store = AdminConfigStore(primary, self.root / 'routes.env', self.root / 'state', restart=lambda: None)
        self.assertIs(store._scopes()['fixture-token']['automatic_health_diagnosis'], False)
        store.create_project('unrelated.fixture', 'notice', 'fixture-human')
        store.set_project_severity('unrelated.fixture', 'important', 'fixture-human')
        persisted = json.loads(primary.read_text().split('=', 1)[1])
        self.assertIs(persisted['fixture-token']['automatic_health_diagnosis'], False)

    def test_nonboolean_policy_is_rejected(self):
        primary = self.root / 'primary.env'
        primary.write_text('NOTIFY_CENTER_TOKENS_JSON=' + json.dumps({'fixture-token': {
            'project': 'codex-daemon.server-100', 'max_severity': 'important',
            'automatic_health_diagnosis': 'false'}}) + '\n')
        store = AdminConfigStore(primary, self.root / 'routes.env', self.root / 'state', restart=lambda: None)
        with self.assertRaises(ValidationError):
            store._scopes()

    def test_actual_health_contract_rejects_missing_recipient(self):
        center = self.center(False)
        event = self.signal()
        event.pop('recipient')
        with self.assertRaisesRegex(ValidationError, 'recipient'):
            HealthWorkflow(center).intake_signal('fixture-token', 'fixture-invalid', event)


if __name__ == '__main__':
    unittest.main()
