"""Real binding proof: the ready MCP tools create, deliver, answer, and resume.

Only the two outbound networks are replaced: the Telegram Bot API and the
Agent Herder loopback. The Notice Place HTTP API, the durable store, and the MCP
tool handlers are the real ones, so a passing run means the installed binding
really issues a card and really continues the bound native session.
"""

from __future__ import annotations

import json
import os
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
        payload = json.dumps({
            "request_id": body.get("request_id"), "status": "resuming", "continuation": "resume",
        }).encode()
        self.send_response(202)
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

        def fake_telegram(token: str, method: str, payload: dict) -> dict:
            self.telegram.append((method, dict(payload)))
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