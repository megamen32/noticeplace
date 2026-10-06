"""Native receipt identity, source stops and read-only archive scope."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.agent_job_helper import _health_session_name
from notification_center.health_session_lifecycle import HealthSessionArchiver


class HealthSessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name) / 'db', {'token': {'project': 'fleet', 'max_severity': 'critical'}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.event = {'schema': 'notify.event.v1', 'project': 'fleet', 'recipient': 'me', 'kind': 'incident',
            'severity': 'critical', 'title': 'Сайт недоступен', 'body': 'Проверяем', 'dedup_key': 'site',
            'event_type': 'health.degraded', 'source_id': 'site', 'host_id': '100', 'signal_type': 'http', 'correlation_id': 'episode'}
        self.incident = self.center.create_event('token', 'new', self.event)['incident_id']
        self.center.record_health_agent_session(self.incident, 'diag', '', 'codex', 'native-id', 'https://agent.bezrabotnyi.com', stage='health-diagnosis')
        self.profile = {'url': 'http://127.0.0.1:18787/api/sessions/new-or-resume', 'harness': 'codex', 'cwd': self.tmp.name}

    def resolve(self):
        self.center.resolve_event('token', 'resolved', {**self.event, 'action': 'resolve', 'event_type': 'health.recovered'})

    def run_archive(self, held=False, missing=False, rejected=False):
        calls = []
        def read(request, *_args, **_kwargs):
            calls.append(request)
            if '/coordination/context?' in request.full_url:
                self.assertIn('consume=0', request.full_url)
                return {} if missing else {'humanStopHeld': held}
            if '/details?' in request.full_url:
                return {'messages': [{'role': 'assistant', 'text': json.dumps({'status': 'diagnosis_complete', 'diagnosis': 'Проверка завершена'})}]}
            self.assertTrue(request.full_url.endswith('/api/sessions/codex/native-id/archive'))
            self.assertEqual(b'{}', request.data)
            return {'ok': not rejected, 'sessionId': 'native-id'}
        with patch('notification_center.health_session_lifecycle._read_json_request', side_effect=read):
            result = HealthSessionArchiver(self.center, self.profile).run_once()
        return result, calls

    def test_completed_resolved_controller_archived_once_history_retained(self):
        self.resolve()
        self.assertEqual(1, self.run_archive()[0])
        self.assertEqual((0, []), self.run_archive())
        self.assertEqual('native-id', self.center.latest_agent_session(self.incident)['session_id'])
        self.assertEqual('resolved', self.center.get_incident(self.incident)['state'])

    def test_open_and_ack_are_not_cleanup_candidates(self):
        self.assertEqual((0, []), self.run_archive())
        self.center.acknowledge(self.incident, 'operator')
        self.assertEqual((0, []), self.run_archive())

    def test_active_job_prevents_archive(self):
        self.resolve()
        self.center._schedule_delivery(self.incident, 'gptadmin.agent:health-diagnosis', 'active', 0)
        self.assertEqual((0, []), self.run_archive())

    def test_held_or_unknown_stop_gate_never_posts(self):
        for option in ('held', 'missing'):
            with self.subTest(option=option):
                self.center._connection.execute("DELETE FROM audit_events WHERE type='health_controller_archive'")
                self.resolve()
                count, calls = self.run_archive(**{option: True})
                self.assertEqual(0, count)
                self.assertTrue(calls)
                self.assertTrue(all(request.get_method() == 'GET' for request in calls))

    def test_unsupported_harness_and_synthetic_are_excluded(self):
        self.resolve()
        self.center._connection.execute("UPDATE events SET payload_json=json_set(payload_json,'$.harness','zcode') WHERE event_type='health.diagnosis_session_started'")
        self.assertEqual((0, []), self.run_archive())

    def test_synthetic_and_remediation_sessions_are_not_archived(self):
        self.resolve()
        with patch.object(self.center, 'health_incident_is_synthetic', return_value=True):
            self.assertEqual((0, []), self.run_archive())
        self.center._connection.execute("UPDATE events SET event_type='health.remediation_session_started' WHERE event_type='health.diagnosis_session_started'")
        self.assertEqual((0, []), self.run_archive())

    def test_missing_completed_result_does_not_archive(self):
        self.resolve()
        with patch('notification_center.health_session_lifecycle._extract_health_diagnosis', return_value=None):
            count, calls = self.run_archive()
        self.assertEqual(0, count)
        self.assertTrue(all(request.get_method() == 'GET' for request in calls))

    def test_archive_error_has_no_fallback_and_is_audited(self):
        self.resolve()
        result, calls = self.run_archive(rejected=True)
        self.assertEqual(0, result)
        self.assertEqual(1, sum(request.get_method() == 'POST' for request in calls))
        self.assertEqual((0, []), self.run_archive())

    def test_daemon_uses_existing_local_seam_without_reading_private_hub_profile(self):
        self.resolve()
        with patch('notification_center.health_session_lifecycle._read_json_request', return_value={'humanStopHeld': True}) as read, \
             patch('notification_center.agent_job_helper._load_profile', side_effect=PermissionError('private profile')):
            self.assertEqual(0, HealthSessionArchiver(self.center).run_once())
        self.assertIn('http://127.0.0.1:18787/api/coordination/context?', read.call_args.args[0].full_url)

    def test_archive_does_not_send_receipts_to_an_external_configured_host(self):
        self.resolve()
        with patch.dict('os.environ', {'NOTIFY_HEALTH_REMEDIATION_URL': 'https://other.example/api/sessions/new-or-resume'}), \
             patch('notification_center.health_session_lifecycle._read_json_request') as read:
            self.assertEqual(0, HealthSessionArchiver(self.center).run_once())
        read.assert_not_called()

    def test_human_title_preserves_incident_and_bounded_secondary_identity(self):
        profile = {'name': 'health_diagnosis_100'}
        event = {'incident': {'id': 'one', 'title': 'Нагрузка\nна сервере 100 token=secret-value'}}
        title = _health_session_name(profile, 'health-diagnosis', event)
        self.assertTrue(title.startswith('Диагностика: Нагрузка на сервере 100'))
        self.assertNotIn('secret-value', title)
        self.assertNotEqual(title, _health_session_name(profile, 'health-diagnosis', {'incident': {**event['incident'], 'id': 'two'}}))
        self.assertLessEqual(len(_health_session_name(profile, 'health-orchestrator', {'incident': {'title': 'я' * 500}})), 128)
        self.assertEqual(profile['name'], _health_session_name(profile, 'other-job', event))
