"""A verified Matrix room answer completes the incident's own human question.

The isolated test-room path is one answer surface: when the declared matrix
sender answers the notice of an incident that carries a pending free-text
human question, the same answer completes that question and the owed native
resume follows the canonical completion path. One answer event is consumed
once, so a repeat never resumes the native session twice.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter
from notification_center.http_api import build_handler

ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
BOT_B = "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"
CAPABILITY = "transport-capability"


class MatrixReplyQuestionTests(unittest.TestCase):
    """Run real loopback requests against one isolated in-memory-shaped store."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(
            Path(self.tempdir.name) / "notify.sqlite3",
            {"secret-token": {"project": "hermes", "max_severity": "notice"}},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.center, "health-token", "mcp-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.tempdir.cleanup()

    def request(self, method: str, path: str, body: dict[str, object] | None = None, **headers: str) -> tuple[int, dict[str, object]]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def _notice_with_question(
        self, *, mode: str = "question", actors: list[str] | None = None,
        choices: list[dict[str, str]] | None = None, native: bool = True,
    ) -> tuple[dict, str, str]:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix question",
            policy=[
                {
                    "id": "step-1",
                    "platform": "matrix",
                    "action": "message",
                    "target": {
                        "room_id": ROOM,
                        "participants": [BOT_B],
                        "senders": [BOT_B],
                        "target": BOT_B,
                    },
                    "retry_interval_seconds": 60,
                    "max_repeats": 1,
                },
            ],
        )
        created = self.center.create_event(
            consumer["intake_token"],
            "notice-question",
            {
                "schema": "notify.event.v1",
                "project": "hermes",
                "recipient": "operator",
                "kind": "incident",
                "severity": "critical",
                "title": "Секретарь ждёт ответа",
                "dedup_key": "notice-question",
            },
        )
        incident_id = created["incident_id"]
        row = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id=? ORDER BY created_at LIMIT 1", (incident_id,)
        ).fetchone()
        self.center.complete_delivery(
            row["id"], "sent",
            result={"event_id": "$notice-question", "room_id": ROOM},
        )
        request_id = f"q-{incident_id}"
        body: dict[str, object] = {
            "schema": "ask_human.request.v1",
            "request_id": request_id,
            "project": "hermes",
            "recipient": "operator",
            "mode": mode,
            "message": "Вопрос секретаря: подтверждаете получение уведомления?",
            "allowed_actors": actors if actors is not None else [f"matrix:{BOT_B}"],
            "kind": "user",
            "incident_id": incident_id,
        }
        if choices is not None:
            body["choices"] = choices
        if native:
            body["native_target"] = {"harness": "opencode", "session_id": "ses_executor", "cwd": "/srv/app"}
        self.center.create_human_request(consumer["intake_token"], body)
        return consumer, incident_id, request_id

    def _reply(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        with mock.patch.dict(os.environ, {"NOTIFY_MATRIX_REPLY_TOKEN": CAPABILITY}):
            return self.request("POST", "/v1/matrix/reply", payload, Authorization=f"Bearer {CAPABILITY}")

    def _reply_payload(self, reply_event_id: str, answer: str = "да, подтверждаю") -> dict[str, object]:
        return {
            "reply_event_id": reply_event_id,
            "reply_to_event_id": "$notice-question",
            "sender": BOT_B,
            "room_id": ROOM,
            "answer": answer,
        }

    def test_room_answer_completes_the_bound_question_and_resumes_the_session(self) -> None:
        consumer, incident_id, request_id = self._notice_with_question()
        with mock.patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            resume.side_effect = lambda rid, harness, session, cwd, result_ref, runner: {"status": "resuming"}
            status, body = self._reply(self._reply_payload("$reply-1"))
        self.assertEqual(200, status)
        self.assertTrue(body["bound"])
        self.assertEqual(incident_id, body["incident_id"])
        question = body["question"]
        self.assertEqual(request_id, question["request_id"])
        self.assertEqual("accepted", question["answer_status"])
        self.assertEqual("resuming", question["native"]["status"])
        resolved = self.center.get_human_request(consumer["intake_token"], request_id)
        self.assertEqual("resolved", resolved["state"])
        self.assertEqual("да, подтверждаю", resolved["response_value"])
        self.assertEqual(f"matrix:{BOT_B}", resolved["response_actor"])
        resume.assert_called_once()
        self.assertEqual(request_id, resume.call_args[0][0])
        self.assertEqual("opencode", resume.call_args[0][1])
        self.assertEqual("ses_executor", resume.call_args[0][2])

    def test_foreign_sender_binds_the_incident_but_never_completes_the_question(self) -> None:
        consumer, incident_id, request_id = self._notice_with_question(actors=["matrix:@someone-else"])
        with mock.patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            status, body = self._reply(self._reply_payload("$reply-2"))
        self.assertEqual(200, status)
        self.assertTrue(body["bound"])
        self.assertEqual("sender_not_allowed", body["question"]["status"])
        pending = self.center.get_human_request(consumer["intake_token"], request_id)
        self.assertEqual("pending", pending["state"])
        resume.assert_not_called()

    def test_choice_question_stays_for_a_surface_that_names_one_option(self) -> None:
        consumer, incident_id, request_id = self._notice_with_question(
            mode="choice",
            choices=[
                {"label": "Да", "value": "yes"},
                {"label": "Нет", "value": "no"},
            ],
        )
        with mock.patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            status, body = self._reply(self._reply_payload("$reply-3"))
        self.assertEqual(200, status)
        self.assertTrue(body["bound"])
        self.assertEqual("skipped_unsupported_mode", body["question"]["status"])
        pending = self.center.get_human_request(consumer["intake_token"], request_id)
        self.assertEqual("pending", pending["state"])
        resume.assert_not_called()

    def test_repeat_reply_event_never_resumes_twice(self) -> None:
        consumer, incident_id, request_id = self._notice_with_question()
        with mock.patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            resume.side_effect = lambda rid, harness, session, cwd, result_ref, runner: {"status": "resuming"}
            status, body = self._reply(self._reply_payload("$reply-4"))
            self.assertEqual(200, status)
            self.assertTrue(body["bound"])
            self.assertEqual("accepted", body["question"]["answer_status"])
            status, body = self._reply(self._reply_payload("$reply-4", "поздний другой ответ"))
        self.assertEqual(200, status)
        self.assertFalse(body["bound"])
        self.assertTrue(body.get("idempotent"))
        self.assertIsNone(body["question"])
        resolved = self.center.get_human_request(consumer["intake_token"], request_id)
        self.assertEqual("да, подтверждаю", resolved["response_value"])
        resume.assert_called_once()

    def test_incident_without_question_returns_no_question_section(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix no question",
            policy=[
                {
                    "id": "step-1",
                    "platform": "matrix",
                    "action": "message",
                    "target": {"room_id": ROOM, "participants": [BOT_B], "senders": [BOT_B], "target": BOT_B},
                    "retry_interval_seconds": 60,
                    "max_repeats": 1,
                },
            ],
        )
        created = self.center.create_event(
            consumer["intake_token"], "notice-plain",
            {
                "schema": "notify.event.v1", "project": "hermes", "recipient": "operator",
                "kind": "incident", "severity": "critical", "title": "Без вопроса",
                "dedup_key": "notice-plain",
            },
        )
        row = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id=? ORDER BY created_at LIMIT 1",
            (created["incident_id"],),
        ).fetchone()
        self.center.complete_delivery(row["id"], "sent", result={"event_id": "$notice-plain", "room_id": ROOM})
        with mock.patch("notification_center.agent_job_helper.resume_bound_native_session") as resume:
            status, body = self._reply({
                "reply_event_id": "$reply-5",
                "reply_to_event_id": "$notice-plain",
                "sender": BOT_B,
                "room_id": ROOM,
                "answer": "подтверждаю",
            })
        self.assertEqual(200, status)
        self.assertTrue(body["bound"])
        self.assertIsNone(body["question"])
        resume.assert_not_called()


if __name__ == "__main__":
    unittest.main()
