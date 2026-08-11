from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import telegram_inline_keyboard
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


class AgentHerderChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"producer": {"project": "hermes", "max_severity": "critical"}},
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _event(self, **extra: object) -> dict[str, object]:
        return {
            "schema": "notify.event.v1",
            "project": "hermes",
            "recipient": "me",
            "kind": "notification",
            "severity": "important",
            "title": "Choose next step",
            "body": "MiniMax is uncertain.",
            "dedup_key": "agent-herder:choice:test",
            "choice_request_id": "11111111-1111-4111-8111-111111111111",
            "choices": [
                {"choice_id": "inspect", "label": "Inspect failed unit"},
                {"choice_id": "correlate", "label": "Correlate failure timing"},
                {"choice_id": "assess", "label": "Assess recurrence risk"},
            ],
            **extra,
        }

    def test_choice_event_renders_signed_buttons_and_posts_opaque_selection(self) -> None:
        created = self.center.create_event("producer", "choice-event", self._event())
        payload = self.center.delivery_payload({"incident_id": created["incident_id"]})
        codec = TelegramActionCodec("x" * 32)
        keyboard = telegram_inline_keyboard(codec, payload["incident"], payload["choices"])

        self.assertEqual(
            [["Inspect failed unit"], ["Correlate failure timing"], ["Assess recurrence risk"]],
            [[button["text"] for button in row] for row in keyboard["inline_keyboard"]],
        )
        callback_data = keyboard["inline_keyboard"][1][0]["callback_data"]
        self.assertNotIn("nextGoal", callback_data)
        self.assertEqual(("choice", created["incident_id"], "correlate"), codec.decode(callback_data))

        calls: list[tuple[str, str, str]] = []

        def choice_callback(request_id: str, choice_id: str, actor: str) -> dict[str, object]:
            calls.append((request_id, choice_id, actor))
            return {"status": "resumed", "choice_id": choice_id}

        updates = [{
            "update_id": 501,
            "callback_query": {
                "id": "callback-501",
                "from": {"id": 42},
                "data": callback_data,
                "message": {"chat": {"id": 42}},
            },
        }]

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=api,
            choice_callback=choice_callback,
        )
        self.assertEqual(1, poller.poll_once())
        self.assertEqual([
            ("11111111-1111-4111-8111-111111111111", "correlate", "telegram:42"),
        ], calls)

    def test_choice_event_rejects_authority_goal_data(self) -> None:
        with self.assertRaises(ValidationError):
            self.center.create_event("producer", "choice-authority", self._event(
                choices=[
                    {"choice_id": "inspect", "label": "Inspect", "next_goal": "restart server"},
                    {"choice_id": "verify", "label": "Verify"},
                ],
            ))


if __name__ == "__main__":
    unittest.main()
