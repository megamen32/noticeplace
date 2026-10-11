"""Telegram inline controls handled inside the NoticePlace process."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .core import NotificationCenter, ValidationError
from .health_workflow import TelegramHealthPlanCodec, health_plan_keyboard
from .selected_card import apply_selected_card, apply_selection_receipt, canonical_choice_ref


class TelegramActionCodec:
    """Sign compact callback data so an incident action cannot be forged."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Telegram callback secret must be at least 16 characters")
        self._secret = secret.encode()

    @property
    def secret(self) -> str:
        """Expose the callback secret for same-process helper codecs."""
        return self._secret.decode()

    def encode(self, action: str, incident_id: str, plan_id: str | None = None, choice_id: str | None = None) -> str:
        if action == "health_plan":
            if not plan_id or choice_id is not None:
                raise ValueError("health_plan callback requires a plan_id")
            unsigned = f"n:{action}:{incident_id}:{plan_id}"
        elif action in {"choice", "human_choice"}:
            if plan_id is not None or not choice_id or ":" in choice_id:
                raise ValueError("choice callback requires a safe choice_id")
            unsigned = f"n:{action}:{incident_id}:{choice_id}"
        else:
            if plan_id is not None or choice_id is not None:
                raise ValueError("only health_plan and choice callbacks may carry a value")
            unsigned = f"n:{action}:{incident_id}"
        signature = hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()[:12]
        encoded = f"{unsigned}:{signature}"
        if len(encoded.encode()) > 64:
            raise ValueError("Telegram callback data exceeds 64 bytes")
        return encoded

    def human_choice_selector(self, request_id: str) -> str:
        """Shorten a request identity for the 64-byte Telegram callback budget."""
        unsigned = f"human_choice:{request_id}"
        return hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()[:12]

    def encode_human_choice(self, request_id: str, index: str) -> str:
        """Sign a human-choice button by a fixed-width selector.

        A Herder request id is a UUID, which never fits beside an incident id
        in 64 bytes. The signature still covers the full identity, so a
        selector cannot be forged into another request's choice.
        """
        selector = self.human_choice_selector(request_id)
        signature = hmac.new(self._secret, f"{selector}:{request_id}:{index}".encode(), hashlib.sha256).hexdigest()[:12]
        encoded = f"n:human_choice:{selector}:{index}:{signature}"
        if len(encoded.encode()) > 64:
            raise ValueError("Telegram callback data exceeds 64 bytes")
        return encoded

    def decode_human_choice(self, value: str, request_ids: Any) -> tuple[str, str] | None:
        """Resolve a compact human-choice callback to its request and index."""
        parts = value.split(":")
        if len(parts) != 5 or parts[0] != "n" or parts[1] != "human_choice":
            return None
        _, _, selector, index, signature = parts
        if not index.isdigit() or len(index) > 4:
            return None
        for candidate in request_ids:
            if self.human_choice_selector(str(candidate)) != selector:
                continue
            expected = hmac.new(self._secret, f"{selector}:{candidate}:{index}".encode(), hashlib.sha256).hexdigest()[:12]
            if hmac.compare_digest(signature, expected):
                return str(candidate), index
        return None

    def decode(self, value: str) -> tuple[str, str] | tuple[str, str, str] | None:
        parts = value.split(":")
        if len(parts) not in (4, 5) or parts[0] != "n":
            return None
        if len(parts) == 4:
            action, incident_id, signature = parts[1:]
            if action == "health_plan":
                return None
            expected = self.encode(action, incident_id).rsplit(":", 1)[1]
            if not hmac.compare_digest(signature, expected):
                return None
            return action, incident_id
        action, incident_id, plan_id, signature = parts[1:]
        if action not in {"health_plan", "choice", "human_choice"}:
            return None
        expected = (
            self.encode(action, incident_id, plan_id)
            if action == "health_plan"
            else self.encode(action, incident_id, choice_id=plan_id)
        ).rsplit(":", 1)[1]
        if not hmac.compare_digest(signature, expected):
            return None
        return action, incident_id, plan_id


