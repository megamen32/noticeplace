"""Focused contract tests for durable AskHuman requests."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter, ValidationError
from notification_center.http_api import TelegramSender, build_handler, send_human_request_telegram
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller
from mcp.notify_mcp import tool_ask_human
from mcp.notify_mcp import tool_specs


class AskHumanEntrypointTests(unittest.TestCase):
    """Prove the installed command loads its private runtime environment."""

    def test_entrypoint_loads_default_runtime_env(self) -> None:
        """Keep Codex registration simple by sourcing one fixed env file."""
        script = (Path(__file__).parents[1] / "bin" / "ask-human-mcp").read_text()
        self.assertIn('$HOME/.config/ask-human/env', script)
        self.assertIn('ASK_HUMAN_ENV_FILE', script)


class HumanRequestTests(unittest.TestCase):
    """Prove request validation and immutable resolution in the domain layer."""

    def setUp(self) -> None:
        """Create isolated durable state for each test."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"token": {"project": "hermes", "max_severity": "critical"}})

    def tearDown(self) -> None:
        """Remove isolated state."""
        self.tempdir.cleanup()

    def request(self, **overrides: object) -> dict[str, object]:
        """Return one valid choice request with stable values."""
        payload: dict[str, object] = {
            "schema": "ask_human.request.v1", "request_id": "deploy-1", "project": "hermes",
            "recipient": "operator", "mode": "choice", "message": "Deploy now?",
            "choices": [{"label": "Now", "value": "now"}, {"label": "Later", "value": "later"}],
            "allowed_actors": ["telegram:42"], "expires_at": time.time() + 60,
        }
        payload.update(overrides)
        return payload

    def test_modes_choice_bounds_and_actor_binding(self) -> None:
        """Accept four internal choices while enforcing bounded choice and actor contracts."""
        created = self.center.create_human_request("token", self.request())
        self.assertEqual("pending", created["state"])
        self.assertEqual("choice", created["mode"])
        for mode in ("notify", "question"):
            result = self.center.create_human_request("token", self.request(request_id=f"mode-{mode}", mode=mode, choices=None))
            self.assertEqual(mode, result["mode"])
        four_choices = [
            {"label": f"Option {index}", "value": f"option-{index}"}
            for index in range(4)
        ]
        accepted = self.center.create_human_request(
            "token", self.request(request_id="four-choices", choices=four_choices)
        )
        self.assertEqual(four_choices, accepted["choices"])
        five_choices = four_choices + [{"label": "Option 5", "value": "option-5"}]
        for choices in ([{"label": "One", "value": "one"}], five_choices):
            with self.assertRaisesRegex(ValidationError, "between two and four"):
                self.center.create_human_request("token", self.request(request_id=f"bad-{len(choices)}", choices=choices))
        with self.assertRaisesRegex(ValidationError, "not allowed"):
            self.center.resolve_human_request("token", "deploy-1", "telegram:7", "now")

    def test_create_is_exactly_idempotent_and_rejects_payload_conflicts(self) -> None:
        """Return the durable request on exact replay and reject every changed identity field."""
        center = NotificationCenter(
            Path(self.tempdir.name) / "idempotent.sqlite3",
            {"wildcard": {"project": "*", "max_severity": "critical"}},
        )
        expires_at = time.time() + 60
        payload = self.request(request_id="idempotent-replay", expires_at=expires_at)
        created = center.create_human_request("wildcard", payload)
        replayed = center.create_human_request("wildcard", dict(payload))
        self.assertFalse(created["idempotent"])
        self.assertTrue(replayed["idempotent"])
        self.assertEqual(
            {key: value for key, value in created.items() if key != "idempotent"},
            {key: value for key, value in replayed.items() if key != "idempotent"},
        )

        conflicts = [
            {"project": "other"},
            {"recipient": "another-operator"},
            {"mode": "question", "choices": None},
            {"message": "Deploy later?"},
            {"choices": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]},
            {"allowed_actors": ["telegram:7"]},
            {"expires_at": expires_at + 1},
        ]
        for index, changes in enumerate(conflicts):
            with self.subTest(changes=changes):
                request_id = f"idempotency-conflict-{index}"
                original = self.request(request_id=request_id, expires_at=expires_at)
                center.create_human_request("wildcard", original)
                with self.assertRaisesRegex(ValidationError, "idempotency conflict"):
                    center.create_human_request("wildcard", {**original, **changes})

    def test_original_text_is_exact_durable_and_part_of_idempotency(self) -> None:
        """Preserve original Markdown byte-for-byte and reject changed replay content."""
        original = "# Заголовок\n\nТекст с `кодом` и эмодзи 🧭\n"
        payload = self.request(request_id="with-original", original_text=original)
        created = self.center.create_human_request("token", payload)
        self.assertEqual(original, created["original_text"])
        self.assertEqual(original, self.center.create_human_request("token", dict(payload))["original_text"])
        with self.assertRaisesRegex(ValidationError, "idempotency conflict"):
            self.center.create_human_request("token", {**payload, "original_text": original + "changed"})
        with self.assertRaisesRegex(ValidationError, "100000 UTF-8 bytes"):
            self.center.create_human_request(
                "token", self.request(request_id="too-large", original_text="я" * 50_001)
            )
        with self.assertRaisesRegex(ValidationError, "must be a string"):
            self.center.create_human_request(
                "token", self.request(request_id="wrong-type", original_text=42)
            )

    def test_single_winner_is_idempotent_immutable_and_replayable(self) -> None:
        """Persist one stable answer and reject a conflicting callback without mutation."""
        self.center.create_human_request("token", self.request())
        first = self.center.resolve_human_request("token", "deploy-1", "telegram:42", "later")
        retry = self.center.resolve_human_request("token", "deploy-1", "telegram:42", "later")
        self.assertEqual("later", first["response_value"])
        self.assertEqual(first["resolved_at"], retry["resolved_at"])
        with self.assertRaisesRegex(ValidationError, "already resolved"):
            self.center.resolve_human_request("token", "deploy-1", "telegram:42", "now")
        self.assertEqual(first, self.center.get_human_request("token", "deploy-1"))

    def test_question_expiry_and_cancel_are_terminal(self) -> None:
        """Expire overdue questions and prevent resolution after explicit cancellation."""
        self.center.create_human_request("token", self.request(request_id="expired", mode="question", choices=None, expires_at=time.time() - 1))
        self.assertEqual("expired", self.center.get_human_request("token", "expired")["state"])
        self.center.create_human_request("token", self.request(request_id="cancelled", mode="question", choices=None))
        cancelled = self.center.cancel_human_request("token", "cancelled", "api")
        self.assertEqual("cancelled", cancelled["state"])
        with self.assertRaisesRegex(ValidationError, "cancelled"):
            self.center.resolve_human_request("token", "cancelled", "telegram:42", "wait")

    def test_two_connections_preserve_one_winner(self) -> None:
        """Allow only one answer across independent SQLite connections."""
        self.center.create_human_request("token", self.request())
        other = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"token": {"project": "hermes", "max_severity": "critical"}})
        def resolve(center: NotificationCenter, value: str) -> str:
            try:
                return str(center.resolve_human_request("token", "deploy-1", "telegram:42", value)["response_value"])
            except ValidationError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: resolve(*pair), [(self.center, "now"), (other, "later")]))
        winner = str(self.center.get_human_request("token", "deploy-1")["response_value"])
        self.assertIn(winner, {"now", "later"})
        self.assertIn(winner, results)
        self.assertEqual(winner, self.center.resolve_human_request("token", "deploy-1", "telegram:42", winner)["response_value"])

    def test_three_signed_buttons_and_telegram_selection(self) -> None:
        """Build the actual Bot API keyboard and resolve its selected value."""
        codec = TelegramActionCodec("x" * 32)
        request = self.request(choices=[
            {"label": "Now", "value": "now"},
            {"label": "Later", "value": "later"},
            {"label": "Cancel", "value": "cancel"},
        ])
        created = self.center.create_human_request("token", request)
        calls: list[tuple[str, dict[str, object]]] = []

        def send_api(method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method, payload))
            return {"ok": True, "result": {"message_id": 501, "chat": {"id": 42}}}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot",
            "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
        }, clear=False):
            receipt = send_human_request_telegram(self.center, "token", created, api=send_api)
        self.assertEqual({"status": "sent", "chat_id": "42", "message_id": 501}, receipt)
        keyboard = json.loads(str(calls[0][1]["reply_markup"]))
        callbacks = [row[0]["callback_data"] for row in keyboard["inline_keyboard"]]
        self.assertEqual(3, len(callbacks))
        self.assertTrue(all(codec.decode(value) for value in callbacks))

        update = {"update_id": 30, "callback_query": {
            "id": "choice", "from": {"id": 42}, "data": callbacks[1],
            "message": {"message_id": 501, "chat": {"id": 42}, "text": "Deploy now?"},
        }}
        def poll_api(method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": [update] if method == "getUpdates" else True}
        TelegramInteractionPoller(self.center, "bot", {"42"}, codec, api=poll_api).poll_once()
        self.assertEqual("later", self.center.get_human_request("token", "deploy-1")["response_value"])

    def test_original_markdown_is_sent_and_document_receipt_is_persisted(self) -> None:
        """Reply to a long interactive card with the exact UTF-8 Markdown document."""
        original = "# Полный текст\n\n" + ("абзац\n" * 800)
        created = self.center.create_human_request(
            "token", self.request(request_id="long-source", original_text=original)
        )
        calls: list[dict[str, object]] = []

        def send_api(_method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            return {"ok": True, "result": {"message_id": 601, "chat": {"id": -1001}}}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot", "TELEGRAM_CHAT_ID": "-1001",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
        }, clear=False), mock.patch.object(
            TelegramSender, "send_document", autospec=True,
            return_value={"message_id": 602, "filename": "human-request-long-source.md"},
        ) as send_document:
            receipt = send_human_request_telegram(self.center, "token", created, api=send_api)

        self.assertTrue(str(calls[0]["text"]).startswith("Исходное сообщение (начало):"))
        self.assertIn("разбор приложены Markdown-файлом", str(calls[0]["text"]))
        self.assertIn("Разбор:\nDeploy now?", str(calls[0]["text"]))
        self.assertEqual(602, receipt["document_message_id"])
        stored = self.center.get_human_request("token", "long-source")
        self.assertEqual(602, stored["telegram_document_message_id"])
        self.assertEqual("sent", stored["telegram_document_state"])
        args = send_document.call_args.args
        self.assertEqual("human-request-long-source.md", args[2])
        self.assertIn(original, args[3])
        self.assertIn("## Разбор и варианты действий\n\nDeploy now?", args[3])
        self.assertEqual(601, args[5])

    def test_short_original_is_visible_in_card_without_document(self) -> None:
        """Keep the complete short source visible beside the interactive controls."""
        original = "Короткий исходный текст 🧭"
        created = self.center.create_human_request(
            "token", self.request(request_id="short-source", original_text=original)
        )
        calls: list[dict[str, object]] = []

        def send_api(_method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            return {"ok": True, "result": {"message_id": 603, "chat": {"id": 42}}}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot", "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
        }, clear=False), mock.patch.object(TelegramSender, "send_document") as send_document:
            receipt = send_human_request_telegram(self.center, "token", created, api=send_api)
        self.assertIn(original, str(calls[0]["text"]))
        self.assertLess(str(calls[0]["text"]).index(original), str(calls[0]["text"]).index("Deploy now?"))
        self.assertIn("reply_markup", calls[0])
        send_document.assert_not_called()
        self.assertNotIn("document_message_id", receipt)

    def test_document_sender_builds_utf8_markdown_reply_in_same_topic(self) -> None:
        """Build sendDocument multipart with exact bytes and reply/topic linkage."""
        sender = TelegramSender("fixture-bot", "-1001")
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({"ok": True, "result": {
            "message_id": 702,
            "document": {
                "file_id": "BQACAgIAAxkBAA_fixture",
                "file_unique_id": "AgAD_fixture",
                "file_name": "human-request-source.md",
                "mime_type": "text/markdown",
                "file_size": 24,
            },
        }}).encode()
        response.__enter__.return_value = response
        with mock.patch("notification_center.http_api.urllib.request.urlopen", return_value=response) as urlopen:
            receipt = sender.send_document(
                {"chat_id": "-1001", "message_thread_id": "5764"},
                "human-request-source.md", "точный UTF-8 🧭", "Полный текст", 701,
            )
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendDocument"))
        self.assertIn("точный UTF-8 🧭".encode(), request.data)
        self.assertIn(b"Content-Type: text/markdown; charset=utf-8", request.data)
        self.assertIn(b'name="message_thread_id"\r\n\r\n5764', request.data)
        self.assertIn(b'{"message_id":701}', request.data)
        self.assertEqual(702, receipt["message_id"])
        self.assertEqual("BQACAgIAAxkBAA_fixture", receipt["file_id"])

    def test_project_topic_routes_human_request_without_changing_other_projects(self) -> None:
        """Use the durable project topic and keep the global chat as the fallback."""
        center = NotificationCenter(
            Path(self.tempdir.name) / "project-routes.sqlite3",
            {"wildcard": {"project": "*", "max_severity": "critical"}},
        )
        center.set_runtime_setting("telegram_topics_json", json.dumps({
            "userio": {
                "name": "Разбор UserIO", "chat_id": "-1004322359393",
                "message_thread_id": 5764, "enabled": True,
            },
        }))
        userio = center.create_human_request(
            "wildcard", self.request(request_id="userio-route", project="userio")
        )
        other = center.create_human_request(
            "wildcard", self.request(request_id="other-route")
        )
        calls: list[dict[str, object]] = []

        def send_api(_method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            return {"ok": True, "result": {
                "message_id": 502, "chat": {"id": payload["chat_id"]},
            }}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot", "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
        }, clear=False):
            send_human_request_telegram(center, "wildcard", userio, api=send_api)
            send_human_request_telegram(center, "wildcard", other, api=send_api)

        self.assertEqual("-1004322359393", calls[0]["chat_id"])
        self.assertEqual("5764", calls[0]["message_thread_id"])
        self.assertEqual("42", calls[1]["chat_id"])
        self.assertNotIn("message_thread_id", calls[1])

