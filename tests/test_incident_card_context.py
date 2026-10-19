"""Focused integration: monitor facts survive intake and Telegram projection.

Detects the English-only external-site incident losing its target and evidence.
Expected 2 seconds, emergency maximum 20 seconds; no external network.
"""
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.incident_card import render


class IncidentCardContextTests(unittest.TestCase):
    def test_external_site_keeps_target_reason_and_launch_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            center = NotificationCenter(Path(directory) / 'notice.sqlite3',
                {'p': {'project': 'fleet-health', 'max_severity': 'emergency',
                       'agent_jobs': ['health-diagnosis']}}, default_quiet_hours=[])
            self.addCleanup(center._connection.close)
            created = center.create_event('p', 'site-context', {
                'schema': 'notify.event.v1', 'project': 'fleet-health',
                'recipient': 'external-site-monitor', 'kind': 'incident',
                'severity': 'critical', 'title': 'External site down',
                'body': 'artem.bezrabotnyi.com (curl=28, http=0)\n'
                        'via=proxy-1 SSL connection timeout | via=direct Resolving timed out',
                'dedup_key': 'external-site:artem.bezrabotnyi.com',
                'event_type': 'health.external_site',
                'source_id': 'external-site:artem.bezrabotnyi.com',
                'host_id': 'artem.bezrabotnyi.com', 'signal_type': 'https',
                'correlation_id': 'site-context-episode',
            })
            incident_id = created['incident_id']
            text, _ = render(center.incident_card_payload(incident_id))
            self.assertIn('artem.bezrabotnyi.com', text)
            self.assertIn('TLS', text)
            self.assertIn('DNS', text)
            self.assertIn('via=proxy-1', text)
            self.assertIn('запуск исполнителя ещё не подтверждён', text)
            self.assertNotIn('Исполнитель не указан', text)
            self.assertNotIn('исправление выполняются', text)
            center.record_health_agent_session(incident_id, 'launch-context', 'health-diagnosis',
                'opencode', 'ses_context', 'https://agent.bezrabotnyi.com',
                stage='health-diagnosis', model='minimax-coding-plan/MiniMax-M3.1-Flash')
            text, _ = render(center.incident_card_payload(incident_id))
            self.assertIn('artem.bezrabotnyi.com', text)
            self.assertIn('Исполнитель: OpenCode', text)
            self.assertIn('MiniMax-M3.1-Flash', text)
            self.assertIn('opencode%3Ases_context', text)

    def test_english_notification_keeps_original_title_and_body(self):
        text, _ = render({'incident': {'id': 'i', 'project': 'p', 'kind': 'notification',
            'severity': 'important', 'state': 'open', 'title': 'Worker failed',
            'body': 'worker.example: connection refused token=secret-value'}})
        self.assertIn('Worker failed', text)
        self.assertIn('worker.example: connection refused', text)
        self.assertNotIn('secret-value', text)

