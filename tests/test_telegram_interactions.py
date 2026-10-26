from __future__ import annotations

import tempfile
import unittest
import unittest.mock
import json
from pathlib import Path

from notification_center.core import NotificationCenter
from notification_center.health_workflow import HealthWorkflow
from notification_center.telegram_interactions import TelegramActionCodec, TelegramConsoleOwnerControl, TelegramInteractionPoller
from notification_center.http_api import telegram_inbox_sink_from_environment, telegram_inline_keyboard


class _FakeHerderOwnerControl:
    def __init__(self, *, forbidden: bool = False, sessions: list[dict[str, object]] | None = None,
                 partial: bool = False, fail_stop: bool = False) -> None:
        self.forbidden = forbidden
        self.sessions = list(sessions or [])
        self.partial = partial
        self.fail_stop = fail_stop
        self.calls: list[tuple[str, str]] = []
        self.revision = 1

    def read_policy(self):
        self.calls.append(("read_policy", ""))
        return {
            "ok": True,
            "etag": f'"policy-{self.revision}"',
            "state": {"disarmed": self.forbidden,
                      "records": [{"scope": "*", "forbidden": self.forbidden}]},
            "nodes": [{"hostId": host, "status": "confirmed"}
                      for host in ("roomhacker-server-100", "server-44", "server-88")],
        }

    def read_active_sessions(self):
        self.calls.append(("read_active_sessions", ""))
        return {"sessions": list(self.sessions), "partial": self.partial,
                "hosts": [{"hostId": host, "state": "available"}
                          for host in ("server-100", "server-44", "server-88")]}

    def stop_current(self):
        self.calls.append(("stop_current", ""))
        if self.fail_stop:
            raise TimeoutError("unknown stop result")
        self.forbidden = True
        self.revision += 1
        return {"ok": True, "applied": True, "stopped": [], "stillRunning": []}

    def allow_new_operations(self, etag: str):
        self.calls.append(("allow_new_operations", etag))
        if etag != f'"policy-{self.revision}"':
            return {"ok": False, "applied": False}
        self.forbidden = False
        self.revision += 1
        return {"ok": True, "applied": True}


class _FakeConsoleResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self._body


class TelegramInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "critical"}})
        self.created = self.center.create_event("producer", "create", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident", "severity": "critical", "title": "Outage", "body": "details", "dedup_key": "telegram-controls",
        })

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_signed_callback_acknowledges_once_and_rejects_a_tampered_action(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        calls: list[tuple[str, dict[str, object]]] = []
        callback = {"id": "cb_1", "from": {"id": 42}, "data": codec.encode("ack", self.created["incident_id"])}
        updates = [{"update_id": 10, "callback_query": callback}, {"update_id": 11, "callback_query": {**callback, "id": "cb_2", "data": codec.encode("ack", self.created["incident_id"]).replace("ack", "ask", 1)}}]

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api)
        self.assertEqual(2, poller.poll_once())
        self.assertEqual("acknowledged", self.center.get_incident(self.created["incident_id"])["state"])
        self.assertEqual(2, len([method for method, _payload in calls if method == "answerCallbackQuery"]))
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([0, 12], [payload["offset"] for method, payload in calls if method == "getUpdates"])

    def test_ask_command_is_audited_but_not_executed(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        question = "Should I restart the worker?"
        update = {"update_id": 20, "message": {"from": {"id": 42}, "chat": {"id": 42}, "text": f"/ask {self.created['incident_id']} {question}"}}

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api).poll_once()
        events = self.center._connection.execute("SELECT type, payload_json FROM audit_events WHERE incident_id = ? ORDER BY created_at", (self.created["incident_id"],)).fetchall()
        self.assertIn("telegram_ask_recorded", [row["type"] for row in events])
        self.assertEqual("open", self.center.get_incident(self.created["incident_id"])["state"])

    def test_ai_button_schedules_one_manual_diagnosis_job(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        keyboard = telegram_inline_keyboard(codec, self.center.get_incident(self.created["incident_id"]))
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        ai_button = next(button for button in buttons if button["text"] == "Исправить через ИИ")
        updates = [{
            "update_id": 21,
            "callback_query": {"id": "cb_ai", "from": {"id": 42}, "data": ai_button["callback_data"]},
        }]

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api).poll_once()
        repeated = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        rows = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis'",
            (self.created["incident_id"],),
        ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertTrue(repeated["idempotent"])

    def test_ai_button_restarts_a_failed_diagnosis_delivery(self) -> None:
        first = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        self.center.complete_delivery(first["agent_job_delivery_id"], "failed", "temporary downstream failure")

        restarted = self.center.apply_telegram_action(self.created["incident_id"], "ai", "telegram:42")
        rows = self.center._connection.execute(
            "SELECT id, status, last_error FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis' ORDER BY created_at, id",
            (self.created["incident_id"],),
        ).fetchall()

        self.assertFalse(restarted["idempotent"])
        self.assertTrue(restarted["restarted"])
        self.assertNotEqual(first["agent_job_delivery_id"], restarted["agent_job_delivery_id"])
        self.assertEqual(["failed", "queued"], [row["status"] for row in rows])
        self.assertIsNone(rows[1]["last_error"])

    def test_manual_ai_marks_the_card_before_queueing_and_keeps_its_identity(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        calls = []
        callback = {"id": "ai-card", "from": {"id": 42},
                    "data": codec.encode("ai", self.created["incident_id"]),
                    "message": {"message_id": 12, "chat": {"id": -100123},
                                "text": "Исходное уведомление", "entities": []}}
        def api(method, payload):
            calls.append((method, payload))
            return {"ok": True, "result": True}
        poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api)
        poller._handle_callback(callback)
        edits = [p for m, p in calls if m == "editMessageText"]
        self.assertEqual(1, len(edits))
        self.assertIn("✅ Выбрано: решение через AI", edits[0]["text"])
        self.assertEqual([], json.loads(edits[0]["reply_markup"])["inline_keyboard"])
        job = self.center._connection.execute("SELECT target_json FROM deliveries WHERE channel='gptadmin.agent:health-diagnosis'").fetchone()
        self.assertEqual(12, json.loads(job[0])["ai_card"]["message_id"])
        poller._handle_callback(callback)
        self.assertEqual(1, len([p for m, p in calls if m == "editMessageText"]))
        recorded = self.center.record_health_agent_session(self.created["incident_id"], "manual-session",
            "", "codex", "session-id", "https://agent.bezrabotnyi.com", stage="health-diagnosis")
        row = self.center._connection.execute("SELECT * FROM deliveries WHERE id=?", (recorded["session_delivery_id"],)).fetchone()
        payload = self.center.delivery_payload(dict(row))
        self.assertEqual(12, payload["target"]["ai_card"]["message_id"])
        self.assertIn("codex%3Asession-id", payload["health_session"]["session_url"])

    def test_ai_marker_survives_a_full_length_message_with_emoji(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        edits = []
        def api(method, payload):
            if method == "editMessageText":
                edits.append(payload)
            return {"ok": True, "result": True}
        callback = {"id": "long-ai", "from": {"id": 42}, "data": codec.encode("ai", self.created["incident_id"]),
                    "message": {"message_id": 12, "chat": {"id": -100123}, "text": "🙂" * 2048}}
        TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api)._handle_callback(callback)
        self.assertIn("✅ Выбрано: решение через AI", edits[0]["text"])
        self.assertLessEqual(len(edits[0]["text"].encode("utf-16-le")) // 2, 4096)

    def test_native_reply_is_attached_to_the_replied_incident_card(self) -> None:
        self.center.complete_delivery(
            self.created["initial_delivery_id"],
            "sent",
            result={"message_id": 73, "chat_id": "-1001"},
        )
        calls: list[tuple[str, dict[str, object]]] = []
        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            TelegramActionCodec("x" * 32),
            api=lambda method, payload: calls.append((method, payload)) or {"ok": True, "result": True},
        )

        poller._handle_message({
            "message_id": 74,
            "from": {"id": 42, "is_bot": False},
            "chat": {"id": -1001},
            "text": "Проверь логи воркера и почини.",
            "reply_to_message": {"message_id": 73},
        })

        row = self.center._connection.execute(
            "SELECT type, payload_json FROM audit_events WHERE incident_id = ? AND type = 'telegram_reply_recorded'",
            (self.created["incident_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("Проверь логи воркера и почини.", json.loads(row["payload_json"])["text"])
        self.assertFalse(any(method == "sendMessage" for method, _ in calls), "incident replies stay on the original card")

    def _health_incident_with_selected_plan(self, dedup_key: str, key_prefix: str) -> dict[str, object]:
        health = self.center.create_event("producer", f"create-{key_prefix}", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Health outage", "body": "degraded", "dedup_key": dedup_key,
            "event_type": "health.degraded", "source_id": "source-a", "host_id": "host-a", "signal_type": "relay",
        })
        self.center.complete_delivery(
            health["initial_delivery_id"],
            "sent",
            result={"message_id": 73, "chat_id": "-1001"},
        )
        workflow = HealthWorkflow(self.center)
        workflow.attach_plans(
            health["incident_id"],
            f"{key_prefix}:plans",
            [
                {"plan_id": "observe", "title": "Observe", "summary": "s", "step": "observe"},
                {"plan_id": "repair", "title": "Repair", "summary": "s", "step": "repair"},
                {"plan_id": "verify", "title": "Verify", "summary": "s", "step": "verify"},
            ],
            orchestration={
                "diagnosis_session_id": "ses-diag-1",
                "diagnosis_model": "minimax-coding-plan/MiniMax-M3.1-Flash",
                "orchestrator_session_id": "ses-orch-1",
                "orchestrator_requested_model": "minimax-coding-plan/MiniMax-M3.1-Flash",
                "orchestrator_effective_model": "minimax-coding-plan/MiniMax-M3.1-Flash",
                "harness": "opencode",
                "diagnosis_elapsed_ms": 1,
                "orchestrator_elapsed_ms": 1,
                "total_elapsed_ms": 2,
            },
        )
        self.center.record_health_plan_selection(health["incident_id"], "repair", "telegram:42", f"{key_prefix}:select")
        return health

    def test_health_reply_is_forwarded_into_the_live_agent_session(self) -> None:
        health = self._health_incident_with_selected_plan("health-relay-1", "health-relay")
        self.center.record_health_agent_session(
            health["incident_id"],
            f"{health['incident_id']}:health-remediation_session:opencode:ses-live-1:v3",
            "repair",
            "opencode",
            "ses-live-1",
            "https://agent.bezrabotnyi.com",
            stage="health-remediation",
        )
        calls: list[tuple[str, dict[str, object]]] = []
        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            TelegramActionCodec("x" * 32),
            api=lambda method, payload: calls.append((method, payload)) or {"ok": True, "result": True},
        )
        relay_requests = []

        def fake_relay(harness: str, session_id: str, incident_id: str, text: str, source_ref: str, runner=None) -> bool:
            relay_requests.append((harness, session_id, incident_id, text, source_ref))
            return True

        with unittest.mock.patch("notification_center.agent_job_helper.relay_operator_reply", side_effect=fake_relay):
            poller._handle_message({
                "message_id": 74,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "Перезапусти воркер и проверь.",
                "reply_to_message": {"message_id": 73},
            })

        self.assertEqual([("opencode", "ses-live-1", health["incident_id"], "Перезапусти воркер и проверь.", "-1001:74")], relay_requests)
        self.assertFalse(any(method == "sendMessage" for method, _ in calls), "native relay must not send another stage card")

    def test_health_reply_falls_back_when_the_session_is_unreachable(self) -> None:
        health = self._health_incident_with_selected_plan("health-relay-2", "health-relay-fallback")
        self.center.record_health_agent_session(
            health["incident_id"],
            f"{health['incident_id']}:health-remediation_session:opencode:ses-dead-1:v3",
            "repair",
            "opencode",
            "ses-dead-1",
            "https://agent.bezrabotnyi.com",
            stage="health-remediation",
        )
        calls: list[tuple[str, dict[str, object]]] = []
        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            TelegramActionCodec("x" * 32),
            api=lambda method, payload: calls.append((method, payload)) or {"ok": True, "result": True},
        )

        with unittest.mock.patch("notification_center.agent_job_helper.relay_operator_reply", return_value=False):
            poller._handle_message({
                "message_id": 75,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "Проверь ещё раз.",
                "reply_to_message": {"message_id": 73},
            })

        self.assertFalse(any(method == "sendMessage" for method, _ in calls), "incident replies stay on the original card")

    def test_mute_button_suppresses_same_scope_and_old_card_restores_it(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        calls: list[tuple[str, dict[str, object]]] = []
        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=lambda method, payload: calls.append((method, payload)) or {"ok": True, "result": True},
        )
        card = {"message_id": 73, "chat": {"id": -1001}}

        poller._handle_callback({
            "id": "mute-1", "from": {"id": 42},
            "data": codec.encode("mute", self.created["incident_id"]), "message": card,
        })
        self.assertTrue(self.center.get_incident(self.created["incident_id"])["notifications_muted"])
        muted = self.center.create_event("producer", "muted-repeat", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Still out", "body": "details", "dedup_key": "telegram-controls",
        })
        self.assertTrue(muted["notifications_muted"])
        self.assertIsNone(muted["initial_delivery_id"])
        edit = next(payload for method, payload in calls if method == "editMessageReplyMarkup")
        self.assertIn("Включить такие уведомления", str(edit["reply_markup"]))

        poller._handle_callback({
            "id": "unmute-1", "from": {"id": 42},
            "data": codec.encode("unmute", self.created["incident_id"]), "message": card,
        })
        self.assertFalse(self.center.get_incident(self.created["incident_id"])["notifications_muted"])
        restored_now = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE incident_id = ? AND delivery_key LIKE ?",
            (self.created["incident_id"], "%:notifications-restored:%"),
        ).fetchone()
        self.assertIsNotNone(restored_now)
        self.assertEqual("queued", restored_now["status"])
        self.center.resolve(self.created["incident_id"], "test")
        restored = self.center.create_event("producer", "restored-event", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Out again", "body": "details", "dedup_key": "telegram-controls",
        })
        self.assertFalse(restored["notifications_muted"])
        self.assertIsNotNone(restored["initial_delivery_id"])

    def test_allowed_regular_message_is_forwarded_to_universal_inbox_once(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 30,
            "message": {
                "message_id": 50,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "route me",
            },
        }
        forwarded = []

        def api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": [update] if method == "getUpdates" else True}

        def inbox_sink(payload: dict[str, object]) -> dict[str, object]:
            forwarded.append(payload)
            return {"event_id": "evt_1", "delivery_id": "dlv_1"}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=api,
            inbox_sink=inbox_sink,
            inbox_chat_ids={"-1001"},
        )

        self.assertEqual(1, poller.poll_once())
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([{
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "-1001:50",
            "sender": "42",
            "body": "route me",
        }], forwarded)

    def test_inbox_sender_allowlist_is_independent_from_callback_allowlist(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 33,
            "message": {
                "message_id": 53,
                "from": {"id": 99, "is_bot": False},
                "chat": {"id": -1001},
                "text": "relay me",
            },
        }
        forwarded = []

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=lambda method, _payload: {"ok": True, "result": [update] if method == "getUpdates" else True},
            inbox_sink=lambda payload: forwarded.append(payload),
            inbox_chat_ids={"-1001"},
            inbox_sender_ids={"99"},
        )

        self.assertEqual(1, poller.poll_once())
        self.assertEqual("99", forwarded[0]["sender"])

    def test_bot_or_unallowlisted_message_is_not_forwarded_to_universal_inbox(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        updates = [
            {"update_id": 31, "message": {"message_id": 51, "from": {"id": 42, "is_bot": True}, "chat": {"id": -1001}, "text": "bot"}},
            {"update_id": 32, "message": {"message_id": 52, "from": {"id": 42}, "chat": {"id": -1002}, "text": "other"}},
        ]
        forwarded = []

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=lambda method, _payload: {"ok": True, "result": updates if method == "getUpdates" else True},
            inbox_sink=lambda payload: forwarded.append(payload),
            inbox_chat_ids={"-1001"},
        )

        self.assertEqual(2, poller.poll_once())
        self.assertEqual([], forwarded)

    def test_configured_inbox_sink_uses_fixed_operator_url_and_bearer(self) -> None:
        requests = []

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read() -> bytes:
                return b'{"event_id":"evt_1","delivery_id":"dlv_1"}'

        def runner(request, *, timeout):
            requests.append((request, timeout))
            return Response()

        sink = telegram_inbox_sink_from_environment({
            "UNIVERSAL_INBOX_INGRESS_URL": "http://127.0.0.1:8092/v1/inbox/messages",
            "UNIVERSAL_INBOX_INGRESS_TOKEN": "inbox-token",
        }, runner=runner)

        self.assertIsNotNone(sink)
        result = sink({
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "-1001:50",
            "sender": "42",
            "body": "route me",
        })

        self.assertEqual("evt_1", result["event_id"])
        self.assertEqual("Bearer inbox-token", requests[0][0].get_header("Authorization"))
        self.assertEqual("http://127.0.0.1:8092/v1/inbox/messages", requests[0][0].full_url)
        self.assertEqual("route me", json.loads(requests[0][0].data)["body"])

    def test_failed_inbox_sink_retries_same_update_before_advancing_offset(self) -> None:
        codec = TelegramActionCodec("x" * 32)
        update = {
            "update_id": 40,
            "message": {
                "message_id": 60,
                "from": {"id": 42, "is_bot": False},
                "chat": {"id": -1001},
                "text": "retry me",
            },
        }
        offsets = []
        deliveries = []

        def api(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method != "getUpdates":
                return {"ok": True, "result": True}
            offsets.append(payload["offset"])
            return {"ok": True, "result": [] if int(payload["offset"]) > 40 else [update]}

        def inbox_sink(payload: dict[str, object]) -> dict[str, object]:
            if not deliveries:
                deliveries.append("failed")
                raise RuntimeError("Inbox temporarily unavailable")
            deliveries.append(payload)
            return {"event_id": "evt_1", "delivery_id": "dlv_1"}

        poller = TelegramInteractionPoller(
            self.center,
            "bot-token",
            {"42"},
            codec,
            api=api,
            inbox_sink=inbox_sink,
            inbox_chat_ids={"-1001"},
        )

        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            poller.poll_once()
        self.assertEqual(1, poller.poll_once())
        self.assertEqual(0, poller.poll_once())
        self.assertEqual([0, 0, 41], offsets)
        self.assertEqual(2, len(deliveries))
        self.assertEqual("retry me", deliveries[1]["body"])

    def _owner_poller(self, updates, owner_control, emergency_pause_reader=None):
        calls = []

        def api(method, payload):
            calls.append((method, payload))
            return {"ok": True, "result": updates if method == "getUpdates" else True}

        poller = TelegramInteractionPoller(
            self.center, "bot-token", {"42"}, TelegramActionCodec("x" * 32), api=api,
            inbox_chat_ids={"-1001"}, owner_user_ids={"42"}, owner_control=owner_control,
            emergency_pause_reader=emergency_pause_reader,
        )
        return poller, calls

    @staticmethod
    def _owner_command(update_id, message_id, sender_id, chat_id, text):
        return {"update_id": update_id, "message": {
            "message_id": message_id, "from": {"id": sender_id, "is_bot": False},
            "chat": {"id": chat_id}, "text": text,
        }}

    def test_owner_commands_require_both_allowlisted_sender_and_trusted_chat(self):
        control = _FakeHerderOwnerControl()
        updates = [
            self._owner_command(101, 201, 99, -1001, "/stop"),
            self._owner_command(102, 202, 42, -1002, "/stop"),
        ]
        poller, calls = self._owner_poller(updates, control)

        self.assertEqual(2, poller.poll_once())
        self.assertFalse(any(name == "stop_current" for name, _ in control.calls))
        self.assertFalse(any(method == "sendMessage" for method, _ in calls))
        audit = self.center._connection.execute(
            "SELECT actor, payload_json FROM audit_events WHERE type='telegram.owner_command' ORDER BY created_at"
        ).fetchall()
        self.assertEqual(["telegram:99", "telegram:42"], [row["actor"] for row in audit])
        self.assertEqual(["denied", "denied"], [json.loads(row["payload_json"])["outcome"] for row in audit])

    def test_owner_stop_uses_current_herder_stop_endpoint_and_reads_back_all_nodes(self):
        control = _FakeHerderOwnerControl(
            sessions=[{"id": "manual-session-1", "harness": "codex", "runtimeHostId": "server-44"}],
            partial=True,
        )
        update = self._owner_command(103, 203, 42, -1001, "/stop")
        poller, calls = self._owner_poller([update], control)

        self.assertEqual(1, poller.poll_once())
        self.assertEqual(1, [name for name, _ in control.calls].count("stop_current"))
        self.assertTrue(control.forbidden)
        self.assertEqual("manual-session-1", control.sessions[0]["id"])
        reply = next(payload["text"] for method, payload in calls if method == "sendMessage")
        self.assertIn("запрещены", reply)
        self.assertIn("server-44 — подтвердил", reply)
        self.assertIn("Текущие сессии: неполные данные: найдено 1 сессий", reply)
        self.assertIn("Общие нативные сессии показаны отдельно", reply)
        self.assertIn("Аварийная сетевая пауза Notice Place: неизвестно", reply)
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE type='telegram.owner_command' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        self.assertEqual("confirmed", json.loads(audit["payload_json"])["outcome"])

    def test_repeated_stop_is_idempotent_and_does_not_stop_twice(self):
        control = _FakeHerderOwnerControl()
        updates = [self._owner_command(104, 204, 42, -1001, "/stop")]
        poller, _calls = self._owner_poller(updates, control)
        self.assertEqual(1, poller.poll_once())
        # A distinct Telegram message after confirmed stop is a fresh command,
        # but the settled state lets the poller answer without another mutation.
        control.sessions = [{"id": "manual-session-2", "harness": "codex", "runtimeHostId": "server-88"}]
        control.partial = True
        updates.append(self._owner_command(105, 205, 42, -1001, "/stop"))
        self.assertEqual(1, poller.poll_once())
        self.assertEqual(1, [name for name, _ in control.calls].count("stop_current"))
        self.assertEqual(0, poller.poll_once())

    def test_start_only_allows_new_operations_and_does_not_resume_sessions(self):
        control = _FakeHerderOwnerControl(forbidden=True)
        updates = [self._owner_command(106, 206, 42, -1001, "/start")]
        poller, calls = self._owner_poller(updates, control)

        self.assertEqual(1, poller.poll_once())
        self.assertFalse(control.forbidden)
        self.assertEqual([("allow_new_operations", '"policy-1"')],
                         [call for call in control.calls if call[0] == "allow_new_operations"])
        self.assertFalse(any(name in {"stop_current", "resume", "start_session"} for name, _ in control.calls))
        self.assertIn("сессии не возобновлялись", next(p["text"] for m, p in calls if m == "sendMessage"))
        updates.append(self._owner_command(110, 210, 42, -1001, "/start"))
        self.assertEqual(1, poller.poll_once())
        self.assertEqual(1, len([call for call in control.calls if call[0] == "allow_new_operations"]))

    def test_status_reports_unknown_data_and_emergency_pause_without_false_pass(self):
        control = _FakeHerderOwnerControl(partial=True)
        control.read_active_sessions = lambda: {
            "confirmed": False, "count": 10, "partial": True, "hosts": ["server-44"],
        }
        update = self._owner_command(107, 207, 42, -1001, "/status")
        poller, calls = self._owner_poller([update], control)
        self.assertEqual(1, poller.poll_once())
        reply = next(payload["text"] for method, payload in calls if method == "sendMessage")
        self.assertIn("Политика Herder для новых запусков: разрешены", reply)
        self.assertIn("Фактическая доступность автоматических запусков: неизвестно", reply)
        self.assertIn("Текущие сессии: неполные данные, видно не менее 10 сессий", reply)
        self.assertIn("Узлы с сессиями: server-44", reply)
        self.assertIn("Аварийная сетевая пауза Notice Place: неизвестно", reply)

    def test_status_does_not_call_ai_effectively_enabled_while_emergency_pause_is_active(self):
        control = _FakeHerderOwnerControl()
        update = self._owner_command(109, 209, 42, -1001, "/status")
        poller, calls = self._owner_poller(
            [update], control, emergency_pause_reader=lambda: {"confirmed": True, "active": True},
        )
        self.assertEqual(1, poller.poll_once())
        reply = next(payload["text"] for method, payload in calls if method == "sendMessage")
        self.assertIn("Политика Herder для новых запусков: разрешены", reply)
        self.assertIn("Фактическая доступность автоматических запусков: заблокированы аварийной сетевой паузой", reply)

    def test_stop_unknown_result_is_audited_and_not_replayed(self):
        control = _FakeHerderOwnerControl(fail_stop=True)
        update = self._owner_command(108, 208, 42, -1001, "/stop")
        poller, calls = self._owner_poller([update], control)
        self.assertEqual(1, poller.poll_once())
        self.assertEqual(0, poller.poll_once())
        self.assertEqual(1, [name for name, _ in control.calls].count("stop_current"))
        reply = next(payload["text"] for method, payload in calls if method == "sendMessage")
        self.assertIn("состояние неизвестным", reply)
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE type='telegram.owner_command' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        self.assertEqual("unknown", json.loads(audit["payload_json"])["outcome"])

    def test_console_api_snapshot_adapts_owner_sessions_and_emergency_pause(self):
        requests = []
        payload = {"ai": {
            "owner": {"confirmed": True, "forbidden": True, "etag": '"rev-1"', "nodes": [
                {"hostId": "server-100", "status": "confirmed"},
                {"hostId": "server-44", "status": "confirmed"},
                {"hostId": "server-88", "status": "confirmed"},
            ]},
            "current_sessions": {"confirmed": True, "count": 2, "hosts": ["server-44"]},
            "emergency_network_pause": {"state": "active", "unit_active": True, "firewall_rule": True},
        }}

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return _FakeConsoleResponse(payload)

        control = TelegramConsoleOwnerControl(opener=opener)
        policy, sessions, pause = control.read_snapshot()
        self.assertEqual("http://127.0.0.1:8094/api/ai", requests[0][0].full_url)
        self.assertEqual("1", requests[0][0].get_header("X-notify-admin"))
        self.assertTrue(policy["ok"])
        self.assertTrue(policy["state"]["records"][0]["forbidden"])
        self.assertEqual(2, sessions["count"])
        self.assertFalse(sessions["partial"])
        self.assertTrue(pause["confirmed"])
        self.assertTrue(pause["active"])
        partial_sessions = control._adapt_active_sessions({"ai": {"current_sessions": {
            "confirmed": False, "count": 10, "partial": True, "hosts": ["server-44"],
        }}})
        self.assertFalse(partial_sessions["confirmed"])
        self.assertEqual(10, partial_sessions["count"])
        self.assertTrue(partial_sessions["partial"])

    def test_console_api_mutations_fetch_csrf_and_use_existing_arm_routes(self):
        requests = []
        responses = [
            _FakeConsoleResponse({"token": "csrf-one"}),
            _FakeConsoleResponse({"ok": True, "state": {}, "stopped": [], "still_running": []}),
            _FakeConsoleResponse({"token": "csrf-two"}),
            _FakeConsoleResponse({"ok": True, "state": {}}),
        ]

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return responses.pop(0)

        control = TelegramConsoleOwnerControl(opener=opener)
        stopped = control.stop_current()
        started = control.allow_new_operations('"rev-1"')
        self.assertTrue(stopped["ok"])
        self.assertEqual([], stopped["stillRunning"])
        self.assertTrue(started["ok"])
        self.assertEqual([
            ("GET", "http://127.0.0.1:8094/api/csrf"),
            ("POST", "http://127.0.0.1:8094/api/ai/disarm"),
            ("GET", "http://127.0.0.1:8094/api/csrf"),
            ("POST", "http://127.0.0.1:8094/api/ai/arm"),
        ], [(request.get_method(), request.full_url) for request, _timeout in requests])
        for request, _timeout in requests:
            self.assertEqual("1", request.get_header("X-notify-admin"))
        self.assertEqual("csrf-one", requests[1][0].get_header("X-notify-csrf"))
        self.assertEqual("csrf-two", requests[3][0].get_header("X-notify-csrf"))
        self.assertEqual(10, requests[1][1])
