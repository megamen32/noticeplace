"""Producer authorization, retry safety and exact-card analysis progress."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError
from notification_center.http_api import build_handler, refresh_human_request_progress, refresh_human_request_original
from notification_center.human_request_original import utf16_length
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


URL = "https://agent.bezrabotnyi.com/#/session/codex%3Atest-session"


class HumanRequestProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db"
        self.tokens = {"owner": {"project": "userio", "max_severity": "critical"},
                       "other": {"project": "other", "max_severity": "critical"}}
        self.center = NotificationCenter(self.path, self.tokens)
        self.center.create_human_request("owner", {
            "schema": "ask_human.request.v1", "request_id": "r1", "project": "userio",
            "recipient": "owner", "mode": "choice", "message": "Разобрать сообщение?",
            "choices": [{"label": "Глубокий разбор", "value": "deep_analysis"}, {"label": "Неважно", "value": "ignore"}],
            "allowed_actors": ["telegram:42"], "expires_at": None,
        })
        self.center.bind_human_request_message("owner", "r1", "-1001", 501)
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test"})
        self.env.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.center._connection.close()
        self.tmp.cleanup()

    def resolve(self):
        self.center.resolve_human_request("owner", "r1", "telegram:42", "deep_analysis")
        # Fixtures below represent analysis after the initial accepted edit.
        return self.center.confirm_human_request_progress_card("r1")

    def api(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 501, "chat": {"id": -1001}}}

    def test_accepted_is_durable_atomic_with_answer(self):
        answer = self.resolve()
        self.assertEqual("accepted", answer["progress"]["phase"])
        self.assertEqual("Принял, запускаю глубокий разбор…", answer["progress"]["message"])
        reopened = NotificationCenter(self.path, self.tokens)
        self.assertEqual(answer["progress"], reopened.get_human_request("owner", "r1")["progress"])
        reopened._connection.close()
        self.assertEqual(answer["progress"], self.resolve()["progress"])

    def test_progress_requires_original_scope_selected_action_and_binding(self):
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running"})
        self.resolve()
        with self.assertRaises(AuthorizationError):
            self.center.update_human_request_progress("other", "r1", {"phase": "running"})
        with self.assertRaises(AuthorizationError):
            self.center.update_human_request_progress("invalid", "r1", {"phase": "running"})
        self.center._connection.execute("UPDATE human_requests SET telegram_message_id=NULL WHERE request_id='r1'")
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running"})

    def test_wrong_resolution_is_not_analysis(self):
        self.center.resolve_human_request("owner", "r1", "telegram:42", "ignore")
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running"})

    def test_url_and_payload_are_narrowly_validated(self):
        self.resolve()
        for url in ("http://agent.bezrabotnyi.com/#/session/x", "https://evil.test/#/session/x",
                    "https://agent.bezrabotnyi.com.evil.test/#/session/x", "https://user@agent.bezrabotnyi.com/#/session/x",
                    "https://agent.bezrabotnyi.com:443/#/session/x", "https://agent.bezrabotnyi.com/?secret=x#/session/x",
                    "https://agent.bezrabotnyi.com/#/other/x", "https://agent.bezrabotnyi.com/#/session/x\n"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": url})
        for body in ({"phase": "running", "chat_id": "evil"}, {"phase": "unknown"},
                     {"phase": "completed", "message": "😀" * 751}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                self.center.update_human_request_progress("owner", "r1", body)

    def test_monotonic_idempotent_updates_preserve_link_and_terminal_result(self):
        self.resolve()
        running = self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": URL})
        repeat = self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": URL})
        self.assertEqual(running["progress"], repeat["progress"])
        stale = self.center.update_human_request_progress("owner", "r1", {"phase": "accepted"})
        self.assertEqual(running["progress"], stale["progress"])
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": URL + "different"})
        completed = self.center.update_human_request_progress("owner", "r1", {"phase": "completed", "message": "Разбор готов"})
        self.assertEqual(URL, completed["progress"]["session_url"])
        self.assertEqual(completed["progress"], self.center.update_human_request_progress("owner", "r1", {"phase": "completed", "message": "Разбор готов"})["progress"])
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "failed"})
        with self.assertRaises(ValidationError):
            self.center.update_human_request_progress("owner", "r1", {"phase": "completed", "message": "Другой результат"})

    def test_producer_edits_same_card_and_retains_button_on_completion(self):
        before = self.resolve()
        refresh_human_request_progress(self.center, "owner", "r1", {"phase": "running", "session_url": URL}, api=self.api)
        after = refresh_human_request_progress(self.center, "owner", "r1", {"phase": "completed", "message": "Ответ подготовлен"}, api=self.api)
        for method, payload in self.calls:
            self.assertEqual("editMessageText", method)
            self.assertEqual(("-1001", 501), (payload["chat_id"], payload["message_id"]))
            self.assertEqual(URL, json.loads(payload["reply_markup"])["inline_keyboard"][0][0]["url"])
        self.assertIn("Ответ подготовлен", self.calls[-1][1]["text"])
        for key in ("response_actor", "response_value", "resolved_at", "telegram_chat_id", "telegram_message_id"):
            self.assertEqual(before[key], after[key])

    def test_edit_failure_retry_does_not_change_card_or_create_message(self):
        self.resolve()
        body = {"phase": "running", "session_url": URL}
        with self.assertRaises(TimeoutError):
            refresh_human_request_progress(self.center, "owner", "r1", body, api=mock.Mock(side_effect=TimeoutError))
        self.assertEqual("running", self.center.get_human_request("owner", "r1")["progress"]["phase"])
        refresh_human_request_progress(self.center, "owner", "r1", body, api=self.api)
        refresh_human_request_progress(self.center, "owner", "r1", body,
                                      api=mock.Mock(side_effect=RuntimeError("Bad Request: message is not modified")))
        with self.assertRaises(RuntimeError):
            refresh_human_request_progress(self.center, "owner", "r1", body,
                api=mock.Mock(return_value={"ok": True, "result": {"message_id": 999, "chat": {"id": -1001}}}))

    def test_utf16_bound_keeps_result_and_url_without_claiming_new_attachment(self):
        self.center.enrich_human_request_original("owner", "r1", "🧭" * 1400)
        self.resolve()
        refresh_human_request_progress(self.center, "owner", "r1",
            {"phase": "completed", "message": "Результат" * 150, "session_url": URL}, api=self.api)
        text = self.calls[-1][1]["text"]
        self.assertLessEqual(utf16_length(text), 4096)
        self.assertIn(URL, text)
        self.assertIn("Результат" * 150, text)
        self.assertNotIn("приложены Markdown-файлом", text)

    def test_original_backfill_retains_running_status_and_link_button(self):
        self.resolve()
        refresh_human_request_progress(self.center, "owner", "r1", {"phase": "running", "session_url": URL}, api=self.api)
        refresh_human_request_original(self.center, "owner", "r1", "Исходное сообщение", api=self.api)
        self.assertIn(URL, self.calls[-1][1]["text"])
        self.assertEqual(URL, json.loads(self.calls[-1][1]["reply_markup"])["inline_keyboard"][0][0]["url"])

    def test_callback_immediately_edits_accepted_and_replay_does_not_revert_running(self):
        codec = TelegramActionCodec("x" * 32)
        callback = {"id": "click", "from": {"id": 42}, "data": codec.encode("human_choice", "r1", choice_id="0"),
                    "message": {"message_id": 501, "chat": {"id": -1001}, "text": "Старая карточка"}}
        poller = TelegramInteractionPoller(self.center, "test", {"42"}, codec, api=self.api)
        poller._handle_callback(callback)
        self.assertEqual("accepted", self.center.get_human_request("owner", "r1")["progress"]["phase"])
        self.assertIn("Принял, запускаю глубокий разбор…", self.calls[0][1]["text"])
        self.assertEqual([], json.loads(self.calls[0][1]["reply_markup"])["inline_keyboard"])
        refresh_human_request_progress(self.center, "owner", "r1", {"phase": "running", "session_url": URL}, api=self.api)
        poller._handle_callback(callback)
        edit = [payload for method, payload in self.calls if method == "editMessageText"][-1]
        self.assertIn(URL, edit["text"])
        self.assertNotIn("Принял, запускаю", edit["text"])
        self.assertEqual(501, edit["message_id"])

    def test_forged_card_callback_does_not_resolve_or_edit(self):
        codec = TelegramActionCodec("x" * 32)
        poller = TelegramInteractionPoller(self.center, "test", {"42"}, codec, api=self.api)
        poller._handle_callback({"id": "click", "from": {"id": 42},
            "data": codec.encode("human_choice", "r1", choice_id="0"),
            "message": {"message_id": 999, "chat": {"id": -1001}}})
        self.assertEqual("pending", self.center.get_human_request("owner", "r1")["state"])
        self.assertFalse(any(method == "editMessageText" for method, _ in self.calls))

    def test_producer_cannot_observe_answer_before_immediate_edit_finishes(self):
        codec = TelegramActionCodec("x" * 32)
        editing, release, observed = threading.Event(), threading.Event(), threading.Event()
        def blocking_api(method, payload):
            if method == "editMessageText":
                editing.set()
                self.assertTrue(release.wait(2))
            return self.api(method, payload)
        poller = TelegramInteractionPoller(self.center, "test", {"42"}, codec, api=blocking_api)
        worker = threading.Thread(target=lambda: poller._handle_callback({"id": "click", "from": {"id": 42},
            "data": codec.encode("human_choice", "r1", choice_id="0"),
            "message": {"message_id": 501, "chat": {"id": -1001}}}))
        def observe():
            self.center.get_human_request("owner", "r1")
            observed.set()
        observer = threading.Thread(target=observe)
        worker.start()
        try:
            self.assertTrue(editing.wait(2))
            observer.start()
            self.assertFalse(observed.wait(0.05))
        finally:
            release.set()
            worker.join(2)
            if observer.ident is not None:
                observer.join(2)
        self.assertTrue(observed.is_set())

    def test_failed_progress_human_text_on_same_card(self):
        self.resolve()
        refresh_human_request_progress(self.center, "owner", "r1", {"phase": "failed", "message": "Исполнитель недоступен. Повторите запрос позже."}, api=self.api)
        self.assertIn("Не удалось завершить", self.calls[-1][1]["text"])
        self.assertIn("Повторите запрос позже", self.calls[-1][1]["text"])

    def test_initial_edit_timeout_keeps_answer_hidden_durably_until_retry(self):
        codec = TelegramActionCodec("x" * 32)
        callback = {"id": "click", "from": {"id": 42},
                    "data": codec.encode("human_choice", "r1", choice_id="0"),
                    "message": {"message_id": 501, "chat": {"id": -1001}}}
        poller = TelegramInteractionPoller(self.center, "test", {"42"}, codec,
            api=mock.Mock(side_effect=TimeoutError("Telegram timeout")))
        with self.assertRaises(TimeoutError):
            poller._handle_callback(callback)
        hidden = self.center.get_human_request("owner", "r1")
        self.assertEqual("pending", hidden["state"])
        self.assertIsNone(hidden["response_value"])
        self.assertIsNone(hidden["response_actor"])
        self.assertIsNone(hidden["resolved_at"])
        self.assertTrue(hidden["progress_delivery_pending"])
        internal = self.center.get_human_request_from_telegram("r1")
        self.assertEqual("deep_analysis", internal["response_value"])
        reopened = NotificationCenter(self.path, self.tokens)
        self.assertEqual("pending", reopened.get_human_request("owner", "r1")["state"])
        reopened._connection.close()
        with self.assertRaisesRegex(ValidationError, "confirmed Telegram acceptance"):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": URL})
        from notification_center.http_api import _human_request_http_result
        http_view = _human_request_http_result(hidden)
        self.assertTrue(http_view["progress_delivery_pending"])
        self.assertIsNone(_human_request_http_result(internal)["response_value"])
        TelegramInteractionPoller(self.center, "test", {"42"}, codec, api=self.api)._handle_callback(callback)
        ready = self.center.get_human_request("owner", "r1")
        self.assertEqual("resolved", ready["state"])
        self.assertEqual("deep_analysis", ready["response_value"])
        self.assertFalse(ready["progress_delivery_pending"])
        self.assertIsNotNone(ready["telegram_progress_confirmed_at"])
        self.assertEqual(internal["resolved_at"], ready["resolved_at"])

    def test_accepted_progress_retry_confirms_visibility_without_creating_message(self):
        self.center.resolve_human_request("owner", "r1", "telegram:42", "deep_analysis")
        with self.assertRaises(TimeoutError):
            refresh_human_request_progress(self.center, "owner", "r1", {"phase": "accepted"},
                                          api=mock.Mock(side_effect=TimeoutError))
        self.assertEqual("pending", self.center.get_human_request("owner", "r1")["state"])
        result = refresh_human_request_progress(self.center, "owner", "r1", {"phase": "accepted"},
            api=mock.Mock(side_effect=RuntimeError("Bad Request: message is not modified")))
        self.assertEqual("resolved", result["state"])
        self.assertFalse(result["progress_delivery_pending"])

    def test_delayed_running_url_attaches_once_after_completion_without_regression(self):
        self.resolve()
        finished = refresh_human_request_progress(self.center, "owner", "r1", {"phase": "completed", "message": "Разбор готов"}, api=self.api)
        result = refresh_human_request_progress(self.center, "owner", "r1",
            {"phase": "running", "session_url": URL, "message": "Запускаю"}, api=self.api)
        self.assertEqual("completed", result["progress"]["phase"])
        self.assertEqual("Разбор готов", result["progress"]["message"])
        self.assertEqual(URL, result["progress"]["session_url"])
        self.assertIn("Разбор готов", self.calls[-1][1]["text"])
        self.assertEqual(URL, json.loads(self.calls[-1][1]["reply_markup"])["inline_keyboard"][0][0]["url"])
        repeated = refresh_human_request_progress(self.center, "owner", "r1", {"phase": "running", "session_url": URL}, api=self.api)
        self.assertEqual(result["progress"], repeated["progress"])
        self.assertEqual(finished["resolved_at"], result["resolved_at"])
        with self.assertRaisesRegex(ValidationError, "immutable"):
            self.center.update_human_request_progress("owner", "r1", {"phase": "running", "session_url": URL + "other"})

    def test_terminal_same_phase_can_receive_missing_url_but_not_new_result(self):
        self.resolve()
        self.center.update_human_request_progress("owner", "r1", {"phase": "failed", "message": "Исполнитель недоступен"})
        result = refresh_human_request_progress(self.center, "owner", "r1", {"phase": "failed", "session_url": URL}, api=self.api)
        self.assertEqual("failed", result["progress"]["phase"])
        self.assertEqual("Исполнитель недоступен", result["progress"]["message"])
        self.assertEqual(URL, result["progress"]["session_url"])
        with self.assertRaisesRegex(ValidationError, "terminal"):
            self.center.update_human_request_progress("owner", "r1", {"phase": "failed", "message": "Изменённый результат"})

    def test_http_real_loopback_producer_scope_and_same_card(self):
        self.resolve()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health", "mcp"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch("notification_center.http_api.telegram_api", side_effect=lambda _token, method, payload: self.api(method, payload)):
                for token, expected in (("other", 401), ("bad", 401), ("owner", 200), ("owner", 200)):
                    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/human-requests/r1/progress",
                        data=json.dumps({"phase": "running", "session_url": URL}).encode(),
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(request, timeout=2) as response:
                            status, body = response.status, json.loads(response.read())
                    except urllib.error.HTTPError as error:
                        status, body = error.code, json.loads(error.read())
                        error.close()
                    self.assertEqual(expected, status, body)
                self.assertEqual(2, len(self.calls))
                self.assertTrue(all(payload["message_id"] == 501 for _, payload in self.calls))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_http_get_masks_selected_answer_until_durable_card_receipt(self):
        self.center.resolve_human_request("owner", "r1", "telegram:42", "deep_analysis")
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health", "mcp"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/human-requests/r1",
                headers={"Authorization": "Bearer owner"})
            with urllib.request.urlopen(request, timeout=2) as response:
                hidden = json.loads(response.read())
            self.assertEqual("pending", hidden["state"])
            self.assertIsNone(hidden["response_value"])
            self.assertTrue(hidden["progress_delivery_pending"])
            refresh_human_request_progress(self.center, "owner", "r1", {"phase": "accepted"}, api=self.api)
            with urllib.request.urlopen(request, timeout=2) as response:
                ready = json.loads(response.read())
            self.assertEqual("resolved", ready["state"])
            self.assertEqual("deep_analysis", ready["response_value"])
            self.assertFalse(ready["progress_delivery_pending"])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
