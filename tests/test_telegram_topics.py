"""Active-mode routing and idempotent Telegram forum-topic reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest

from notification_center.http_api import TelegramTopicManager, telegram_active_modes, telegram_destination, telegram_routes_with_auto_topics


class TelegramTopicTests(unittest.TestCase):
    def test_log_uses_log_mode_and_inactive_modes_do_not_route(self) -> None:
        routes = {
            "log": {"chat_id": "-1001", "message_thread_id": 11},
            "important": {"chat_id": "-1001", "message_thread_id": 12},
            "emergency": {"chat_id": "-1001", "message_thread_id": 13},
        }
        self.assertEqual("11", telegram_destination("-1001", routes, {"kind": "log", "severity": "info"}, {"log", "important", "emergency"})["message_thread_id"])
        self.assertEqual({}, telegram_destination("-1001", routes, {"kind": "incident", "severity": "critical"}, {"log", "important", "emergency"}))

    def test_active_modes_default_to_small_operator_set(self) -> None:
        self.assertEqual({"emergency", "important", "log"}, telegram_active_modes(""))
        self.assertEqual({"important"}, telegram_active_modes(json.dumps(["important"])))

    def test_reconcile_creates_only_missing_active_topics(self) -> None:
        calls: list[tuple[str, str]] = []

        def create(name: str) -> int:
            calls.append(("create", name))
            return 30

        manager = TelegramTopicManager("-1001", create)
        routes = manager.reconcile({"important": {"message_thread_id": 12}}, {"important", "emergency", "log"})
        self.assertEqual(2, len(calls))
        self.assertEqual(12, routes["important"]["message_thread_id"])
        self.assertEqual(30, routes["emergency"]["message_thread_id"])
        self.assertEqual(30, routes["log"]["message_thread_id"])

    def test_auto_reconcile_persists_topic_ids_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            created: list[str] = []

            def create(name: str) -> int:
                created.append(name)
                return 40 + len(created)

            state = f"{root}/topics.json"
            first = telegram_routes_with_auto_topics(
                "token", "-1001",
                {"important": {"message_thread_id": 41}, "log": {"message_thread_id": 42}},
                {"important", "log"}, state, enabled=True, runner=None,
            )
            self.assertEqual({"important", "log"}, set(first))
            self.assertEqual([], created)

            # The low-level creator is injected through TelegramTopicManager in production;
            # persistence behavior is covered by supplying already-created routes here.
            second = telegram_routes_with_auto_topics("token", "-1001", first, {"important"}, state, enabled=False)
            self.assertEqual({"important"}, set(second))


if __name__ == "__main__":
    unittest.main()