class HumanRequestHttpTests(unittest.TestCase):
    """Prove the same contract through the real loopback HTTP surface."""

    def setUp(self) -> None:
        """Start an isolated HTTP server."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"token": {"project": "hermes", "max_severity": "critical"}})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health", "mcp"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        """Stop the server and remove isolated state."""
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2); self.tempdir.cleanup()

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        """Call one authenticated JSON endpoint and preserve error payloads."""
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Authorization": "Bearer token", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_create_resolve_read_and_cancel_routes(self) -> None:
        """Expose request lifecycle without coupling it to incident routes."""
        payload = HumanRequestTests.request(self)  # type: ignore[arg-type]
        status, created = self.call("POST", "/v1/human-requests", payload)
        self.assertEqual(201, status)
        status, resolved = self.call("POST", "/v1/human-requests/deploy-1/resolve", {"actor": "telegram:42", "value": "now"})
        self.assertEqual(200, status)
        self.assertEqual("now", resolved["response_value"])
        status, read = self.call("GET", "/v1/human-requests/deploy-1")
        self.assertEqual(200, status)
        self.assertFalse(read["telegram_document_required"])
        self.assertEqual(resolved, {
            key: value for key, value in read.items() if key != "telegram_document_required"
        })

    def test_create_route_allows_four_choices_and_is_exactly_idempotent(self) -> None:
        """Keep HTTP retries safe without widening the public AskHuman MCP schema."""
        payload = HumanRequestTests.request(  # type: ignore[arg-type]
            self,
            request_id="http-idempotent",
            choices=[
                {"label": f"Option {index}", "value": f"option-{index}"}
                for index in range(4)
            ],
        )
        payload.pop("allowed_actors")
        telegram_calls: list[tuple[str, dict[str, object]]] = []

        def telegram(_token: str, method: str, request: dict[str, object]) -> dict[str, object]:
            telegram_calls.append((method, request))
            return {"ok": True, "result": {"message_id": 808, "chat": {"id": 42}}}

        from unittest.mock import patch
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot",
            "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
            "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "42",
        }, clear=False), patch("notification_center.http_api.telegram_api", side_effect=telegram):
            status, created = self.call("POST", "/v1/human-requests", payload)
            self.assertEqual(201, status)
            self.assertEqual(4, len(created["choices"]))
            self.assertFalse(created["idempotent"])
            self.assertFalse(created["telegram_document_required"])
            self.assertEqual({"status": "sent", "chat_id": "42", "message_id": 808}, created["telegram"])

            status, replayed = self.call("POST", "/v1/human-requests", payload)
            self.assertEqual(201, status)
            self.assertTrue(replayed["idempotent"])
            self.assertFalse(replayed["telegram_document_required"])
            self.assertEqual(created["telegram"], replayed["telegram"])
            self.assertEqual("42", replayed["telegram_chat_id"])
            self.assertEqual(808, replayed["telegram_message_id"])
            self.assertEqual(1, len(telegram_calls))

            status, conflict = self.call(
                "POST", "/v1/human-requests", {**payload, "message": "Different message"}
            )
        self.assertEqual(400, status)
        self.assertIn("idempotency conflict", str(conflict["error"]))

    def test_long_post_returns_refreshed_document_receipt_and_get_requirement(self) -> None:
        """Return persisted document proof on first POST and the same strict requirement on GET."""
        payload = HumanRequestTests.request(  # type: ignore[arg-type]
            self, request_id="http-long-document", original_text="полный текст\n" * 500
        )
        payload.pop("allowed_actors")

        def telegram(_token: str, _method: str, _request: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "result": {"message_id": 811, "chat": {"id": 42}}}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot", "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
            "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "42",
        }, clear=False), mock.patch(
            "notification_center.http_api.telegram_api", side_effect=telegram
        ), mock.patch.object(
            TelegramSender, "send_document",
            return_value={
                "message_id": 812,
                "filename": "human-request-http-long-document.md",
                "file_id": "BQACAgIAAxkBAA_http_fixture",
            },
        ):
            status, created = self.call("POST", "/v1/human-requests", payload)

        self.assertEqual(201, status)
        self.assertTrue(created["telegram_document_required"])
        self.assertEqual("sent", created["telegram_document_state"])
        self.assertEqual(812, created["telegram_document_message_id"])
        self.assertEqual("BQACAgIAAxkBAA_http_fixture", created["telegram_document_file_id"])
        self.assertEqual(812, created["telegram"]["document_message_id"])
        self.assertEqual("BQACAgIAAxkBAA_http_fixture", created["telegram"]["document_file_id"])
        status, read = self.call("GET", "/v1/human-requests/http-long-document")
        self.assertEqual(200, status)
        self.assertTrue(read["telegram_document_required"])
        self.assertEqual("sent", read["telegram_document_state"])
        self.assertEqual(812, read["telegram_document_message_id"])
        self.assertEqual("BQACAgIAAxkBAA_http_fixture", read["telegram_document_file_id"])

    def test_get_long_card_without_send_shows_required_and_missing_document(self) -> None:
        """Distinguish a persisted long card from a completed document delivery."""
        self.center.create_human_request(
            "token", HumanRequestTests.request(  # type: ignore[arg-type]
                self, request_id="card-only-long", original_text="сырой текст " * 500
            )
        )
        status, read = self.call("GET", "/v1/human-requests/card-only-long")
        self.assertEqual(200, status)
        self.assertTrue(read["telegram_document_required"])
        self.assertIsNone(read["telegram_document_state"])
        self.assertIsNone(read["telegram_document_message_id"])

    def test_unknown_document_outcome_is_not_retried_or_reported_complete(self) -> None:
        """Expose a missing document receipt after a failed send without risking a duplicate."""
        payload = HumanRequestTests.request(  # type: ignore[arg-type]
            self, request_id="unknown-document", original_text="длинный текст " * 500
        )
        payload.pop("allowed_actors")
        telegram_calls: list[str] = []

        def telegram(_token: str, method: str, _request: dict[str, object]) -> dict[str, object]:
            telegram_calls.append(method)
            return {"ok": True, "result": {"message_id": 901, "chat": {"id": 42}}}

        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fixture-bot", "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CALLBACK_SECRET": "x" * 32,
            "TELEGRAM_CALLBACK_ALLOWED_USER_IDS": "42",
        }, clear=False), mock.patch(
            "notification_center.http_api.telegram_api", side_effect=telegram
        ), mock.patch.object(
            TelegramSender, "send_document", side_effect=TimeoutError("outcome unknown")
        ) as send_document:
            status, failed = self.call("POST", "/v1/human-requests", payload)
            self.assertEqual(500, status)
            self.assertEqual("internal error", failed["error"])
            status, replayed = self.call("POST", "/v1/human-requests", payload)

        self.assertEqual(201, status)
        self.assertEqual("uncertain", replayed["telegram"]["status"])
        self.assertIsNone(replayed["telegram_document_message_id"])
        self.assertEqual("uncertain", replayed["telegram_document_state"])
        self.assertEqual(["sendMessage"], telegram_calls)
        self.assertEqual(1, send_document.call_count)
        status, _resolved = self.call(
            "POST", "/v1/human-requests/unknown-document/resolve",
            {"actor": "telegram:42", "value": "now"},
        )
        self.assertEqual(200, status)
        status, read = self.call("GET", "/v1/human-requests/unknown-document")
        self.assertEqual(200, status)
        self.assertEqual("resolved", read["state"])
        self.assertTrue(read["telegram_document_required"])
        self.assertEqual("uncertain", read["telegram_document_state"])
        self.assertIsNone(read["telegram_document_message_id"])

    def test_ask_human_tool_returns_selected_button_value(self) -> None:
        """Use the actual MCP handler over HTTP and return the chosen value."""
        import os
        from unittest.mock import patch

        def answer() -> None:
            while True:
                with self.center._lock:
                    row = self.center._connection.execute("SELECT request_id FROM human_requests LIMIT 1").fetchone()
                if row:
                    self.center.resolve_human_request("token", str(row["request_id"]), "telegram:42", "later")
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        env = {
            "NOTIFY_CENTER_TOKEN": "token", "NOTIFY_CENTER_EVENT_URL": self.url + "/v1/events",
            "NOTIFY_CENTER_PROJECT": "hermes", "NOTIFY_CENTER_RECIPIENT": "operator",
            "ASK_HUMAN_URL": self.url + "/v1/human-requests", "ASK_HUMAN_ALLOWED_ACTORS": "telegram:42",
        }
        with patch.dict(os.environ, env, clear=False):
            result = tool_ask_human({"message": "Deploy?", "choices": [{"label": "Now", "value": "now"}, {"label": "Later", "value": "later"}], "wait_seconds": 2})
        thread.join(timeout=2)
        self.assertEqual("resolved", result["status"])
        self.assertEqual("later", result["value"])

    def test_ask_human_uses_central_service_token_without_user_env_file(self) -> None:
        """Use the running center's scoped token instead of a second MCP credential file."""
        import os
        from unittest.mock import patch

        environment = {
            "NOTIFY_CENTER_TOKENS_JSON": json.dumps({"token": {"project": "hermes", "max_severity": "critical"}}),
            "NOTIFY_CENTER_PORT": str(self.server.server_port),
            "NOTIFY_CENTER_PROJECT": "hermes",
            "ASK_HUMAN_ALLOWED_ACTORS": "telegram:42",
        }
        cleared = {"NOTIFY_CENTER_TOKEN": "", "NOTIFY_CENTER_EVENT_URL": "", "NOTIFY_SECRETS_FILE": "/missing"}
        with patch.dict(os.environ, {**environment, **cleared}, clear=False):
            result = tool_ask_human({"message": "Central config?", "choices": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}], "wait_seconds": 0})
        self.assertEqual("pending", result["status"])

    def test_ask_human_is_the_first_canonical_agent_tool(self) -> None:
        """Make the preferred human interaction obvious during MCP discovery."""
        tools = tool_specs()
        self.assertEqual("ask_human", tools[0]["name"])
        schema = tools[0]["inputSchema"]
        self.assertEqual(2, schema["properties"]["choices"]["minItems"])
        self.assertEqual(3, schema["properties"]["choices"]["maxItems"])


if __name__ == "__main__":
    unittest.main()
