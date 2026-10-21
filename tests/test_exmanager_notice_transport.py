"""Contract tests for the authenticated ExManager same-client Notice API."""

from __future__ import annotations

import json
import unittest
from typing import Any

from notification_center.exmanager_notice_transport import (
    BASE_URL,
    CHAT_ID,
    ExManagerNoticeTransport,
    ExManagerNoticeTransportError,
    render_task_question,
)


class _Response:
    status = 200

    def __init__(self, body: dict[str, Any]):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class ExManagerNoticeTransportTests(unittest.TestCase):
    def test_fixed_readiness_submit_and_query_contract(self) -> None:
        responses = [
            {"ok": True, "accountId": "8810909089", "username": "@deus_excode",
             "peerId": CHAT_ID, "peerAddressable": True},
            {"ok": True, "outcome": "accepted", "senderId": "8810909089",
             "chatId": CHAT_ID, "messageId": 601, "acceptedAt": 1000},
            {"ok": True, "outcome": "accepted", "senderId": "8810909089",
             "chatId": CHAT_ID, "messageId": 601, "acceptedAt": 1000},
        ]
        seen = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return _Response(responses.pop(0))

        transport = ExManagerNoticeTransport(BASE_URL, "test-capability", opener=opener)
        readiness = transport.readiness()
        submitted = transport.submit_question("td-abcdef0123456789", "notice-question:td-abcdef0123456789", "Question")
        queried = transport.query_question("td-abcdef0123456789", "notice-question:td-abcdef0123456789")

        self.assertEqual("@deus_excode", readiness["username"])
        self.assertEqual(submitted, queried)
        self.assertEqual("accepted", submitted["outcome"])
        self.assertEqual(601, submitted["message_id"])
        self.assertEqual(1000.0, submitted["accepted_at"])
        self.assertEqual("GET", seen[0][0].get_method())
        self.assertTrue(seen[0][0].full_url.endswith("/internal/notice/readiness"))
        self.assertEqual("POST", seen[1][0].get_method())
        self.assertEqual("GET", seen[2][0].get_method())
        self.assertIn("/internal/notice/questions/td-abcdef0123456789/", seen[2][0].full_url)
        for request, _timeout in seen:
            self.assertEqual("Bearer test-capability", request.get_header("Authorization"))
        self.assertEqual({"requestId": "td-abcdef0123456789",
                          "operationId": "notice-question:td-abcdef0123456789",
                          "chatId": CHAT_ID, "text": "Question"},
                         json.loads(seen[1][0].data))

    def test_rejects_wrong_ready_peer_and_untrusted_receipt_identity(self) -> None:
        wrong_ready = ExManagerNoticeTransport(
            BASE_URL, "capability",
            opener=lambda _request, timeout: _Response({
                "ok": True, "accountId": "8810909089", "username": "@deus_excode",
                "peerId": "-100other", "peerAddressable": True,
            }),
        )
        with self.assertRaises(ExManagerNoticeTransportError):
            wrong_ready.readiness()

        wrong_receipt = ExManagerNoticeTransport(
            BASE_URL, "capability",
            opener=lambda _request, timeout: _Response({
                "ok": True, "outcome": "accepted", "senderId": "other",
                "chatId": CHAT_ID, "messageId": 601, "acceptedAt": 1000.0,
            }),
        )
        with self.assertRaises(ExManagerNoticeTransportError):
            wrong_receipt.submit_question("td-abcdef0123456789", "notice-question:td-abcdef0123456789", "Question")

    def test_rejects_unapproved_endpoint_and_renders_every_exact_choice_value(self) -> None:
        with self.assertRaises(ExManagerNoticeTransportError):
            ExManagerNoticeTransport("http://192.168.2.100:30193", "capability")
        with self.assertRaises(ExManagerNoticeTransportError):
            ExManagerNoticeTransport("http://192.168.128.1:30193", "capability")
        rendered = render_task_question({
            "message": "Choose the approved action.",
            "choices": [{"value": "continue", "label": "Continue"},
                        {"value": "stop", "label": "Stop"}],
        })
        self.assertIn("continue — Continue", rendered)
        self.assertIn("stop — Stop", rendered)

    def test_sending_query_is_normalized_to_unknown_and_missing_timestamp_is_rejected(self) -> None:
        pending = ExManagerNoticeTransport(
            BASE_URL, "capability",
            opener=lambda _request, timeout: _Response({"ok": True, "outcome": "sending"}),
        )
        self.assertEqual({"outcome": "unknown"}, pending.query_question("td-abcdef0123456789", "operation-12345678"))

        missing_time = ExManagerNoticeTransport(
            BASE_URL, "capability",
            opener=lambda _request, timeout: _Response({
                "ok": True, "outcome": "accepted", "senderId": "8810909089",
                "chatId": CHAT_ID, "messageId": 602,
            }),
        )
        with self.assertRaises(ExManagerNoticeTransportError):
            missing_time.query_question("td-abcdef0123456789", "operation-12345678")

        fractional_time = ExManagerNoticeTransport(
            BASE_URL, "capability",
            opener=lambda _request, timeout: _Response({
                "ok": True, "outcome": "accepted", "senderId": "8810909089",
                "chatId": CHAT_ID, "messageId": 603, "acceptedAt": 1000.5,
            }),
        )
        with self.assertRaises(ExManagerNoticeTransportError):
            fractional_time.query_question("td-abcdef0123456789", "operation-12345678")


if __name__ == "__main__":
    unittest.main()
