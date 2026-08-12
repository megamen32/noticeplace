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
from notification_center.http_api import build_handler, send_human_request_telegram
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
        """Accept three modes while enforcing the bounded choice and actor contracts."""
        created = self.center.create_human_request("token", self.request())
        self.assertEqual("pending", created["state"])
        self.assertEqual("choice", created["mode"])
        for mode in ("notify", "question"):
            result = self.center.create_human_request("token", self.request(request_id=f"mode-{mode}", mode=mode, choices=None))
            self.assertEqual(mode, result["mode"])
        for choices in ([{"label": "One", "value": "one"}], self.request()["choices"] * 2):
            with self.assertRaisesRegex(ValidationError, "two or three"):
                self.center.create_human_request("token", self.request(request_id=f"bad-{len(choices)}", choices=choices))
        with self.assertRaisesRegex(ValidationError, "not allowed"):
            self.center.resolve_human_request("token", "deploy-1", "telegram:7", "now")

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
        def resolve(value: str) -> str:
            try:
                return str(other.resolve_human_request("token", "deploy-1", "telegram:42", value)["response_value"])
            except ValidationError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: pair[0].resolve_human_request("token", "deploy-1", "telegram:42", pair[1])["response_value"] if pair[0] is self.center else resolve(pair[1]), [(self.center, "now"), (other, "later")]))
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
        self.assertEqual(resolved, read)

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

    def test_ask_human_is_the_first_canonical_agent_tool(self) -> None:
        """Make the preferred human interaction obvious during MCP discovery."""
        tools = tool_specs()
        self.assertEqual("ask_human", tools[0]["name"])
        schema = tools[0]["inputSchema"]
        self.assertEqual(2, schema["properties"]["choices"]["minItems"])
        self.assertEqual(3, schema["properties"]["choices"]["maxItems"])


if __name__ == "__main__":
    unittest.main()
