import unittest

from notification_center.incident_card import render


class Codec:
    def encode(self, action, incident_id, **_kwargs):
        return f"{action}:{incident_id}"


def incident_payload(**overrides):
    incident = {
        "id": "inc_" + "a" * 32,
        "project": "fleet-health",
        "kind": "incident",
        "severity": "critical",
        "state": "open",
        "title": "Сервер перегружен",
        "body": "На узле 100 закончилась память.",
    }
    incident.update(overrides.pop("incident", {}))
    return {"incident": incident, **overrides}


class IncidentCardHumanCopyTests(unittest.TestCase):
    def test_open_incident_leads_with_service_and_problem_without_placeholders(self):
        text, _ = render(incident_payload())

        self.assertEqual("Сервис: Fleet Health", text.splitlines()[0])
        self.assertEqual("Критично: Сервер перегружен", text.splitlines()[2])
        self.assertIn("На узле 100 закончилась память.", text)
        self.assertNotIn("Исполнитель не указан", text)
        self.assertNotIn("Диагностика: запуск исполнителя", text)
        self.assertNotIn("Инцидент принят. Восстановление пока не подтверждено", text)
        self.assertNotIn("Проект уведомления", text)

    def test_executor_requires_a_confirmed_session_identity(self):
        text, _ = render(incident_payload(execution_identity={"harness": "opencode"}))
        self.assertNotIn("Исполнитель:", text)
        self.assertNotIn("Модель:", text)
        self.assertNotIn("Рабочий каталог:", text)

        identity = {
            "harness": "opencode",
            "session_id": "ses_confirmed",
            "session_url": "https://agent.bezrabotnyi.com/#/session/opencode%3Ases_confirmed",
        }
        text, _ = render(incident_payload(execution_identity=identity))
        self.assertIn("Исполнитель: OpenCode", text)
        self.assertIn(identity["session_url"], text)

    def test_emergency_and_human_question_remain_actionable(self):
        payload = incident_payload(
            incident={"severity": "emergency", "title": "Внешний маршрут недоступен"},
            human_question={
                "request_id": "req-1",
                "incident_id": "inc_" + "a" * 32,
                "message": "Переключить маршрут на резервный?",
                "mode": "question",
                "state": "pending",
            },
        )

        text, keyboard = render(payload, Codec())

        self.assertEqual("Сервис: Fleet Health", text.splitlines()[0])
        self.assertIn("Аварийная ситуация", text)
        self.assertIn("Вопрос помощника: Переключить маршрут на резервный?", text)
        self.assertIn("Ответьте на эту карточку сообщением или по телефону.", text)
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        self.assertTrue(any(button["text"] == "Прочитал" for button in buttons))

    def test_resolved_card_keeps_confirmed_recovery(self):
        text, _ = render(incident_payload(
            incident={"state": "resolved"},
            health_outcome={"status": "healthy", "observed_state": "healthy"},
        ))

        self.assertIn("Мониторинг подтвердил восстановление", text)
        self.assertIn("действий от вас не требуется", text)

    def test_resolved_controls_never_offer_ai_but_keep_existing_session_link(self):
        identity = {
            'harness': 'opencode', 'session_id': 'ses_confirmed',
            'session_url': 'https://agent.bezrabotnyi.com/#/session/opencode%3Ases_confirmed',
        }
        for key in (None, 'health_session', 'ai_session', 'execution_identity'):
            with self.subTest(session_field=key):
                payload = incident_payload(incident={'state': 'resolved'}, **({key: identity} if key else {}))
                _, keyboard = render(payload, Codec())
                buttons = [button for row in keyboard['inline_keyboard'] for button in row]
                self.assertFalse(any(b.get('callback_data', '').startswith('ai:') for b in buttons))
                self.assertTrue(any(b['text'] == 'Настроить это уведомление' for b in buttons))
                if key:
                    self.assertTrue(any(b.get('url') == identity['session_url'] for b in buttons))
        _, keyboard = render(incident_payload(), Codec())
        self.assertTrue(any(b.get('callback_data', '').startswith('ai:') for row in keyboard['inline_keyboard'] for b in row))


if __name__ == "__main__":
    unittest.main()
