"""One incident card follows accepted sessions and source recovery."""
import json
import io
import tempfile
import unittest
import urllib.parse
import urllib.error
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.http_api import TelegramSender
from notification_center.telegram_interactions import TelegramActionCodec


class HealthCardUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.center = NotificationCenter(Path(self.tmp.name) / "state.db",
            {"producer": {"project": "fleet", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.event = {"schema": "notify.event.v1", "project": "fleet", "recipient": "me",
            "kind": "incident", "severity": "critical", "title": "Повышена нагрузка на сервере 100",
            "body": "Проверяем причину.", "dedup_key": "load", "event_type": "health.degraded",
            "source_id": "fleet:100", "host_id": "100", "signal_type": "load", "correlation_id": "episode"}
        self.created = self.center.create_event("producer", "intake", self.event)
        self.incident = self.created["incident_id"]
        self.center.complete_delivery(self.created["initial_delivery_id"], "sent",
            result={"chat_id": "-100123", "message_id": 42})

    def send(self, delivery_id):
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        payload = self.center.delivery_payload(dict(row))
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok":true,"result":{"message_id":42,"chat":{"id":-100123}}}'
        def open_request(request, **kwargs):
            requests.append((request.full_url, urllib.parse.parse_qs(request.data.decode())))
            return Response()
        sender = TelegramSender("test", "-100123", action_codec=TelegramActionCodec("x" * 32))
        with patch("notification_center.http_api.urllib.request.urlopen", side_effect=open_request):
            receipt = sender.send(payload)
        self.assertTrue(requests[0][0].endswith("/editMessageText"))
        self.assertEqual(["42"], requests[0][1]["message_id"])
        self.assertTrue(receipt["edited_in_place"])
        self.center.complete_delivery(delivery_id, "sent", result=receipt)
        return requests[0][1]

    def test_diagnosis_planner_failure_and_recovery_update_same_card(self):
        for stage in ("health-diagnosis", "health-orchestrator"):
            session = self.center.record_health_agent_session(self.incident, stage, "", "codex", stage,
                "https://agent.bezrabotnyi.com", stage=stage)
            data = self.send(session["session_delivery_id"])
            self.assertIn("Ссылка на сессию", data["text"][0])
            self.assertNotIn("Выбрано: решение через AI", data["text"][0])
        failed = self.center.schedule_health_outcome(self.incident, "failed", "failed-job")
        self.assertIn("Работа остановилась", self.send(failed)["text"][0])
        self.center.resolve_event("producer", "recovery", {
            **self.event, "action": "resolve", "event_type": "health.recovered"})
        recovery = self.center._connection.execute("SELECT id FROM deliveries WHERE json_extract(target_json,'$.health_outcome.status')='recovered'").fetchone()
        self.assertIn("Состояние восстановилось", self.send(recovery["id"])["text"][0])

    def test_late_session_delivery_preserves_recovered_state(self):
        session = self.center.record_health_agent_session(self.incident, "late", "", "codex", "late",
            "https://agent.bezrabotnyi.com", stage="health-diagnosis")
        self.center.resolve_event("producer", "recovery", {
            **self.event, "action": "resolve", "event_type": "health.recovered"})
        self.assertIn("Состояние восстановилось", self.send(session["session_delivery_id"])["text"][0])

    def test_severity_phase_change_never_claims_recovery_even_for_late_card(self):
        session = self.center.record_health_agent_session(self.incident, 'phase', '', 'codex', 'phase',
            'https://agent.bezrabotnyi.com', stage='health-diagnosis')
        self.center.resolve_event('producer', 'phase-change', {**self.event, 'action': 'resolve',
            'event_type': 'health.phase_changed', 'next_severity': 'important'})
        for row in self.center._connection.execute("SELECT id FROM deliveries WHERE json_extract(target_json,'$.health_outcome.status')='phase_changed'"):
            text = self.send(row['id'])['text'][0]
            self.assertIn('Сигнал ещё активен', text)
            self.assertNotIn('Состояние восстановилось', text)
        text = self.send(session['session_delivery_id'])['text'][0]
        self.assertIn('Сигнал ещё активен', text)

    def test_unconfirmed_receipt_does_not_choose_edit_destination(self):
        self.center._connection.execute("UPDATE deliveries SET status='uncertain',result_json=NULL WHERE id=?",
            (self.created['initial_delivery_id'],))
        session = self.center.record_health_agent_session(self.incident, "no-receipt", "", "codex", "no-receipt",
            "https://agent.bezrabotnyi.com", stage="health-diagnosis")
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE id=?", (session['session_delivery_id'],)).fetchone()
        self.assertNotIn('incident_card', self.center.delivery_payload(dict(row))['target'])

    def test_identical_edit_is_success_but_other_errors_propagate(self):
        session = self.center.record_health_agent_session(self.incident, 'same', '', 'codex', 'same',
            'https://agent.bezrabotnyi.com', stage='health-diagnosis')
        row = self.center._connection.execute('SELECT * FROM deliveries WHERE id=?', (session['session_delivery_id'],)).fetchone()
        payload = self.center.delivery_payload(dict(row))
        sender = TelegramSender('test', '-100123', action_codec=TelegramActionCodec('x' * 32))
        for description in ('Bad Request: message is not modified: specified new message content and reply markup are exactly the same', 'Bad Request: message to edit not found'):
            error = urllib.error.HTTPError('test', 400, 'Bad Request', {}, io.BytesIO(json.dumps({'description': description}).encode()))
            with patch('notification_center.http_api.urllib.request.urlopen', side_effect=error):
                if 'not modified' in description:
                    receipt = sender.send(payload)
                    self.assertEqual(42, receipt['message_id'])
                    self.assertTrue(receipt['edited_in_place'])
                else:
                    with self.assertRaises(urllib.error.HTTPError):
                        sender.send(payload)