TelegramApi = Callable[[str, dict[str, Any]], dict[str, Any]]
ChoiceCallback = Callable[[str, str, str], Mapping[str, Any]]
InboxSink = Callable[[dict[str, object]], Mapping[str, Any]]


def agent_herder_choice_callback(url: str, token: str, request_id: str, choice_id: str, _actor: str) -> dict[str, Any]:
    """POST only opaque choice identity to the bound Agent Herder session."""
    body = json.dumps({"request_id": request_id, "choice_id": choice_id}, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Agent Herder choice callback returned HTTP {response.status}")
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("Agent Herder choice callback returned invalid JSON")
    return result


def telegram_api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call one Bot API method without logging request bodies or the token."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urllib.parse.urlencode(payload).encode(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} failed")
    return result


class TelegramInteractionPoller:
    """Poll Bot API callbacks in-process; no public webhook or second daemon exists."""

    def __init__(self, center: NotificationCenter, token: str, allowed_user_ids: set[str], codec: TelegramActionCodec, health_plan_codec: TelegramHealthPlanCodec | None = None, api: TelegramApi | None = None, choice_callback: ChoiceCallback | None = None, human_request_token: str | None = None, inbox_sink: InboxSink | None = None, inbox_chat_ids: set[str] | None = None, inbox_sender_ids: set[str] | None = None) -> None:
        self._center = center
        self._token = token
        self._allowed_user_ids = allowed_user_ids
        self._codec = codec
        self._health_plan_codec = health_plan_codec
        self._api = api or (lambda method, payload: telegram_api(token, method, payload))
        self._choice_callback = choice_callback
        self._human_request_token = human_request_token
        self._inbox_sink = inbox_sink
        self._inbox_chat_ids = set(inbox_chat_ids or ())
        self._inbox_sender_ids = set(inbox_sender_ids or allowed_user_ids)

    def _answer(self, callback_id: str, text: str) -> None:
        self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def _message(self, chat_id: str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:1200]})

    def _incident_control_row(self, incident: Mapping[str, Any]) -> list[dict[str, str]]:
        incident_id = str(incident["id"])
        muted = bool(incident.get("notifications_muted"))
        return [
            {"text": "AI", "callback_data": self._codec.encode("ai", incident_id)},
            {
                "text": "🔔 Включить обратно" if muted else "🔕 Отключить такие",
                "callback_data": self._codec.encode("unmute" if muted else "mute", incident_id),
            },
        ]

    def _allowed(self, user_id: Any) -> bool:
        return str(user_id) in self._allowed_user_ids

    @staticmethod
    def _health_plan_label(plan_id: str, title: Any) -> str:
        known = {
            "observe": "Наблюдать", "repair": "Исправить", "verify": "Проверить",
            "plan-001": "План 1", "plan-002": "План 2", "plan-003": "План 3",
        }
        value = str(title or "").strip()
        return known.get(plan_id, value or plan_id)

    def _dismiss_choice_message(self, callback: dict[str, Any], label: str) -> None:
        """Remove choice buttons and leave a visible selected marker.

        Thin wrapper over the shared receipt-driven updater so a voice or API
        resolver marks exactly the same card text.
        """
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat_id = str(message.get("chat", {}).get("id") or "") if isinstance(message.get("chat"), dict) else ""
        message_id = message.get("message_id")
        if not chat_id or not isinstance(message_id, int) or message_id <= 0:
            return
        apply_selected_card(
            self._api,
            chat_id=chat_id,
            message_id=message_id,
            base_text=str(message.get("text") or ""),
            label=label,
            entities=message.get("entities"),
        )

    def _mark_health_choice_message(
        self,
        callback: dict[str, Any],
        incident_id: str,
        plan_id: str,
        label: str,
        plans: list[Mapping[str, Any]],
    ) -> None:
        """Keep all health routes visible and mark the route already selected."""
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat_id = str(message.get("chat", {}).get("id") or "") if isinstance(message.get("chat"), dict) else ""
        message_id = message.get("message_id")
        if not chat_id or not isinstance(message_id, int) or message_id <= 0 or self._health_plan_codec is None:
            return
        current_text = str(message.get("text") or "").rstrip()
        lines = current_text.splitlines()
        while lines and lines[-1].startswith("✅ Выбран путь:"):
            lines.pop()
            while lines and not lines[-1].strip():
                lines.pop()
        marker = f"✅ Выбран путь: {label[:128]}"
        base_text = "\n".join(lines).rstrip()
        base_limit = 4096 - len(marker) - 2
        if len(base_text) > base_limit:
            base_text = base_text[:max(0, base_limit - 1)].rstrip() + "…"
        text = f"{base_text}\n\n{marker}" if base_text else marker
        keyboard = health_plan_keyboard(self._health_plan_codec, incident_id, plans, selected_plan_id=plan_id)
        incident = self._center.get_incident(incident_id)
        if incident is not None:
            keyboard["inline_keyboard"].append(self._incident_control_row(incident))
        payload = {
            "chat_id": chat_id,
            "message_id": str(message_id),
            "text": text[:4096],
            "reply_markup": json.dumps(keyboard, ensure_ascii=False, separators=(",", ":")),
        }
        if isinstance(message.get("entities"), list):
            limit = len(payload["text"].encode("utf-16-le")) // 2
            entities = [entity for entity in message["entities"]
                        if isinstance(entity, dict)
                        and isinstance(entity.get("offset"), int)
                        and isinstance(entity.get("length"), int)
                        and 0 <= entity["offset"] < entity["offset"] + entity["length"] <= limit]
            payload["entities"] = json.dumps(entities, ensure_ascii=False)
        try:
            self._api("editMessageText", payload)
        except Exception:
            self._api("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": str(message_id),
                "reply_markup": payload["reply_markup"],
            })

    def _refresh_action_keyboard(self, callback: dict[str, Any], incident_id: str) -> None:
        """Reflect a mute toggle on the original Telegram card immediately."""
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "")
        message_id = message.get("message_id")
        incident = self._center.get_incident(incident_id)
        if not chat_id or not isinstance(message_id, int) or message_id <= 0 or incident is None:
            return
        plans = self._center.latest_health_plans(incident_id)
        if plans and self._health_plan_codec is not None and str(incident.get("event_type") or "").startswith("health."):
            selection = self._center.latest_health_selection(incident_id) or {}
            keyboard = health_plan_keyboard(
                self._health_plan_codec,
                incident_id,
                plans,
                selected_plan_id=str(selection.get("plan_id") or "") or None,
            )
            keyboard["inline_keyboard"].append(self._incident_control_row(incident))
        else:
            rows = (
                [[
                    {"text": "ACK", "callback_data": self._codec.encode("ack", incident_id)},
                    {"text": "Snooze 15m", "callback_data": self._codec.encode("snz", incident_id)},
                ]] if str(incident["severity"]) == "critical" else []
            )
            rows.append(self._incident_control_row(incident))
            keyboard = {"inline_keyboard": rows}
        self._api("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": str(message_id),
            "reply_markup": json.dumps(
                keyboard,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        })

    def _handle_human_choice(self, callback: dict[str, Any], callback_id: str, actor_id: Any,
                             request_id: str, index: str) -> None:
        """Resolve one choice tap through the same canonical completion."""
        from .http_api import complete_human_request

        with self._center.delivery_send_lock():
            request = self._center.get_human_request_from_telegram(request_id)
            if request.get("original_text") or any(choice.get("value") == "deep_analysis" for choice in request.get("choices", [])):
                message = callback.get("message") or {}
                if (str(message.get("chat", {}).get("id")) != str(request.get("telegram_chat_id"))
                        or message.get("message_id") != request.get("telegram_message_id")):
                    raise ValidationError("human request callback does not match its original card")
            choices = request.get("choices") if isinstance(request.get("choices"), list) else []
            try:
                selected = choices[int(index)]
            except (IndexError, TypeError, ValueError):
                raise ValidationError("human request choice is invalid")
            if request.get("original_text"):
                from .human_request_original import render_human_request_original
                text, _ = render_human_request_original({**request, "state": "pending"})
                message = {**callback.get("message", {}), "text": text}
                message.pop("entities", None)
                callback = {**callback, "message": message}
            message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            completed = complete_human_request(
                self._center, request, str(selected.get("value") or ""), f"telegram:{actor_id}", "telegram",
                self._api, base_text=str(message.get("text") or ""), entities=message.get("entities"),
                chat_id=str(chat.get("id") or ""), message_id=message.get("message_id"),
                choice_codec=self._codec,
            )
        if completed.get("card") == "marked" or completed.get("card") == "already_marked":
            self._answer(callback_id, f"✅ Выбрано: {completed.get('label') or selected.get('label')}")
        else:
            self._answer(callback_id, f"choice: {completed.get('state')}")

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        actor_id = callback.get("from", {}).get("id")
        if not self._allowed(actor_id):
            self._answer(callback_id, "Not authorized")
            return
        try:
            if self._health_plan_codec is not None:
                selected = self._health_plan_codec.decode(str(callback.get("data") or ""))
                if selected is not None:
                    incident_id, plan_id = selected
                    result = self._center.record_health_plan_selection(incident_id, plan_id, f"telegram:{actor_id}", f"{incident_id}:health.plan:{plan_id}")
                    plans = self._center.latest_health_plans(incident_id)
                    title = next((plan.get("title") for plan in plans if str(plan.get("plan_id") or "") == plan_id), "")
                    label = self._health_plan_label(plan_id, title)
                    self._mark_health_choice_message(callback, incident_id, plan_id, label, plans)
                    self._answer(callback_id, f"✅ Выбрано: {label}")
                    return
            data = str(callback.get("data") or "")
            if data.startswith("n:human_choice:"):
                # Compact selector form first; legacy cards keep the long form.
                resolved = self._codec.decode_human_choice(data, self._center.human_request_ids())
                if resolved is not None:
                    return self._handle_human_choice(callback, callback_id, actor_id, resolved[0], resolved[1])
            parsed = self._codec.decode(data)
            if parsed is not None and len(parsed) == 2:
                action, incident_id = parsed
                if action == "ai":
                    # Hold the same queue lock used for final delivery admission:
                    # the checkmark must be visible before a session can edit it.
                    with self._center.delivery_send_lock():
                        result = self._center.apply_telegram_action(incident_id, action, f"telegram:{actor_id}",
                            telegram_message=callback.get("message"))
                        if result.get("agent_job_delivery_id") and not result.get("idempotent"):
                            self._dismiss_choice_message(callback, "решение через AI. Создаётся сессия…")
                    if result.get("state") == "inactive":
                        self._answer(callback_id, "Инцидент уже закрыт; запуск не требуется")
                        return
                    self._answer(callback_id, "AI уже запущен" if result.get("idempotent") else "AI начал диагностику")
                    return
                result = self._center.apply_telegram_action(incident_id, action, f"telegram:{actor_id}")
                if action in {"mute", "unmute"}:
                    self._refresh_action_keyboard(callback, incident_id)
                    self._answer(
                        callback_id,
                        "Такие уведомления отключены" if action == "mute" else "Такие уведомления снова включены",
                    )
                    return
                if action == "ask" and result["state"] != "inactive":
                    chat_id = str(callback.get("message", {}).get("chat", {}).get("id") or "")
                    if chat_id:
                        self._message(chat_id, f"Reply with /ask {incident_id} followed by your question.")
                self._answer(callback_id, f"{action}: {result['state']}")
                return
            if parsed is not None and len(parsed) == 3:
                action, incident_id, plan_id = parsed
                if action == "health_plan":
                    result = self._center.record_health_plan_selection(incident_id, plan_id, f"telegram:{actor_id}", f"{incident_id}:health.plan:{plan_id}")
                    plans = self._center.latest_health_plans(incident_id)
                    title = next((plan.get("title") for plan in plans if str(plan.get("plan_id") or "") == plan_id), "")
                    label = self._health_plan_label(plan_id, title)
                    self._mark_health_choice_message(callback, incident_id, plan_id, label, plans)
                    self._answer(callback_id, f"✅ Выбрано: {label}")
                    return
                if action == "choice":
                    with self._center.delivery_send_lock():
                        selection = self._center.get_telegram_choice(incident_id, plan_id)
                        selected_choice_id = str(selection["choice_id"])
                        existing = self._center.selection_receipt(str(selection["request_id"]))
                        if existing:
                            result = {"status": "already_answered", "choice_id": existing["choice_id"]}
                        else:
                            if self._choice_callback is None:
                                raise ValidationError("Agent Herder choice callback is not configured")
                            result = self._choice_callback(
                                str(selection["request_id"]), selected_choice_id, f"telegram:{actor_id}")
                        self._center.record_telegram_choice(
                            incident_id,
                            selected_choice_id,
                            f"telegram:{actor_id}",
                            str(selection["request_id"]),
                            str(result.get("status") or "accepted"),
                        )
                        # Agent Herder owns the accepted option for the bound native
                        # session. When it answers with its own choice identity, that
                        # canonical result wins over the option tapped here, so a
                        # voice answer followed by a tap marks one and the same card.
                        canonical = canonical_choice_ref(str(result.get("choice_id") or selected_choice_id))
                        if canonical not in {str(choice["choice_id"]) for choice in self._center.latest_choice_request(incident_id)["choices"]}:
                            canonical = canonical_choice_ref(selected_choice_id)
                        label = next(
                            (str(choice["label"]) for choice in self._center.latest_choice_request(incident_id)["choices"]
                             if canonical_choice_ref(str(choice["choice_id"])) == canonical),
                            str(selection["label"]),
                        )
                        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
                        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
                        applied = apply_selection_receipt(
                            self._center,
                            self._api,
                            request_id=str(selection["request_id"]),
                            incident_id=incident_id,
                            choice_id=canonical,
                            label=label,
                            actor=f"telegram:{actor_id}",
                            source="telegram",
                            base_text=str(message.get("text") or ""),
                            entities=message.get("entities"),
                            chat_id=str(chat.get("id") or ""),
                            message_id=message.get("message_id"),
                            choice_buttons=[{"choice_id": str(choice["choice_id"]), "label": str(choice["label"]),
                                             "callback_data": self._codec.encode("choice", incident_id, choice_id=str(index))}
                                            for index, choice in enumerate(self._center.latest_choice_request(incident_id)["choices"])],
                        )
                        self._answer(callback_id, f"✅ Выбрано: {applied['selection']['label']}")
                        return
                if action == "human_choice":
                    self._handle_human_choice(callback, callback_id, actor_id, incident_id, plan_id)
                    return
            self._answer(callback_id, "Недопустимое действие")
        except (ValidationError, ValueError) as error:
            self._answer(callback_id, str(error)[:180])

    def _relay_reply_to_session(self, incident: Mapping[str, Any], text: str, chat_id: str, message_id: Any) -> bool:
        """Best-effort operator reply forwarding into the incident's live agent session."""
        try:
            from .agent_job_helper import relay_operator_reply
            session = self._center.latest_health_agent_session(str(incident.get("id") or ""))
            if not session:
                return False
            return relay_operator_reply(
                session["harness"],
                session["session_id"],
                str(incident.get("id") or ""),
                text,
                f"{chat_id}:{message_id}",
            )
        except Exception:
            # The reply is already durably attached to the incident; a failed
            # relay must never break the Telegram poll loop.
            return False

    def _handle_message(self, message: dict[str, Any]) -> None:
        actor_id = message.get("from", {}).get("id")
        text = str(message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id") or "")
        message_id = message.get("message_id")
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        allowed_actor = self._allowed(actor_id)
        reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        reply_message_id = reply.get("message_id")
        if allowed_actor and chat_id and isinstance(reply_message_id, int) and text:
            from .http_api import complete_human_request
            answered = self._center.human_request_for_reply(
                chat_id, reply_message_id, f"telegram:{actor_id}"
            )
            if answered is not None:
                # One canonical completion for a free-text answer too: the same
                # card is marked and the bound native session continues once.
                completed = complete_human_request(
                    self._center, answered, text, f"telegram:{actor_id}", "telegram_text", self._api,
                    chat_id=chat_id, message_id=reply_message_id,
                )
                native_status = (completed.get("native") or {}).get("status")
                if completed.get("answer_status") == "no_accept":
                    notice = "Ответ не принят: вопрос уже закрыт."
                elif native_status in {"resuming", "resumed", "sent"}:
                    notice = "Ответ принят и передан в сессию агента."
                elif native_status == "retryable":
                    notice = "Ответ сохранён. Сессия агента временно недоступна; передача будет повторена."
                elif native_status in {"unknown", "sending"}:
                    notice = "Ответ сохранён. Подтверждение передачи в сессию агента пока не получено."
                else:
                    notice = "Ответ принят."
                self._message(chat_id, notice)
                return
            incident = self._center.record_telegram_reply(
                chat_id, reply_message_id, f"telegram:{actor_id}", text
            )
            if incident is not None:
                self._message(chat_id, "Ответ передан в сессию агента." if self._relay_reply_to_session(incident, text, chat_id, message_id) else "Ответ привязан к инциденту.")
                return
        if (
            self._inbox_sink is not None
            and chat_id in self._inbox_chat_ids
            and str(actor_id) in self._inbox_sender_ids
            and text
            and isinstance(message_id, int)
            and message_id > 0
            and sender.get("is_bot") is not True
        ):
            self._inbox_sink({
                "schema": "universal.inbox.message.v1",
                "source": "telegram",
                "message_id": f"{chat_id}:{message_id}",
                "sender": str(actor_id),
                "body": text,
            })
            return
        if not allowed_actor:
            return
        if not text.startswith("/ask "):
            return
        _, incident_id, question = text.split(maxsplit=2) if len(text.split(maxsplit=2)) == 3 else ("", "", "")
        if not incident_id or not question:
            return
        self._center.record_telegram_ask(incident_id, f"telegram:{actor_id}", question)
        if chat_id:
            self._message(chat_id, "Вопрос сохранён.")

    def poll_once(self) -> int:
        if not self._token or not self._allowed_user_ids:
            return 0
        offset = self._center.telegram_update_offset()
        response = self._api("getUpdates", {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": json.dumps(["callback_query", "message"]),
        })
        processed = 0
        for update in response.get("result", []):
            update_id = int(update.get("update_id", -1))
            if update_id < offset:
                continue
            already_processed = not self._center.claim_telegram_update(update_id)
            if not already_processed:
                try:
                    if isinstance(update.get("callback_query"), dict):
                        self._handle_callback(update["callback_query"])
                    elif isinstance(update.get("message"), dict):
                        self._handle_message(update["message"])
                except Exception:
                    self._center.release_telegram_update(update_id)
                    raise
            self._center.complete_telegram_update(update_id)
            processed += 1
        return processed
