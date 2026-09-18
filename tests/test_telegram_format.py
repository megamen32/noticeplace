import unittest
from urllib.parse import parse_qs
from unittest.mock import MagicMock, patch

from notification_center.http_api import TelegramSender
from notification_center.telegram_format import telegram_html


class TelegramFormatTests(unittest.TestCase):
    def test_report_sent_with_formatting_and_mobile_table(self):
        body = "## GPU-воркер\n\n| Замер | Значение |\n|---|---|\n| p50 | **222 мс** |\n| p95 | 413 мс |\n\n1. **Модель** — `~/captcha_ml`\nA < B & C"
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"ok":true,"result":{"message_id":1}}'
        with patch("urllib.request.urlopen", return_value=response) as send:
            TelegramSender("token", "1").send({"incident": {"id": "test", "project": "hermes", "severity": "notice", "title": "Report", "body": body}})
        request = parse_qs(send.call_args.args[0].data.decode())
        self.assertEqual(request.get("parse_mode"), ["HTML"])
        text = request["text"][0]
        self.assertIn("<b>GPU-воркер</b>", text)
        self.assertIn("• p50 — <b>222 мс</b>", text)
        self.assertIn("<code>~/captcha_ml</code>", text)
        self.assertIn("A &lt; B &amp; C", text)
        self.assertNotIn("|---|", text)

    def test_code_html_links_and_plain_identifiers(self):
        text = telegram_html('video_watching\n```sh\n**literal** <tag>\n```\n[link](https://example.com/?a=1&b=2)\n<b>literal</b>')
        self.assertIn("video_watching", text)
        self.assertIn("<pre>**literal** &lt;tag&gt;</pre>", text)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', text)
        self.assertIn("&lt;b&gt;literal&lt;/b&gt;", text)

    def test_unclosed_code_and_multicolumn_table(self):
        self.assertEqual(telegram_html("```\nx < y"), "<pre>x &lt; y</pre>")
        text = telegram_html("Name | p50 | p95\n--- | --- | ---\nGPU | 222 | 413")
        self.assertIn("• GPU — p50: 222 — p95: 413", text)
