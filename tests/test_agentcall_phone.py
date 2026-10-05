"""Tests for the local AI incident-call adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from notification_center.agentcall_phone import AgentCallPhoneAdapter
from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, android_phone_from_environment


class AgentCallPhoneAdapterTests(unittest.TestCase):
    def test_incident_context_is_spoken_twice_through_local_control_socket(self) -> None:
        requests: list[dict[str, object]] = []

        def requester(_path: str, payload: bytes, _timeout: float) -> bytes:
            requests.append(json.loads(payload))
            return b'{"ok":true,"receipt_id":"call-123","call_id":"call-123"}\n'

        adapter = AgentCallPhoneAdapter("/run/agentcall/control.sock", requester=requester)
        result = adapter.phone_call({
            "incident": {
                "id": "incident-1",
                "severity": "critical",
                "title": "Сервер 88 недоступен",
                "body": "Три проверки завершились ошибкой.",
            }
        })

        self.assertTrue(adapter.can_phone_call)
        self.assertEqual("call-123", result["receipt_id"])
        self.assertEqual(2, requests[0]["repeat"])
        self.assertIn("Сломалось что-то", requests[0]["message"])
        self.assertIn("Сервер 88 недоступен", requests[0]["message"])
        self.assertIn("Три проверки", requests[0]["context"])

    def test_environment_prefers_agentcall_over_legacy_adb_settings(self) -> None:
        with mock.patch.dict(os.environ, {
            "AGENTCALL_PHONE_SOCKET": "/run/agentcall/control.sock",
            "ANDROID_ADB_SERIAL": "R5CR702SRFP",
            "ANDROID_TELEGRAM_TARGET": "legacy",
        }, clear=True):
            self.assertIsInstance(android_phone_from_environment(), AgentCallPhoneAdapter)

    def test_lead_notification_is_not_described_as_an_outage(self) -> None:
        requests: list[dict[str, object]] = []

        def requester(_path: str, payload: bytes, _timeout: float) -> bytes:
            requests.append(json.loads(payload))
            return b'{"ok":true,"receipt_id":"call-456"}\n'

        adapter = AgentCallPhoneAdapter("/run/agentcall/control.sock", requester=requester)
        adapter.phone_call({
            "incident": {
                "id": "lead-1",
                "severity": "critical",
                "event_type": "lead.created",
                "title": "Новая заявка с лендинга",
                "body": "Имя: Тест",
            }
        })

        self.assertIn("Новая заявка", requests[0]["message"])
        self.assertNotIn("Сломалось что-то", requests[0]["message"])

    def test_spoken_ack_is_bound_to_phone_receipt_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notify.sqlite3"
            tokens = {"test": {"project": "test", "max_severity": "emergency"}}
            center = NotificationCenter(path, tokens, default_quiet_hours=[])
            event = {"schema": "notify.event.v1", "project": "test", "recipient": "me", "kind": "incident",
                     "severity": "critical", "title": "Связь", "body": "Проверка", "dedup_key": "voice"}
            created = center.create_event("test", "voice-ack", event)
            unrelated = center.create_event("test", "other", {**event, "dedup_key": "other"})
            center.complete_delivery(created["initial_delivery_id"], "sent")
            call_delivery = center.schedule_escalation(created["incident_id"], "android.phone.call", 0)
            center.complete_delivery(call_delivery, "sent", result={"call_id": "call-123"})
            future = center.schedule_escalation(created["incident_id"], "phone.call", 10**12)
            adapter = AgentCallPhoneAdapter("/run/agentcall/control.sock")
            transcript = {"event": "transcript_final", "speaker": "remote", "callId": "call-123"}
            for phrase in ("Не понял", "Я не услышал", "Повтори, пожалуйста", "Сколько будет два плюс два?"):
                self.assertFalse(adapter.acknowledge_voice_event(center, {**transcript, "text": phrase}))
            self.assertFalse(adapter.acknowledge_voice_event(center, {**transcript, "callId": "unknown", "text": "Да, я услышал"}))
            self.assertFalse(adapter.acknowledge_voice_event(center, {**transcript, "speaker": "agent", "text": "Да, я услышал"}))
            self.assertTrue(adapter.acknowledge_voice_event(center, {**transcript, "text": "Да, я услышал"}))
            self.assertTrue(adapter.acknowledge_voice_event(center, {**transcript, "text": "Да, я услышал"}))
            restored = NotificationCenter(path, tokens)
            self.assertEqual("acknowledged", restored.get_incident(created["incident_id"])["state"])
            self.assertEqual("open", restored.get_incident(unrelated["incident_id"])["state"])
            self.assertEqual("cancelled", restored._connection.execute("SELECT status FROM deliveries WHERE id=?", (future,)).fetchone()[0])
            self.assertEqual(1, restored._connection.execute("SELECT COUNT(*) FROM audit_events WHERE incident_id=? AND type='incident_acknowledged'", (created["incident_id"],)).fetchone()[0])

    def test_phone_timeout_does_not_redial_and_success_preserves_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            center = NotificationCenter(Path(directory) / "notify.sqlite3", {"test": {"project": "test", "max_severity": "emergency"}}, default_quiet_hours=[])
            event = {"schema": "notify.event.v1", "project": "test", "recipient": "me", "kind": "incident",
                     "severity": "critical", "title": "Связь", "body": "Проверка", "dedup_key": "voice"}
            for outcome in (TimeoutError("late socket response"), {"ok": True, "call_id": "call-123"}):
                created = center.create_event("test", str(outcome), {**event, "dedup_key": str(outcome)})
                center.complete_delivery(created["initial_delivery_id"], "sent")
                center.schedule_escalation(created["incident_id"], "android.phone.call", 0)
                phone = mock.Mock()
                phone.phone_call.side_effect = outcome if isinstance(outcome, Exception) else None
                phone.phone_call.return_value = outcome
                worker = DeliveryWorker(center, mock.Mock(active_modes=None), android_phone=phone,
                                        android_phone_quiet_start_hour=0, android_phone_quiet_end_hour=0)
                worker.run_once()
                row = center._connection.execute("SELECT status,result_json FROM deliveries WHERE incident_id=? AND channel='android.phone.call'", (created["incident_id"],)).fetchone()
                self.assertEqual("uncertain" if isinstance(outcome, Exception) else "sent", row["status"])
                if not isinstance(outcome, Exception):
                    self.assertEqual("call-123", json.loads(row["result_json"])["call_id"])
            self.assertFalse(any(row["channel"] in {"phone.call", "android.phone.call"}
                                 for row in center.claim_due_deliveries(now_epoch=10**12)))


if __name__ == "__main__":
    unittest.main()
