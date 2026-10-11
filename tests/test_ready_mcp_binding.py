"""Real binding proof: the ready MCP tools create, deliver, answer, and resume.

Only the two outbound networks are replaced: the Telegram Bot API and the
Agent Herder loopback. The Notice Place HTTP API, the durable store, and the MCP
tool handlers are the real ones, so a passing run means the installed binding
really issues a card and really continues the bound native session.
"""

from __future__ import annotations

import json
import os
import runpy
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from notification_center import http_api
from notification_center.core import NotificationCenter


class _HerderHandler(BaseHTTPRequestHandler):
    completions: list[dict] = []
    created: list[dict] = []
    responses: list[tuple[int, dict]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/internal/human-requests":
            type(self).created.append(body)
            request_id = "3f5b0c2a-1d44-4c6e-9a71-58b1c0f7d902"
            payload = json.dumps({"request_id": request_id, "kind": body.get("kind", "user"), "binding": {
                "request_id": request_id,
                "native_target": {"harness": body.get("harness", ""), "session_id": body.get("session_id", ""),
                                  "cwd": body.get("cwd", "")},
                "dedup_key": f"human-request:{request_id}", "kind": body.get("kind", "user"),
                "question": {"message": body.get("message", ""), "choices": body.get("choices", [])},
                "answer": {"mcp_tool": "agent-herder_human_request_resolve",
                           "completion_path": "/internal/human-requests/ask-user-completion"},
            }}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        type(self).completions.append({"path": self.path, "body": body})
        code, extra = type(self).responses.pop(0) if type(self).responses else (202, {})
        if code == 0:
            # Herder received the completion but its receipt was lost.
            self.close_connection = True
            return
        payload = json.dumps({
            "request_id": body.get("request_id"), "status": "resuming", "continuation": "resume",
            **extra,
        }).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return None


class ReadyBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.center = NotificationCenter(
            Path(self.directory.name) / "notice.sqlite3",
            {"p": {"project": "hermes", "max_severity": "critical"}}, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)

        _HerderHandler.completions = []
        _HerderHandler.created = []
        _HerderHandler.responses = []
        self.herder = ThreadingHTTPServer(("127.0.0.1", 0), _HerderHandler)
        threading.Thread(target=self.herder.serve_forever, daemon=True).start()
        self.addCleanup(self.herder.shutdown)

        self.notice = ThreadingHTTPServer(
            ("127.0.0.1", 0), http_api.build_handler(self.center, "health-token", "mcp-token"))
        threading.Thread(target=self.notice.serve_forever, daemon=True).start()
        self.addCleanup(self.notice.shutdown)
        base = f"http://127.0.0.1:{self.notice.server_address[1]}"
        herder_base = f"http://127.0.0.1:{self.herder.server_address[1]}"

        self.telegram: list[tuple[str, dict]] = []
        self.card_failure = False

        def fake_telegram(token: str, method: str, payload: dict) -> dict:
            self.telegram.append((method, dict(payload)))
            if self.card_failure and method.startswith("editMessage"):
                raise RuntimeError("card edit unavailable")
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 42, "chat": {"id": int(payload["chat_id"])}}}
            return {"ok": True, "result": {"message_id": 42, "chat": {"id": -100}}}

        environment = {
            "NOTIFY_CENTER_EVENT_URL": f"{base}/v1/events",
            "NOTIFY_CENTER_TOKEN": "p",
            "NOTIFY_CENTER_PROJECT": "hermes",
            "NOTIFY_CENTER_RECIPIENT": "me",
            "NOTIFY_SECRETS_FILE": str(Path(self.directory.name) / "absent.env"),
            "ASK_HUMAN_ALLOWED_ACTORS": "telegram:42",
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "TELEGRAM_CHAT_ID": "-100",
            "TELEGRAM_CALLBACK_SECRET": "s" * 32,
            "NOTIFY_HEALTH_REMEDIATION_URL": f"{herder_base}/api/sessions/new-or-resume",
            "NOTIFY_HEALTH_REMEDIATION_CWD": self.directory.name,
            "NOTIFY_MCP_HTTP_URL": "",
            "NOTIFY_MCP_HTTP_TOKEN": "",
        }
        patcher = patch.dict(os.environ, environment, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        telegram_patcher = patch.object(http_api, "telegram_api", side_effect=fake_telegram)
        telegram_patcher.start()
        self.addCleanup(telegram_patcher.stop)

        from mcp import notify_mcp
        self.tools = notify_mcp.TOOLS
        self.request_id = "3f5b0c2a-1d44-4c6e-9a71-58b1c0f7d902"

    def _create_bound(self, mode: str = "choice") -> None:
        self._call("noticeplace_ask_human", {
            "request_id": self.request_id, "harness": "opencode", "session_id": "ses_executor_1",
            "cwd": self.directory.name, "message": "Починить сейчас?",
            "choices": ([{"value": "repair", "label": "Починить"},
                         {"value": "observe", "label": "Наблюдать"}] if mode == "choice" else []),
        })

    def _delivery_tick(self) -> None:
        """Run the production bounded pool, not a manual completion helper."""
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin" / "notify-center"))
        worker = http_api.DeliveryWorker(self.center, object())
        with patch("time.sleep", side_effect=InterruptedError("one bounded tick")):
            with self.assertRaises(InterruptedError):
                entry["worker_loop"](worker, "agent", 2)

    def _make_due(self) -> None:
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "UPDATE human_request_completions SET next_attempt_at=0, card_next_attempt_at=0")

    def test_http_card_failure_does_not_lose_native_completion_and_card_retries(self) -> None:
        self._create_bound()
        self.card_failure = True
        answered = self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        self.assertEqual("failed", answered["card"])
        self.assertEqual("resuming", answered["native"]["status"])
        self.assertEqual(1, len(_HerderHandler.completions))
        canonical = self._call("noticeplace_question_result", {"request_id": self.request_id})
        self.assertEqual(canonical["result_ref"], _HerderHandler.completions[0]["body"]["result_ref"])
        self.card_failure = False
        self._make_due()
        self._delivery_tick()
        self.assertEqual("sent", self.center.human_request_completion(self.request_id)["card_state"])
        self.assertEqual(1, len(_HerderHandler.completions), "card retry never re-resumes the native session")
        self.assertTrue(any("✅ Выбрано: Починить" in p.get("text", "") for m, p in self.telegram if m == "editMessageText"))

    def test_definite_native_unavailable_retries_canonical_reference_after_restart(self) -> None:
        self._create_bound()
        _HerderHandler.responses = [(502, {"status": "resume_failed", "reason": "agent-resume:native-unavailable", "retry": True})]
        answered = self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        self.assertEqual("retryable", answered["native"]["status"])
        # Reopen the durable database as the service would after a restart.
        reopened = NotificationCenter(Path(self.directory.name) / "notice.sqlite3", self.center._tokens,
                                      default_quiet_hours=[])
        self.addCleanup(reopened._connection.close)
        self.center = reopened
        self._make_due()
        self._delivery_tick()
        self.assertEqual("sent", self.center.human_request_completion(self.request_id)["state"])
        self.assertEqual(2, len(_HerderHandler.completions))
        self.assertEqual(_HerderHandler.completions[0]["body"], _HerderHandler.completions[1]["body"])
        repeated = self._call("noticeplace_answer", {"request_id": self.request_id, "value": "observe"})
        self.assertEqual("repair", repeated["value"])
        self.assertTrue(repeated["conflict"])
        self._delivery_tick()
        self.assertEqual(2, len(_HerderHandler.completions))

    def test_button_and_free_text_card_failure_with_native_outage_use_same_completion(self) -> None:
        from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller
        for surface in ("button", "text"):
            with self.subTest(surface=surface):
                self.request_id = self.request_id[:-1] + ("a" if surface == "button" else "b")
                self._create_bound("choice" if surface == "button" else "question")
                self.card_failure = True
                _HerderHandler.responses = [(502, {"status": "resume_failed", "reason": "agent-resume:native-unavailable", "retry": True})]
                codec = TelegramActionCodec("s" * 32)

                def api(method, payload):
                    if method == "getUpdates":
                        return {"ok": True, "result": []}
                    return http_api.telegram_api("bot-token", method, payload)

                poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec, api=api)
                if surface == "button":
                    poller._handle_callback({"id": "cb", "from": {"id": 42},
                        "data": codec.encode_human_choice(self.request_id, "0"),
                        "message": {"chat": {"id": -100}, "message_id": 42, "text": "Починить сейчас?"}})
                else:
                    poller._handle_message({"from": {"id": 42}, "chat": {"id": -100}, "message_id": 99,
                        "text": "repair", "reply_to_message": {"message_id": 42}})
                stored = self.center.human_request_completion(self.request_id)
                self.assertEqual("retryable", stored["state"])
                self.assertEqual("retryable", stored["card_state"])
                self.assertEqual("repair", self.center.get_human_request("p", self.request_id)["response_value"])
                first_ref = _HerderHandler.completions[-1]["body"]["result_ref"]
                self.card_failure = False
                self._make_due()
                self._delivery_tick()
                recovered = self.center.human_request_completion(self.request_id)
                self.assertEqual("sent", recovered["state"])
                self.assertEqual("sent", recovered["card_state"])
                self.assertEqual(first_ref, _HerderHandler.completions[-1]["body"]["result_ref"])

    def test_answer_and_outbox_roll_back_together_on_storage_failure(self) -> None:
        self._create_bound()
        with self.center._lock, self.center._connection:
            self.center._connection.execute(
                "CREATE TRIGGER fail_completion BEFORE INSERT ON human_request_completions "
                "BEGIN SELECT RAISE(ABORT, 'test outbox unavailable'); END")
        # The real HTTP request fails; an answer must not be committed without
        # the durable continuation owed to its bound native session.
        with self.assertRaises(RuntimeError):
            self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        self.assertEqual("pending", self.center.get_human_request("p", self.request_id)["state"])
        self.assertEqual({}, self.center.human_request_completion(self.request_id))
        self.assertEqual([], _HerderHandler.completions)
        with self.center._lock, self.center._connection:
            self.center._connection.execute("DROP TRIGGER fail_completion")
        self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        self.assertEqual(1, len(_HerderHandler.completions))

    def test_unknown_native_admission_and_generic_http_failure_are_never_retried(self) -> None:
        for code, reply in ((0, {}), (202, {"status": "resuming", "in_flight": True}),
                            (502, {"status": "resume_failed", "reason": "agent-resume:native-unavailable", "retry": False}),
                            (502, {"status": "resume_failed", "reason": "agent-resume:admission-unknown"}),
                            (502, {"error": "provider error", "status": "invalid"})):
            with self.subTest(reply=reply):
                # Each case uses a distinct durable binding.
                self.request_id = self.request_id[:-1] + str(len(_HerderHandler.completions) + 1)
                self._create_bound()
                _HerderHandler.responses = [(code, reply)]
                result = self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
                self.assertEqual("unknown", result["native"]["status"])
                calls = len(_HerderHandler.completions)
                self._make_due()
                self._delivery_tick()
                self._call("noticeplace_answer", {"request_id": self.request_id, "value": "observe"})
                self.assertEqual(calls, len(_HerderHandler.completions))

    def test_concurrent_http_answers_keep_one_answer_card_and_native_delivery(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        self._create_bound()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._call, "noticeplace_answer", {"request_id": self.request_id, "value": value})
                       for value in ("repair", "observe")]
            answers = [future.result(timeout=15) for future in futures]
        self.assertEqual(answers[0]["value"], answers[1]["value"])
        self.assertEqual(1, sum(answer["accepted"] for answer in answers))
        self.assertEqual(1, len(_HerderHandler.completions))
        self.assertEqual(1, sum(method == "editMessageText" for method, _ in self.telegram))

    def test_answer_keeps_both_buttons_and_conflicting_callback_acknowledges_canonical_choice(self) -> None:
        from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller
        self._create_bound()
        initial = json.loads(next(p["reply_markup"] for m, p in self.telegram if m == "sendMessage"))
        self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        marked = json.loads(next(p["reply_markup"] for m, p in self.telegram if m == "editMessageText"))
        rows = marked["inline_keyboard"]
        self.assertEqual(2, len(rows), "answer must retain both visible choices")
        self.assertEqual(["✅ Починить", "Наблюдать"], [row[0]["text"] for row in rows])
        self.assertEqual([r[0]["callback_data"] for r in initial["inline_keyboard"]],
                         [r[0]["callback_data"] for r in rows])
        codec = TelegramActionCodec("s" * 32)
        poller = TelegramInteractionPoller(self.center, "bot-token", {"42"}, codec,
            api=lambda method, payload: http_api.telegram_api("bot-token", method, payload))
        poller._handle_callback({"id": "conflicting-repeat", "from": {"id": 42},
            "data": rows[1][0]["callback_data"], "message": {"chat": {"id": -100}, "message_id": 42,
                                                                         "text": "Починить сейчас?"}})
        self.assertEqual(1, len(_HerderHandler.completions))
        self.assertEqual("repair", self.center.get_human_request("p", self.request_id)["response_value"])
        acknowledgements = [p["text"] for m, p in self.telegram if m == "answerCallbackQuery"]
        self.assertEqual(["✅ Выбрано: Починить"], acknowledgements)

    def test_future_canonical_reference_matches_herder_strict_uuid_gate(self) -> None:
        import re
        self._create_bound()
        self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        result = self._call("noticeplace_question_result", {"request_id": self.request_id})
        # Herder's strict request-identity gate requires RFC UUID variant bits;
        # the completion gate additionally accepts old opaque hex references.
        strict = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
        self.assertRegex(result["result_ref"], strict)
        self.assertEqual(result["result_ref"], _HerderHandler.completions[0]["body"]["result_ref"])

    def test_historical_completion_reference_stays_canonical_in_real_result_handler(self) -> None:
        self._create_bound()
        self._call("noticeplace_answer", {"request_id": self.request_id, "value": "repair"})
        historical = "6944969d-ac29-5003-fba0-a545f0c2891c"
        with self.center._lock, self.center._connection:
            self.center._connection.execute("UPDATE human_request_completions SET result_ref=? WHERE request_id=?",
                                            (historical, self.request_id))
        result = self._call("noticeplace_question_result", {"request_id": self.request_id})
        self.assertEqual(historical, result["result_ref"], "stored completion reference overrides recomputation")
        self.assertEqual("repair", result["answer"])

    def test_incident_buttons_remain_visible_and_repeat_never_resumes_again(self) -> None:
        from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller
        choice_request = '11111111-1111-4111-8111-111111111111'
        created = self.center.create_event('p', 'incident-buttons', {
            'schema': 'notify.event.v1', 'project': 'hermes', 'recipient': 'me', 'kind': 'notification',
            'severity': 'important', 'title': 'Выбери путь', 'body': 'Нужен выбор', 'dedup_key': 'incident-buttons',
            'choice_request_id': choice_request,
            'choices': [{'choice_id': 'repair', 'label': 'Починить'}, {'choice_id': 'observe', 'label': 'Наблюдать'}]})
        incident_id = created['incident_id']
        codec = TelegramActionCodec('s' * 32)
        sender = http_api.TelegramSender('bot-token', '-100', action_codec=codec, center=self.center)
        import io
        import urllib.parse
        def fake_sender_network(request, **kwargs):
            payload = {key: values[0] for key, values in urllib.parse.parse_qs(request.data.decode()).items()}
            method = request.full_url.rsplit('/', 1)[-1]
            return io.BytesIO(json.dumps(http_api.telegram_api('bot-token', method, payload)).encode())
        with patch('notification_center.http_api.urllib.request.urlopen', side_effect=fake_sender_network):
            http_api.DeliveryWorker(self.center, sender).run_once()
        initial = json.loads(next(p['reply_markup'] for m,p in self.telegram if m == 'sendMessage'))
        native = []
        def resume(request_id, choice_id, actor):
            native.append((request_id, choice_id, actor))
            return {'status': 'accepted', 'choice_id': choice_id}
        poller = TelegramInteractionPoller(self.center, 'bot-token', {'42'}, codec, choice_callback=resume,
            api=lambda method, payload: http_api.telegram_api('bot-token', method, payload))
        def tap(index):
            poller._handle_callback({'id': f'incident-{index}', 'from': {'id': 42},
                'data': initial['inline_keyboard'][index][0]['callback_data'],
                'message': {'chat': {'id': -100}, 'message_id': 42, 'text': 'Выбери путь'}})
        tap(1)
        edits = [p for m,p in self.telegram if m == 'editMessageText']
        marked = json.loads(edits[-1]['reply_markup'])['inline_keyboard']
        self.assertEqual(['Починить', '✅ Наблюдать'], [r[0]['text'] for r in marked])
        self.assertEqual([r[0]['callback_data'] for r in initial['inline_keyboard']],
                         [r[0]['callback_data'] for r in marked])
        repeat = self._call('noticeplace_answer', {'incident_id': incident_id, 'request_id': choice_request,
                                                  'choice_id': 'repair'})
        self.assertEqual('observe', repeat['choice_id'])
        self.assertTrue(repeat['conflict'])
        tap(0)
        self.assertEqual(1, len(native))
        self.assertEqual('observe', self.center.selection_receipt(choice_request)['choice_id'])
        self.assertEqual('✅ Выбрано: Наблюдать', [p['text'] for m,p in self.telegram if m == 'answerCallbackQuery'][-1])

    def test_expired_and_cancelled_http_answers_explicitly_accept_nothing(self) -> None:
        for state in ("expired", "cancelled"):
            request_id = f"req-{state}"
            self.center.create_human_request("p", {
                "schema": "ask_human.request.v1", "request_id": request_id, "project": "hermes",
                "recipient": "me", "mode": "question", "message": "Вопрос?", "allowed_actors": ["telegram:42"],
            })
            with self.center._lock, self.center._connection:
                self.center._connection.execute("UPDATE human_requests SET state=? WHERE request_id=?", (state, request_id))
            result = self._call("noticeplace_answer", {"request_id": request_id, "value": "новый ответ"})
            self.assertEqual("no_accept", result["answer_status"])
            self.assertIsNone(result["value"])
            self.assertEqual("skipped", result["card"])
            self.assertEqual("not_attempted", result["native"]["status"])
            self.assertIsNone(self.center.selection_receipt(request_id))
        self.assertEqual([], _HerderHandler.completions)

    def test_secret_http_answer_and_legacy_ordinary_completion_are_rejected_without_leak(self) -> None:
        self.center.create_human_request("p", {
            "schema": "ask_human.request.v1", "request_id": "req-secret", "project": "hermes", "kind": "secret",
            "recipient": "me", "mode": "question", "message": "Используйте AskSecret", "allowed_actors": ["telegram:42"],
        })
        raw_value = "sensitive-test-answer"
        with self.assertRaises(RuntimeError) as raised:
            self._call("noticeplace_answer", {"request_id": "req-secret", "value": raw_value})
        self.assertNotIn(raw_value, str(raised.exception))
        from notification_center.core import ValidationError
        with self.assertRaises(ValidationError):
            self.center.resolve_human_request("p", "req-secret", "telegram:42", raw_value)
        with self.center._lock, self.center._connection:
            # Historical data must stay opaque even if written by an old producer.
            self.center._connection.execute("UPDATE human_requests SET state='resolved', response_value=? WHERE request_id='req-secret'", (raw_value,))
        view = self.center.get_human_request("p", "req-secret")
        result = self._call("noticeplace_question_result", {"request_id": "req-secret"})
        self.assertNotIn(raw_value, json.dumps([view, result]))
        api_calls = []
        self.assertEqual("skipped", http_api.mark_human_request_resolved_card(
            self.center, {**view, "response_value": raw_value}, "telegram:42", lambda *args: api_calls.append(args))["status"])
        self.assertEqual([], api_calls)
        self.assertEqual([], _HerderHandler.completions)

    def _call(self, name: str, arguments: dict) -> dict:
        return self.tools[name]["handler"](arguments)

    def _herder_create(self, request_id: str, message: str, choices: list[dict] | None = None) -> dict:
        """Agent Herder binds the question and returns the binding to the agent."""
        import urllib.request
        base = f"http://127.0.0.1:{self.herder.server_address[1]}"
        body = {"kind": "user", "harness": "opencode", "session_id": "ses_executor_1",
                "cwd": self.directory.name, "message": message, "choices": choices or []}
        request = urllib.request.Request(
            f"{base}/internal/human-requests", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            return json.loads(urllib.request.urlopen(request, timeout=8).read())
        except urllib.error.HTTPError as error:
            raise AssertionError(f"herder create rejected: {error.code} {error.read().decode()}") from error

    def test_one_creation_yields_one_card_then_answer_and_one_native_resume(self) -> None:
        request_id = "3f5b0c2a-1d44-4c6e-9a71-58b1c0f7d902"
        choices = [{"label": "Да, починить", "value": "repair"}, {"label": "Только наблюдать", "value": "observe"}]
        binding = self._herder_create(request_id, "Диск заполнен на 98%. Починить сейчас?", choices)["binding"]
        created = self._call("noticeplace_ask_human", {
            "request_id": binding["request_id"],
            "harness": binding["native_target"]["harness"],
            "session_id": binding["native_target"]["session_id"],
            "cwd": binding["native_target"]["cwd"],
            "message": "Диск заполнен на 98%. Починить сейчас?",
            "choices": choices,
        })
        self.assertEqual(request_id, created["request_id"])
        self.assertEqual("pending", created["state"])
        self.assertEqual("resume", created["continuation"])
        self.assertEqual("sent", created["card"])
        self.assertFalse(created["idempotent"])

        sent = [payload for method, payload in self.telegram if method == "sendMessage"]
        self.assertEqual(1, len(sent))
        self.assertIn("Диск заполнен на 98%", sent[0]["text"])
        self.assertIn("Да, починить", sent[0]["reply_markup"])
        self.assertEqual([], _HerderHandler.completions)

        # One creation means one card, even on a retried create.
        replayed = self._call("noticeplace_ask_human", {
            "request_id": binding["request_id"], "harness": binding["native_target"]["harness"],
            "session_id": binding["native_target"]["session_id"], "cwd": binding["native_target"]["cwd"],
            "message": "Диск заполнен на 98%. Починить сейчас?", "choices": choices,
        })
        self.assertTrue(replayed["idempotent"])
        self.assertEqual(1, len([p for m, p in self.telegram if m == "sendMessage"]))

        pending = self._call("noticeplace_pending_questions", {})
        self.assertEqual(1, len(pending["questions"]))
        entry = pending["questions"][0]
        self.assertEqual(request_id, entry["request_id"])
        self.assertEqual(["repair", "observe"], [choice["value"] for choice in entry["choices"]])
        self.assertEqual("ses_executor_1", entry["native"]["session_id"])
        self.assertEqual("opencode", entry["native"]["harness"])

        answered = self._call("noticeplace_answer", {"request_id": request_id, "value": "repair"})
        self.assertEqual("resolved", answered["state"])
        self.assertEqual("repair", answered["value"])
        self.assertEqual("marked", answered["card"])

        edits = [payload for method, payload in self.telegram if method == "editMessageText"]
        self.assertEqual(1, len(edits))
        self.assertIn("✅ Выбрано: Да, починить", edits[0]["text"])
        self.assertEqual(1, len(_HerderHandler.completions))
        completion = _HerderHandler.completions[0]
        self.assertEqual("/internal/human-requests/ask-user-completion", completion["path"])
        self.assertEqual(request_id, completion["body"]["request_id"])
        self.assertNotIn("repair", json.dumps(completion["body"]))

        # The resumed session reads the canonical answer from Notice Place.
        canonical = self._call("noticeplace_question_result", {"request_id": request_id})
        self.assertEqual("resolved", canonical["state"])
        self.assertEqual("repair", canonical["answer"])
        self.assertEqual(36, len(canonical["result_ref"]))
        self.assertEqual("ses_executor_1", canonical["native"]["session_id"])
        # A conflicting repeat keeps the first accepted value: no second card
        # edit, no second native resume.
        repeated = self._call("noticeplace_answer", {"request_id": request_id, "value": "observe"})
        self.assertEqual("repair", repeated["value"])
        self.assertEqual(1, len(_HerderHandler.completions))
        self.assertEqual(1, len([p for m, p in self.telegram if m == "editMessageText"]))

    def test_incomplete_binding_creates_no_question_and_no_card(self) -> None:
        for missing in ("harness", "session_id"):
            arguments = {
                "request_id": "9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f", "harness": "opencode",
                "session_id": "ses_executor_1", "cwd": self.directory.name, "message": "Починить?",
            }
            arguments.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                self._call("noticeplace_ask_human", arguments)
        self.assertEqual([], [p for m, p in self.telegram if m == "sendMessage"])
        self.assertEqual([], _HerderHandler.completions)

    def test_incident_progress_is_posted_with_a_stable_idempotency_key(self) -> None:
        created = self.center.create_event("p", "health-1", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Диск", "body": "Диск заполнен", "dedup_key": "health:disk",
            "event_type": "health.degraded", "source_id": "disk", "host_id": "host",
            "signal_type": "disk", "correlation_id": "corr",
        })
        first = self._call("noticeplace_incident_progress", {
            "incident_id": created["incident_id"], "step": "Освободил место",
            "evidence_refs": ["probe:disk"], "source_fingerprint": "fp-1",
        })
        repeat = self._call("noticeplace_incident_progress", {
            "incident_id": created["incident_id"], "step": "Освободил место",
            "evidence_refs": ["probe:disk"], "source_fingerprint": "fp-1",
        })
        self.assertEqual(first["progress"].get("event_id"), repeat["progress"].get("event_id"))
        with self.assertRaises(ValueError):
            self._call("noticeplace_incident_progress", {
                "incident_id": created["incident_id"], "step": "Проверил и без проверки источника",
                "evidence_refs": ["probe:disk"], "observed_state": "healthy",
                "source_id": "disk", "verifier_id": "disk", "verification_id": "v1",
            })


if __name__ == "__main__":
    unittest.main()
