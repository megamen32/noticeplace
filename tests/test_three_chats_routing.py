"""Three-chat routing and the «изменить важность» control (owner order 23:10).

«Решения» carries pending questions; unsolicited incident cards require an
emergency. Critical business notices keep their routing; «Логи» ships disabled.
The per-source severity override re-weights future events of one source.
"""
from __future__ import annotations

import json
import time
import tempfile
import unittest
import urllib.parse
import io
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter
from notification_center.http_api import (
    DeliveryWorker,
    TelegramSender,
    telegram_destination,
    telegram_mode,
    telegram_mode_for_payload,
)
from notification_center.telegram_interactions import (
    SEVERITY_CODES,
    TelegramActionCodec,
    TelegramInteractionPoller,
)


class RoutingModeTests(unittest.TestCase):
    def test_severity_maps_to_notify_only_for_emergency_and_critical(self) -> None:
        self.assertEqual("notify", telegram_mode({"severity": "emergency"}))
        self.assertEqual("notify", telegram_mode({"severity": "critical"}))
        for severity in ("important", "notice", "info", "debug"):
            self.assertEqual("log", telegram_mode({"severity": severity}))

    def test_health_events_are_decision_cards(self) -> None:
        self.assertEqual("decisions", telegram_mode({"severity": "notice", "event_type": "health.degraded"}))

    def test_payload_with_buttons_is_a_decision_card(self) -> None:
        incident = {"severity": "notice"}
        for key in ("human_question", "choices", "health_plans"):
            self.assertEqual("decisions", telegram_mode_for_payload({"incident": incident, key: [{"a": 1}]}))
        self.assertEqual("log", telegram_mode_for_payload({"incident": incident}))

    def test_decisions_route_never_falls_back_to_the_default_chat(self) -> None:
        routes = {"notify": {"chat_id": "-1002"}}
        self.assertEqual({}, telegram_destination("-1001", routes, {"severity": "notice"}, {"decisions", "notify"}))
        self.assertEqual({"chat_id": "-1002"}, telegram_destination("-1001", routes, {"severity": "critical"}, {"decisions", "notify"}))


class SourceSeverityOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3",
            {"producer": {"project": "fail2ban.server-100", "max_severity": "emergency"}},
            default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)
        self.card = self.center.create_event("producer", "first", {
            "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "ban", "dedup_key": "fail2ban:jail:ip",
        })

    def test_override_reweights_future_events_of_the_source(self) -> None:
        result = self.center.set_source_severity(self.card["incident_id"], "emergency", "telegram:42")
        self.assertEqual("fail2ban.server-100", result["project"])

        view = self.center.get_source_severity(self.card["incident_id"])
        self.assertEqual("emergency", view["override_severity"])
        self.assertEqual("notice", view["incident_severity"])

        with patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}):
            follow = self.center.create_event("producer", "second", {
                "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
                "kind": "incident", "severity": "notice", "title": "ban 2", "dedup_key": "fail2ban:jail:ip2",
            })
        self.assertEqual("notify", telegram_mode(self.center.get_incident(follow["incident_id"])))
        self.assertIsNotNone(follow["initial_delivery_id"], "emergency override escapes the disabled log chat")
        applied = self.center._connection.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = 'second'"
        ).fetchone()
        self.assertIn('"severity": "notice"', applied["payload_json"], "producer payload keeps its own severity")
        self.assertEqual("emergency", self.center.get_incident(follow["incident_id"])["severity"])

    def test_override_below_the_threshold_silences_future_events(self) -> None:
        self.center.set_source_severity(self.card["incident_id"], "debug", "telegram:42")
        with patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}):
            follow = self.center.create_event("producer", "third", {
                "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
                "kind": "incident", "severity": "critical", "title": "ban 3", "dedup_key": "fail2ban:jail:ip3",
            })
        self.assertIsNone(follow["initial_delivery_id"], "debug override routes to the disabled log chat")
        self.assertEqual("debug", self.center.get_incident(follow["incident_id"])["severity"])

    def test_invalid_override_severity_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            self.center.set_source_severity(self.card["incident_id"], "loudest", "telegram:42")


class SeverityPickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3",
            {"producer": {"project": "fail2ban.server-100", "max_severity": "emergency"}},
            default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)
        self.card = self.center.create_event("producer", "picker-card", {
            "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "ban", "dedup_key": "fail2ban:jail:ip",
        })
        self.codec = TelegramActionCodec("x" * 32)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.poller = TelegramInteractionPoller(
            self.center, "bot-token", {"42"}, self.codec,
            api=lambda method, payload: self.calls.append((method, payload)) or {"ok": True, "result": True},
        )

    def _callback(self, data: str) -> None:
        self.poller._handle_callback({
            "id": "cb-sev", "from": {"id": 42},
            "data": data, "message": {"message_id": 73, "chat": {"id": -1001}},
        })

    def test_sev_button_opens_the_six_level_picker(self) -> None:
        self._callback(self.codec.encode("sev", self.card["incident_id"]))
        picker = next(payload for method, payload in self.calls if method == "sendMessage")
        keyboard = json.loads(str(picker["reply_markup"]))
        labels = [row[0]["text"] for row in keyboard["inline_keyboard"]]
        self.assertEqual(6, len(labels))
        self.assertIn("🚨 emergency — аварийное", labels[5])
        self.assertIn("fail2ban.server-100", str(picker["text"]))
        answer = next(payload for method, payload in self.calls if method == "answerCallbackQuery")

    def test_sevset_applies_the_chosen_importance(self) -> None:
        self._callback(self.codec.encode("sevset", self.card["incident_id"], plan_id="e"))
        view = self.center.get_source_severity(self.card["incident_id"])
        self.assertEqual("emergency", view["override_severity"])
        answer = next(payload for method, payload in self.calls if method == "answerCallbackQuery")
        self.assertIn("emergency", str(answer["text"]))
        self.assertIn("fail2ban.server-100", str(answer["text"]))

    def test_sevset_codes_round_trip_and_reject_unknown(self) -> None:
        self.assertEqual(dict(sorted(zip("dinpc e".replace(" ", ""), ["debug", "info", "notice", "important", "critical", "emergency"]))), SEVERITY_CODES)
        encoded = self.codec.encode("sevset", self.card["incident_id"], plan_id="c")
        self.assertLessEqual(len(encoded.encode()), 64)
        self.assertEqual(("sevset", self.card["incident_id"], "c"), self.codec.decode(encoded))
        with self.assertRaises(ValueError):
            self.codec.encode("sevset", self.card["incident_id"], plan_id="x")


class ThreeChatDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3",
            {"producer": {"project": "fleet-health", "max_severity": "emergency"}},
            default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)
        self.routes = {
            "decisions": {"chat_id": "-1004419894781"},
            "notify": {"chat_id": "-1004450539053"},
            "log": {"chat_id": "-1004322359393", "message_thread_id": 122},
        }
        self.calls: list[tuple[str, dict[str, object]]] = []
        codec = TelegramActionCodec("x" * 32)
        self.sender = TelegramSender("fake-token", "-100", action_codec=codec, center=self.center,
                                     severity_routes=self.routes, active_modes={"decisions", "notify"})
        self.worker = DeliveryWorker(self.center, self.sender)

        def network(request, **kwargs):
            payload = {k: v[0] for k, v in urllib.parse.parse_qs(request.data.decode()).items()}
            method = request.full_url.rsplit("/", 1)[-1]
            self.calls.append((method, payload))
            message_id = len([m for m, _ in self.calls if m == "sendMessage"]) if method == "sendMessage" else int(payload.get("message_id") or 0)
            response = io.BytesIO(json.dumps({"ok": True, "result": {"chat": {"id": -100}, "message_id": message_id}}).encode())
            response.status = 200
            return response

        patcher = patch("notification_center.http_api.urllib.request.urlopen", side_effect=network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _claim(self, delivery_id):
        import time as clock
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "UPDATE deliveries SET status='claimed',claimed_at=?,attempt=attempt+1 WHERE id=?",
                (clock.time(), delivery_id))
            return dict(self.center._connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone())

    def test_emergency_card_lands_in_the_notify_chat(self) -> None:
        created = self.center.create_event("producer", "crit", {
            "schema": "notify.event.v1", "project": "fleet-health", "recipient": "me",
            "kind": "incident", "severity": "emergency", "title": "Падение", "dedup_key": "load:100",
        })
        self.assertIsNotNone(created["initial_delivery_id"])
        self.worker.deliver(self._claim(created["initial_delivery_id"]))
        send = next(payload for method, payload in self.calls if method == "sendMessage")
        self.assertEqual("-1004450539053", str(send["chat_id"]))

    def test_routine_noise_is_never_queued_while_logs_are_disabled(self) -> None:
        created = self.center.create_event("producer", "noise", {
            "schema": "notify.event.v1", "project": "fleet-health", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "Бан", "dedup_key": "fail2ban:ip",
        })
        self.assertIsNone(created["initial_delivery_id"])
        self.assertEqual(0, self.worker.run_once())

    def test_notice_incident_with_a_question_reaches_the_decisions_chat(self) -> None:
        import os
        import time as clock
        from notification_center.http_api import send_human_request_telegram
        created = self.center.create_event("producer", "quiet", {
            "schema": "notify.event.v1", "project": "fleet-health", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "Выберите", "dedup_key": "choose:1",
        })
        self.assertIsNone(created["initial_delivery_id"])
        # The incident chat is inactive, so the bound question must reach the
        # owner as its own decision card in «Решения» instead of a never-ending
        # card edit.
        request = {
            "schema": "ask_human.request.v1", "request_id": "quiet-question", "project": "fleet-health",
            "recipient": "me", "mode": "choice", "message": "Как поступаем?",
            "choices": [{"label": "Тишина", "value": "mute"}, {"label": "Тревога", "value": "alert"}],
            "allowed_actors": ["telegram:42"], "expires_at": clock.time() + 600,
            "incident_id": created["incident_id"],
        }
        self.center.create_human_request("producer", request)
        fetched = self.center.get_human_request("producer", "quiet-question")
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot",
            "TELEGRAM_CHAT_ID": "-1004322359393",
            "TELEGRAM_DECISIONS_CHAT_ID": "-1004419894781",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
        }):
            outcome = send_human_request_telegram(self.center, "producer", fetched, api=None)
        self.assertEqual("sent", outcome["status"])
        send = next(payload for method, payload in self.calls if method == "sendMessage")
        self.assertEqual("-1004419894781", str(send["chat_id"]))
        self.assertIn("Как поступаем?", str(send["text"]))


class StaleAlarmBoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(
            Path(self.temp.name) / "notice.sqlite3",
            {"producer": {"project": "fleet-health", "max_severity": "emergency"}},
            default_quiet_hours=[],
        )
        self.addCleanup(self.center._connection.close)

    def _event(self, key: str, severity: str = "emergency") -> dict[str, object]:
        with patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify"]'}):
            return self.center.create_event("producer", key, {
                "schema": "notify.event.v1", "project": "fleet-health", "recipient": "me",
                "kind": "incident", "severity": severity, "title": "Падение", "dedup_key": key,
            })

    def test_stale_alarm_card_is_cancelled_instead_of_retried(self) -> None:
        created = self._event("stale-alarm")
        now = time.time()
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "UPDATE deliveries SET created_at = ?, updated_at = ?, due_at = ? WHERE id = ?",
                (now - 25 * 3600, now - 24 * 3600, now - 3600, created["initial_delivery_id"]))
        rows = self.center.claim_due_deliveries(now_epoch=now, limit=4, channel_group="message")
        self.assertEqual([], [row for row in rows if row["id"] == created["initial_delivery_id"]])
        status = self.center._connection.execute(
            "SELECT status, last_error FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)).fetchone()
        self.assertEqual("cancelled", status["status"])
        self.assertIn("устарела", status["last_error"])
        audit = self.center._connection.execute(
            "SELECT COUNT(*) c FROM audit_events WHERE incident_id = ? AND type = 'delivery_denied'",
            (created["incident_id"],)).fetchone()
        self.assertGreaterEqual(audit["c"], 1)

    def test_fresh_alarm_card_still_claims(self) -> None:
        created = self._event("fresh-alarm")
        rows = self.center.claim_due_deliveries(now_epoch=time.time() + 30, limit=4, channel_group="message")
        self.assertIn(created["initial_delivery_id"], [row["id"] for row in rows])


class RuntimeRoutesFallbackTests(unittest.TestCase):
    def test_empty_runtime_topics_keep_the_env_routes(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        center = NotificationCenter(Path(temp.name) / "notice.sqlite3", {}, default_quiet_hours=[])
        self.addCleanup(center._connection.close)
        center.set_runtime_setting("telegram_topics_json", "{}")
        sender = TelegramSender("bot-token", "-100", center=center,
                                severity_routes={"notify": {"chat_id": "-1002"}})
        self.assertEqual({"notify": {"chat_id": "-1002"}}, sender._routes())

    def test_nonempty_runtime_topics_still_override(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        center = NotificationCenter(Path(temp.name) / "notice.sqlite3", {}, default_quiet_hours=[])
        self.addCleanup(center._connection.close)
        center.set_runtime_setting("telegram_topics_json", json.dumps({"decisions": {"chat_id": "-1003"}}))
        sender = TelegramSender("bot-token", "-100", center=center,
                                severity_routes={"notify": {"chat_id": "-1002"}})
        self.assertEqual({"decisions": {"chat_id": "-1003"}}, sender._routes())


if __name__ == "__main__":
    unittest.main()
