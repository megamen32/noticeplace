"""Legacy card recovery must preserve exact decisions and avoid duplicate sends."""
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError
from notification_center.http_api import TelegramSender, build_handler, refresh_human_request_original
from notification_center.telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


class OriginalBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tmp.name)/"db", {
            "owner": {"project": "userio", "max_severity": "critical"},
            "other": {"project": "other", "max_severity": "critical"},
        })
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN":"test", "TELEGRAM_CALLBACK_SECRET":"x"*32})
        self.env.start()
        self.center.create_human_request("owner", {
            "schema":"ask_human.request.v1", "request_id":"userio-1", "project":"userio",
            "recipient":"owner", "mode":"choice", "message":"Разбор сообщения",
            "choices":[{"label":"Отправить 1","value":"send_1"},{"label":"Неважно","value":"ignore"}],
            "allowed_actors":["telegram:42"], "expires_at": None,
        })
        self.center.bind_human_request_message("owner", "userio-1", "-1001", 501)
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def api(self, method, payload):
        self.calls.append((method, payload))
        return {"ok":True,"result":{"message_id":501,"chat":{"id":-1001},"message_thread_id":5764}}

    def test_pending_card_is_edited_with_exact_original_and_same_signed_choices(self):
        result = refresh_human_request_original(self.center,"owner","userio-1","  # Оригинал 🧭\n",api=self.api)
        self.assertEqual("pending", result["state"])
        self.assertEqual(501,result["telegram_message_id"])
        self.assertEqual("editMessageText",self.calls[0][0])
        self.assertIn("  # Оригинал 🧭\n",self.calls[0][1]["text"])
        keyboard=json.loads(self.calls[0][1]["reply_markup"])
        codec=TelegramActionCodec("x"*32)
        self.assertEqual("1",codec.decode(keyboard["inline_keyboard"][1][0]["callback_data"])[2])

    def test_resolved_choice_stays_resolved_and_is_not_reopened(self):
        before=self.center.resolve_human_request("owner","userio-1","telegram:42","send_1")
        result=refresh_human_request_original(self.center,"owner","userio-1","Полный оригинал",api=self.api)
        for key in ("response_actor","response_value","resolved_at","expires_at","allowed_actors","choices","recipient"):
            self.assertEqual(before[key],result[key])
        self.assertIn("✅ Выбрано: Отправить 1",self.calls[0][1]["text"])
        self.assertEqual([],json.loads(self.calls[0][1]["reply_markup"])["inline_keyboard"])

    def test_source_binding_is_immutable_and_project_scoped(self):
        self.center.enrich_human_request_original("owner","userio-1","Первый оригинал")
        with self.assertRaises(ValidationError):
            self.center.enrich_human_request_original("owner","userio-1","Подмена")
        with self.assertRaises(AuthorizationError):
            self.center.enrich_human_request_original("other","userio-1","Первый оригинал")

    def test_long_original_and_analysis_are_one_document_and_replay_sends_no_second_file(self):
        original="# Источник\n"+"строка\n"*700
        with mock.patch.object(TelegramSender,"send_document",return_value={"message_id":502,"file_id":"fixture"}) as send:
            result=refresh_human_request_original(self.center,"owner","userio-1",original,api=self.api)
            refresh_human_request_original(self.center,"owner","userio-1",original,api=self.api)
        self.assertEqual(1,send.call_count)
        self.assertIn(original,send.call_args.args[2])
        self.assertIn("## Разбор и варианты действий\n\nРазбор сообщения",send.call_args.args[2])
        self.assertEqual("5764",send.call_args.args[0]["message_thread_id"])
        self.assertEqual(502,result["telegram_document_message_id"])

    def test_unknown_document_send_stays_unknown_and_is_not_retried(self):
        original="текст\n"*900
        with mock.patch.object(TelegramSender,"send_document",side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError):
                refresh_human_request_original(self.center,"owner","userio-1",original,api=self.api)
            with self.assertRaises(ValidationError):
                refresh_human_request_original(self.center,"owner","userio-1",original,api=self.api)
        self.assertEqual(1,send.call_count)
        self.assertEqual("uncertain",self.center.get_human_request("owner","userio-1")["telegram_document_state"])

    def test_stale_callback_snapshot_keeps_fresh_backfilled_original(self):
        self.center.enrich_human_request_original("owner","userio-1","Новый полный оригинал")
        codec=TelegramActionCodec("x"*32)
        update={"update_id":20,"callback_query":{"id":"choose","from":{"id":42},
                "data":codec.encode("human_choice","userio-1",choice_id="1"),
                "message":{"message_id":501,"chat":{"id":-1001},"text":"Старый пересказ"}}}
        def poll(method,payload):
            if method=="getUpdates":return {"ok":True,"result":[update]}
            return self.api(method,payload)
        TelegramInteractionPoller(self.center,"test",{"42"},codec,api=poll).poll_once()
        edit=next(payload for method,payload in self.calls if method=="editMessageText")
        self.assertIn("Новый полный оригинал",edit["text"])
        self.assertIn("✅ Выбрано: Неважно",edit["text"])

    def test_atomic_document_reservation_has_only_one_winner(self):
        self.assertTrue(self.center.reserve_human_request_document("owner","userio-1"))
        with self.assertRaises(ValidationError):
            self.center.reserve_human_request_document("owner","userio-1")

    def test_native_not_modified_response_is_an_idempotent_edit(self):
        self.center.enrich_human_request_original("owner","userio-1","Оригинал")
        body={"ok":False,"error_code":400,"description":"Bad Request: message is not modified: specified new message content is exactly the same"}
        error=urllib.error.HTTPError("https://api.telegram.org/fixture",400,"Bad Request",{},io.BytesIO(json.dumps(body).encode()))
        with mock.patch("notification_center.telegram_interactions.urllib.request.urlopen",side_effect=error):
            result=refresh_human_request_original(self.center,"owner","userio-1","Оригинал")
        self.assertEqual(501,result["telegram_message_id"])

    def test_edit_error_envelope_with_matching_ids_is_not_a_receipt(self):
        def failed(_method,_payload):
            return {"ok":False,"result":{"message_id":501,"chat":{"id":-1001}}}
        with self.assertRaises(RuntimeError):
            refresh_human_request_original(self.center,"owner","userio-1","Оригинал",api=failed)

    def test_actual_http_original_route_updates_same_resolved_request(self):
        self.center.resolve_human_request("owner","userio-1","telegram:42","send_1")
        server=ThreadingHTTPServer(("127.0.0.1",0),build_handler(self.center,"health","mcp"))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f"http://127.0.0.1:{server.server_port}/v1/human-requests/userio-1/original"
            request=urllib.request.Request(url,data=json.dumps({"original_text":"HTTP оригинал"}).encode(),headers={"Authorization":"Bearer owner","Content-Type":"application/json"})
            with mock.patch("notification_center.http_api.telegram_api",side_effect=lambda _token,method,payload:self.api(method,payload)):
                with urllib.request.urlopen(request,timeout=2) as response:result=json.loads(response.read())
            self.assertEqual("resolved",result["state"])
            self.assertEqual("send_1",result["response_value"])
            self.assertEqual(501,result["telegram_message_id"])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_missing_original_topic_never_guesses_current_route(self):
        self.center.set_runtime_setting("telegram_topics_json",json.dumps({"userio":{"chat_id":"-1001","message_thread_id":9999}}))
        def edited_without_thread(_method,_payload):
            return {"ok":True,"result":{"message_id":501,"chat":{"id":-1001,"type":"supergroup","is_forum":True}}}
        with mock.patch.object(TelegramSender,"send_document",return_value={"message_id":502}) as send:
            with self.assertRaisesRegex(ValidationError,"topic"):
                refresh_human_request_original(self.center,"owner","userio-1","текст\n"*900,api=edited_without_thread)
        send.assert_not_called()
        self.assertIsNone(self.center.get_human_request("owner","userio-1")["telegram_document_state"])


if __name__=="__main__":unittest.main()
