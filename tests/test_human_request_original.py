"""Focused tests for HumanRequest original-message rendering."""

from __future__ import annotations

import unittest

from notification_center.human_request_original import (
    render_human_request_original,
    requires_document,
    truncate_utf16,
    utf16_length,
)


class HumanRequestOriginalTests(unittest.TestCase):
    def test_utf16_helpers_keep_emoji_boundary(self) -> None:
        self.assertEqual(4, utf16_length("A🧭B"))
        self.assertEqual("A", truncate_utf16("A🧭B", 2))
        self.assertEqual("A🧭", truncate_utf16("A🧭B", 3))

    def test_short_original_is_verbatim_inline(self) -> None:
        original = "  # Заголовок\n\n*важно* 🧭\n  "
        card, document = render_human_request_original({
            "message": "Выберите действие",
            "original_text": original,
            "state": "pending",
        })
        self.assertEqual(
            f"Исходное сообщение:\n{original}\n\nРазбор:\nВыберите действие",
            card,
        )
        self.assertIsNone(document)

    def test_long_original_gets_bounded_preview_and_complete_markdown(self) -> None:
        original = "  # Пользовательский Markdown\n\n```python\nprint('точно')\n```\n" + ("Строка `кода` 🧭\n" * 500) + "  "
        message = "AI-карточка\n\n1. Первый вариант\n2. Второй вариант"
        card, document = render_human_request_original({
            "message": message,
            "original_text": original,
            "state": "pending",
        })
        self.assertTrue(requires_document(message, original))
        self.assertLessEqual(utf16_length(card), 4096)
        self.assertIn("📎 Полное исходное сообщение и разбор", card)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(
            "# Полное уведомление\n\n"
            "## Исходное сообщение\n\n"
            f"{original}\n\n"
            "## Разбор и варианты действий\n\n"
            f"{message}",
            document,
        )
        self.assertIn(original, document)

    def test_resolution_uses_choice_label_without_raw_value(self) -> None:
        request = {
            "message": "Как продолжить?",
            "original_text": "Запрос пользователя",
            "choices": [
                {"label": "Продолжить сейчас", "value": "opaque-choice-17"},
                {"label": "Отложить", "value": "opaque-choice-29"},
            ],
            "response_value": "opaque-choice-17",
            "state": "resolved",
        }
        card, document = render_human_request_original(request)
        self.assertIsNone(document)
        self.assertIn("✅ Выбрано: Продолжить сейчас", card)
        self.assertNotIn("opaque-choice-17", card)

    def test_terminal_states_are_honest_and_pending_is_unchanged(self) -> None:
        pending, _ = render_human_request_original({"message": "Карточка", "state": "pending"})
        expired, _ = render_human_request_original({"message": "Карточка", "state": "expired"})
        cancelled, _ = render_human_request_original({"message": "Карточка", "state": "cancelled"})
        resolved, _ = render_human_request_original({
            "message": "Карточка", "state": "resolved", "response_value": "internal-id"
        })
        self.assertEqual("Карточка", pending)
        self.assertEqual("Карточка\n\n⌛ Срок ответа истёк.", expired)
        self.assertEqual("Карточка\n\n🚫 Запрос отменён.", cancelled)
        self.assertEqual("Карточка\n\n✅ Ответ получен.", resolved)
        self.assertNotIn("internal-id", resolved)

    def test_missing_original_keeps_only_a_bounded_card(self) -> None:
        message = "🧭" * 3000
        card, document = render_human_request_original({"message": message, "original_text": None})
        self.assertLessEqual(utf16_length(card), 4096)
        self.assertIsNone(document)
        self.assertNotIn("Исходное сообщение", card)

    def test_long_document_carries_resolution_label_in_analysis_section(self) -> None:
        original = "текст\n" * 1000
        card, document = render_human_request_original({
            "message": "Разбор",
            "original_text": original,
            "state": "resolved",
            "response_value": "yes-id",
            "choices": [{"label": "Да, выполнить", "value": "yes-id"}],
        })
        self.assertLessEqual(utf16_length(card), 4096)
        self.assertIn("✅ Выбрано: Да, выполнить", card)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("## Исходное сообщение\n\n" + original, document)
        self.assertIn("## Разбор и варианты действий\n\nРазбор", document)
        self.assertTrue(document.endswith("✅ Выбрано: Да, выполнить"))
        self.assertNotIn("yes-id", card)
        self.assertNotIn("yes-id", document)

    def test_resolution_crossing_inline_boundary_moves_full_original_to_document(self) -> None:
        message = "AI"
        framing = f"Исходное сообщение:\n\n\nРазбор:\n{message}"
        original = "x" * (4096 - utf16_length(framing))
        self.assertFalse(requires_document(message, original))
        self.assertEqual(4096, utf16_length(
            f"Исходное сообщение:\n{original}\n\nРазбор:\n{message}"
        ))

        card, document = render_human_request_original({
            "message": message,
            "original_text": original,
            "state": "resolved",
            "response_value": "go",
            "choices": [{"label": "Продолжить", "value": "go"}],
        })

        self.assertLessEqual(utf16_length(card), 4096)
        self.assertIn("✅ Выбрано: Продолжить", card)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("## Исходное сообщение\n\n" + original, document)

    def test_pending_choice_reserves_longest_label_and_keeps_document_after_resolution(self) -> None:
        message = "AI"
        choices = [
            {"label": "Да", "value": "yes"},
            {"label": "Продолжить выполнение сейчас", "value": "continue"},
        ]
        framing = f"Исходное сообщение:\n\n\nРазбор:\n{message}"
        original = "x" * (4096 - utf16_length(framing))
        pending = {
            "message": message,
            "original_text": original,
            "mode": "choice",
            "choices": choices,
            "state": "pending",
        }

        pending_card, pending_document = render_human_request_original(pending)
        self.assertIsNotNone(pending_document)
        self.assertNotIn("✅ Выбрано:", pending_card)
        self.assertLessEqual(utf16_length(pending_card), 4096)
        assert pending_document is not None
        self.assertIn("## Исходное сообщение\n\n" + original, pending_document)

        resolved_card, resolved_document = render_human_request_original({
            **pending,
            "state": "resolved",
            "response_value": "continue",
        })
        self.assertIsNotNone(resolved_document)
        self.assertIn("✅ Выбрано: Продолжить выполнение сейчас", resolved_card)
        self.assertLessEqual(utf16_length(resolved_card), 4096)


if __name__ == "__main__":
    unittest.main()
