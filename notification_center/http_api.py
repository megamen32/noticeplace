"""Stdlib HTTP API and Telegram worker for the notification-center MVP."""

from __future__ import annotations

import json
import hashlib
import ipaddress
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .agentcall_phone import AgentCallPhoneAdapter
from .android_phone import AndroidPhoneAdapter, AndroidPhoneConfig
from .core import AuthorizationError, IdempotencyConflict, NotificationCenter, NotificationCenterError, ValidationError
from .gptadmin_phone import GptAdminPhoneAdapter
from .gptadmin_agent import DirectHealthRemediationAdapter, DurableHealthRemediationAdapter, GptAdminAgentJobAdapter
from .health_workflow import HealthWorkflow, TelegramHealthPlanCodec, health_plan_keyboard as health_plan_keyboard_cards, normalize_health_recommendation, sanitize_bounded_text, validate_health_plans
from .instructions import noticeplace_instructions
from .incident_call_chain import metadata as call_chain_metadata
from .human_request_original import render_human_request_original
from .selected_card import apply_selection_receipt, canonical_choice_ref
from .telegram_interactions import TelegramActionCodec, TelegramInteractionPoller, agent_herder_choice_callback, telegram_api
from .telegram_format import telegram_html
from .incident_card import controls as incident_controls
from mcp.notify_mcp import dispatch as notify_mcp_dispatch

ACTIVE_TELEGRAM_MODES = frozenset(("emergency", "important", "log", "health"))
SUPPORTED_TELEGRAM_MODES = frozenset((*ACTIVE_TELEGRAM_MODES, "health"))
SEVERITY_TITLES = {'debug': 'Отладка', 'info': 'Информация', 'notice': 'Обратите внимание',
                   'important': 'Важно', 'critical': 'Критично', 'emergency': 'Срочно'}
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CARD_TEXT_LIMIT = 3600


def _telegram_utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _telegram_truncate_utf16(text: str, limit: int) -> str:
    used = 0
    characters: list[str] = []
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if used + width > limit:
            break
        characters.append(character)
        used += width
    return "".join(characters)


def _human_request_requires_document(message: str, original_text: Any) -> bool:
    if not isinstance(original_text, str) or not original_text:
        return False
    return _telegram_utf16_length(
        f"Исходное сообщение:\n{original_text}\n\nРазбор:\n{message}"
    ) > TELEGRAM_TEXT_LIMIT


def _telegram_text_with_attachment(text: str) -> tuple[str, str | None]:
    """Keep a Telegram card readable and return the full text for an attachment."""
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        return text, None
    suffix = "\n\n📎 Полный текст приложен файлом."
    limit = TELEGRAM_CARD_TEXT_LIMIT - len(suffix)
    cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + suffix, text


def _telegram_document_request(
    token: str,
    fields: Mapping[str, str],
    filename: str,
    content: str,
) -> urllib.request.Request:
    """Build one stdlib multipart request for Telegram sendDocument."""
    boundary = f"noticeplace-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {'text/markdown' if filename.lower().endswith('.md') else 'text/plain'}; charset=utf-8\r\n\r\n".encode(),
        content.encode("utf-8"),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )


def _telegram_overflow_filename(incident_id: str) -> str:
    safe_id = "".join(character if character.isalnum() or character in "-_" else "-" for character in incident_id)
    return f"noticeplace-{safe_id[:80] or 'message'}.txt"


def _human_request_document_filename(request_id: str) -> str:
    safe_id = "".join(character if character.isalnum() or character in "-_" else "-" for character in request_id)
    return f"human-request-{safe_id[:80] or 'message'}.md"


def phone_call_allowed(severity: str, when: datetime, quiet_start_hour: float, quiet_end_hour: float) -> bool:
    """Allow critical calls outside quiet hours; emergency always overrides."""
    if severity == "emergency":
        return True
    if severity != "critical":
        return False
    start = max(0.0, min(24.0, float(quiet_start_hour)))
    end = max(0.0, min(24.0, float(quiet_end_hour)))
    if start == end:
        return True
    hour = when.hour + when.minute / 60 + when.second / 3600
    quiet = start <= hour < end if start < end else hour >= start or hour < end
    return not quiet


def telegram_active_modes(raw: str) -> set[str]:
    """Parse the operator-selected active topic modes."""
    if not raw.strip():
        return set(ACTIVE_TELEGRAM_MODES)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("TELEGRAM_ACTIVE_MODES_JSON must be a JSON array") from error
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError("TELEGRAM_ACTIVE_MODES_JSON must be a JSON array")
    modes = {item.strip().lower() for item in value}
    if not modes.issubset(SUPPORTED_TELEGRAM_MODES):
        raise RuntimeError("unsupported active Telegram mode")
    return modes


def telegram_mode(incident: dict[str, Any]) -> str:
    """Map an incident to the operator-facing forum mode."""
    if str(incident.get("event_type") or "").startswith("health."):
        return "health"
    severity = str(incident["severity"])
    return severity if severity in {"emergency", "important"} else "log"


def telegram_inline_keyboard(action_codec: TelegramActionCodec, incident: dict[str, Any], choices: list[dict[str, Any]] | None = None) -> dict[str, list[list[dict[str, str]]]]:
    """Return the narrow interactive contract for this incident severity.

    Only exact critical incidents ask the recipient to acknowledge or snooze;
    every ordinary notification retains AI and reversible mute controls. Native
    Telegram replies are associated automatically, so no Reply/Ask button is
    needed.
    """
    from .incident_card import controls
    incident_id = str(incident["id"])
    if isinstance(choices, list) and choices:
        buttons = []
        for index, choice in enumerate(choices[:4]):
            if not isinstance(choice, dict):
                continue
            choice_id = str(choice.get("choice_id") or "").strip()
            label = str(choice.get("label") or "").strip()
            if choice_id and label:
                # Keep callback_data below Telegram's 64-byte limit.  The
                # incident-local index is signed; NoticePlace resolves it
                # back to the opaque Agent Herder choice_id server-side.
                buttons.append([{"text": label[:128], "callback_data": action_codec.encode("choice", incident_id, choice_id=str(index))}])
        if buttons:
            return {"inline_keyboard": buttons + controls(action_codec, incident, incident.get('ai_session'))}
    ai = {"text": "AI", "callback_data": action_codec.encode("ai", incident_id)}
    muted = bool(incident.get("notifications_muted"))
    mute = {
        "text": "🔔 Включить обратно" if muted else "🔕 Отключить такие",
        "callback_data": action_codec.encode("unmute" if muted else "mute", incident_id),
    }
    return {"inline_keyboard": controls(action_codec, incident, incident.get('ai_session'))}


def _health_plans_ready(payload: Mapping[str, Any]) -> bool:
    """Require the exact three validated plans before a health card can send."""
    try:
        validate_health_plans(payload.get("health_plans"))
    except ValidationError:
        return False
    return True


def telegram_destination(default_chat_id: str, severity_routes: dict[str, dict[str, Any]], incident: dict[str, Any], active_modes: set[str] | None = None) -> dict[str, str]:
    """Resolve operator-owned project topics before severity routes."""
    mode = telegram_mode(incident)
    if active_modes is not None and mode not in active_modes:
        return {}
    project = str(incident.get("project") or "")
    project_route = severity_routes.get(project) if project not in {"health", "log", "important", "emergency", "debug", "info", "notice", "critical"} else None
    configured = project_route if project_route is not None else severity_routes.get(mode, severity_routes.get(str(incident.get("severity") or ""), {}))
    if configured.get("enabled") is False:
        return {}
    # An explicit project topic must never leak into another topic when its
    # operator configuration is incomplete. Event data cannot supply a route.
    if project_route is not None and (
        not str(configured.get("chat_id") or "").strip()
        or not configured.get("message_thread_id")
    ):
        return {}
    # Health cards contain actionable plan callbacks.  Never fall back to the
    # general chat when the dedicated operator topic is absent: that would
    # make a real user miss the required Health surface and could expose
    # controls in the wrong conversation.
    if mode == "health" and (not str(configured.get("chat_id") or "").strip() or not configured.get("message_thread_id")):
        return {}
    destination = {"chat_id": str(configured.get("chat_id") or default_chat_id)}
    thread_id = configured.get("message_thread_id")
    if thread_id is not None:
        destination["message_thread_id"] = str(thread_id)
    return destination


class TelegramTopicManager:
    """Reconcile active forum modes without deleting operator-created topics."""

    def __init__(self, chat_id: str, create_topic: Any) -> None:
        self._chat_id = chat_id
        self._create_topic = create_topic

    def reconcile(self, routes: dict[str, dict[str, Any]], active_modes: set[str]) -> dict[str, dict[str, Any]]:
        """Reuse known topic IDs and create one topic for each missing mode."""
        result = {mode: dict(route) for mode, route in routes.items() if mode in active_modes}
        for mode in sorted(active_modes):
            route = result.get(mode, {})
            if route.get("message_thread_id") is not None:
                continue
            thread_id = int(self._create_topic(mode.title()))
            if thread_id <= 0:
                raise RuntimeError("Telegram createForumTopic returned an invalid thread ID")
            result[mode] = {"chat_id": self._chat_id, "message_thread_id": thread_id}
        return result


def telegram_create_forum_topic(token: str, chat_id: str, name: str, timeout_seconds: float = 5, runner: Any = urllib.request.urlopen) -> int:
    """Create one Telegram forum topic and return its durable thread ID."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/createForumTopic",
        data=urllib.parse.urlencode({"chat_id": chat_id, "name": name}).encode(),
        method="POST",
    )
    with runner(request, timeout=timeout_seconds) as response:
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"Telegram createForumTopic returned HTTP {response.status}")
        result = json.loads(response.read())
    message = result.get("result") if isinstance(result, dict) else None
    thread_id = message.get("message_thread_id") if isinstance(message, dict) else None
    if result.get("ok") is not True or not isinstance(thread_id, int) or thread_id <= 0:
        raise RuntimeError("Telegram createForumTopic returned an invalid response")
    return thread_id


def telegram_edit_forum_topic(token: str, chat_id: str, thread_id: int, name: str, timeout_seconds: float = 5, runner: Any = urllib.request.urlopen) -> None:
    """Rename one Telegram forum topic through the Bot API."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/editForumTopic",
        data=urllib.parse.urlencode({"chat_id": chat_id, "message_thread_id": thread_id, "name": name}).encode(),
        method="POST",
    )
    with runner(request, timeout=timeout_seconds) as response:
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"Telegram editForumTopic returned HTTP {response.status}")
        result = json.loads(response.read())
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram editForumTopic returned an invalid response")


def telegram_routes_with_auto_topics(token: str, chat_id: str, routes: dict[str, dict[str, Any]], active_modes: set[str], state_path: str, *, enabled: bool, runner: Any = urllib.request.urlopen) -> dict[str, dict[str, Any]]:
    """Load persisted topic IDs, create missing active topics, and persist the result."""
    state_file = os.path.abspath(state_path)
    persisted: dict[str, dict[str, Any]] = {}
    try:
        with open(state_file, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            persisted = {str(mode): dict(route) for mode, route in loaded.items() if isinstance(route, dict)}
    except FileNotFoundError:
        pass
    merged = {**persisted, **routes}
    if not enabled:
        return {mode: route for mode, route in merged.items() if mode in active_modes}
    manager = TelegramTopicManager(chat_id, lambda name: telegram_create_forum_topic(token, chat_id, name, runner=runner))
    reconciled = manager.reconcile(merged, active_modes)
    parent = os.path.dirname(state_file) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    temporary = f"{state_file}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(reconciled, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, state_file)
    return reconciled


def telegram_delivery_destination(default_chat_id: str, severity_routes: dict[str, dict[str, Any]], payload: dict[str, Any], active_modes: set[str] | None = None) -> dict[str, str]:
    """Use an operator-owned consumer target when the durable delivery has one."""
    target = payload.get("target")
    if isinstance(target, dict) and target.get("chat_id") is not None:
        destination = {"chat_id": str(target["chat_id"])}
        if target.get("topic_id") is not None:
            destination["message_thread_id"] = str(target["topic_id"])
        return destination
    return telegram_destination(default_chat_id, severity_routes, payload["incident"], active_modes)


class TelegramSender:
    """Send compact incident cards via Telegram's HTTPS Bot API."""

    def __init__(self, token: str, chat_id: str, timeout_seconds: float = 5, action_codec: TelegramActionCodec | None = None, severity_routes: dict[str, dict[str, Any]] | None = None, active_modes: set[str] | None = None, center: NotificationCenter | None = None) -> None:
        """Create a sender; empty credentials intentionally leave it unavailable."""
        self._token = token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._action_codec = action_codec
        self._severity_routes = severity_routes or {}
        self._active_modes = active_modes
        self._center = center

    def _routes(self) -> dict[str, dict[str, Any]]:
        """Read live topic routes when the worker has a shared center."""
        if self._center is None:
            return self._severity_routes
        raw = self._center.get_runtime_setting("telegram_topics_json")
        if raw is None:
            return self._severity_routes
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return self._severity_routes
        return value if isinstance(value, dict) else self._severity_routes

    def _send_overflow_document(
        self,
        destination: Mapping[str, str],
        message_id: int,
        incident_id: str,
        full_text: str,
    ) -> dict[str, Any]:
        fields = {
            "chat_id": str(destination["chat_id"]),
            "caption": "📎 Полный текст уведомления",
            "reply_parameters": json.dumps({"message_id": message_id}, separators=(",", ":")),
        }
        if destination.get("message_thread_id"):
            fields["message_thread_id"] = str(destination["message_thread_id"])
        request = _telegram_document_request(
            self._token,
            fields,
            _telegram_overflow_filename(incident_id),
            full_text,
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")
            try:
                result = json.loads(response.read())
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError("Telegram returned invalid JSON") from error
        document_message = result.get("result") if isinstance(result, dict) else None
        document_message_id = document_message.get("message_id") if isinstance(document_message, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(document_message_id, int):
            raise RuntimeError("Telegram returned an invalid sendDocument response")
        return {
            "overflow_document_sent": True,
            "overflow_document_message_id": document_message_id,
            "overflow_filename": _telegram_overflow_filename(incident_id),
        }

    def send_document(
        self,
        destination: Mapping[str, str],
        filename: str,
        content: str,
        caption: str,
        reply_to_message_id: int,
    ) -> dict[str, Any]:
        """Send one UTF-8 text document in the card's chat and topic."""
        fields = {
            "chat_id": str(destination["chat_id"]),
            "caption": caption,
            "reply_parameters": json.dumps({"message_id": reply_to_message_id}, separators=(",", ":")),
        }
        if destination.get("message_thread_id"):
            fields["message_thread_id"] = str(destination["message_thread_id"])
        request = _telegram_document_request(self._token, fields, filename, content)
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")
            try:
                result = json.loads(response.read())
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError("Telegram returned invalid JSON") from error
        message = result.get("result") if isinstance(result, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(message_id, int):
            raise RuntimeError("Telegram sendDocument response has no valid message_id")
        document = message.get("document") if isinstance(message, dict) else None
        file_id = document.get("file_id") if isinstance(document, dict) else None
        return {
            "message_id": message_id,
            "filename": filename,
            **({"file_id": file_id} if isinstance(file_id, str) and file_id else {}),
        }

    def _delete_message_best_effort(self, chat_id: str, message_id: int) -> bool:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/deleteMessage",
            data=urllib.parse.urlencode({"chat_id": chat_id, "message_id": str(message_id)}).encode(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    return False
                result = json.loads(response.read())
        except (OSError, TypeError, json.JSONDecodeError):
            return False
        return isinstance(result, dict) and result.get("ok") is True

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver one card and retain the Bot API message identity for audits."""
        if not self._token or not self._chat_id:
            raise RuntimeError("Telegram sender is not configured")
        incident = payload["incident"]
        from .incident_card import render, projection_key, setting
        health_plans = payload.get('health_plans')
        choice_options = payload.get('choices')
        incident_card = (payload.get('target') or {}).get('incident_card')
        ai_card = payload.get('ai_card') if payload.get('health_session') else None
        card = incident_card if isinstance(incident_card, dict) else ai_card
        from .telegram_cleanup import should_delete
        if should_delete(self._center, payload):
            if not isinstance(card, dict):
                return {'cleanup_no_card': True, 'coalesced': True}
            if self._delete_message_best_effort(str(card['chat_id']), int(card['message_id'])):
                return {**card, 'card_deleted': True}
            # The worker retains uncertainty and never replays this claim.
            raise RuntimeError('Удаление Telegram-карточки не подтверждено')
        destination = ({'chat_id': str(card['chat_id'])} if isinstance(card, dict)
                       else telegram_delivery_destination(self._chat_id, self._routes(), payload, self._active_modes))
        mode = telegram_mode(incident)
        if self._active_modes is not None and mode not in self._active_modes:
            raise RuntimeError(f'Telegram mode is inactive: {mode}')
        if not destination:
            raise RuntimeError('Telegram topic route is not configured')
        if mode == 'health' and self._action_codec is None:
            raise RuntimeError('Telegram health delivery requires signed callback configuration')
        text, action_keyboard = render(payload, self._action_codec)
        health_keyboard = None
        if isinstance(health_plans, list) and health_plans and incident.get('state') != 'resolved' and not payload.get('human_question'):
            if self._action_codec is None:
                raise RuntimeError('Telegram health choices require signed callback configuration')
            selected = payload.get('health_plan_selection') or {}
            health_keyboard = health_plan_keyboard_cards(TelegramHealthPlanCodec(self._action_codec.secret), incident['id'],
                health_plans, selected_plan_id=str(selected.get('plan_id') or '') or None)
            action_keyboard['inline_keyboard'] = health_keyboard['inline_keyboard'] + action_keyboard['inline_keyboard']
        projection = projection_key(text, action_keyboard)
        request_data = {**destination, 'text': telegram_html(text), 'parse_mode': 'HTML', 'disable_web_page_preview': 'true',
                        'reply_markup': json.dumps(action_keyboard, ensure_ascii=False)}
        if isinstance(card, dict):
            request_data['message_id'] = str(card['message_id'])
            method = 'editMessageText'
        else:
            sound_key = 'telegram_critical_sound_enabled' if incident['severity'] in {'critical', 'emergency'} else 'telegram_routine_sound_enabled'
            request_data['disable_notification'] = 'false' if setting(self._center, sound_key) else 'true'
            method = 'sendMessage'
        overflow_text = None
        data = urllib.parse.urlencode(request_data).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{self._token}/{method}", data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Telegram returned HTTP {response.status}")
                try:
                    result = json.loads(response.read())
                except (TypeError, json.JSONDecodeError) as error:
                    raise RuntimeError("Telegram returned invalid JSON") from error
        except urllib.error.HTTPError as error:
            # Telegram confirms an identical edit is already applied. Do not
            # mark that exact, known message as an uncertain new delivery.
            try:
                failure = json.loads(error.read(4096))
            except (ValueError, TypeError):
                raise error
            if (method != "editMessageText" or error.code != 400 or not isinstance(failure, dict)
                    or not str(failure.get("description") or "").lower().startswith("bad request: message is not modified")):
                raise
            result = {"ok": True, "result": {"message_id": int(request_data["message_id"]),
                                            "chat": {"id": destination["chat_id"]}}}
        message = result.get("result") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(message, dict):
            raise RuntimeError("Telegram returned an invalid sendMessage response")
        message_id = message.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            raise RuntimeError("Telegram sendMessage response has no valid message_id")
        if method == 'editMessageText' and message_id != int(request_data['message_id']):
            raise RuntimeError('Telegram edit returned a different message identity')
        if isinstance(message.get('chat'), dict) and str(message['chat'].get('id')) != str(destination['chat_id']):
            raise RuntimeError('Telegram returned a different chat identity')
        receipt: dict[str, Any] = {
            "card_projection": projection,
            "message_id": message_id,
            "chat_id": str(message.get("chat", {}).get("id") or destination["chat_id"]) if isinstance(message.get("chat"), dict) else destination["chat_id"],
        }
        thread_id = message.get("message_thread_id")
        if isinstance(thread_id, int) and not isinstance(thread_id, bool) and thread_id > 0:
            receipt["message_thread_id"] = thread_id
        if method == "editMessageText":
            receipt["edited_in_place"] = True
        action_buttons = [
            button
            for row in (action_keyboard or {}).get("inline_keyboard", [])
            for button in row
            if isinstance(button, dict)
        ]
        ai_buttons = [button for button in action_buttons if str(button.get("callback_data") or "").startswith("n:ai:")]
        receipt["ai_button_count"] = len(ai_buttons)
        receipt["ai_signed_callback_count"] = sum(
            1
            for button in ai_buttons
            if self._action_codec is not None
            and self._action_codec.decode(str(button.get("callback_data") or "")) == ("ai", str(incident["id"]))
        )
        if isinstance(health_plans, list):
            receipt["health_plan_ids"] = [
                str(plan.get("plan_id") or "").strip()
                for plan in health_plans[:3]
                if isinstance(plan, dict) and str(plan.get("plan_id") or "").strip()
            ]
            buttons = [
                button
                for row in (health_keyboard or {}).get("inline_keyboard", [])
                for button in row
                if isinstance(button, dict)
            ]
            codec = TelegramHealthPlanCodec(self._action_codec.secret) if self._action_codec is not None else None
            health_buttons = [button for button in buttons if codec is not None and codec.decode(str(button.get("callback_data") or "")) is not None]
            receipt["health_button_count"] = len(health_buttons)
            receipt["health_signed_callback_count"] = sum(
                1
                for button in health_buttons
                if codec is not None and codec.decode(str(button.get("callback_data") or "")) is not None
            )
        if isinstance(choice_options, list) and choice_options:
            buttons = [
                button
                for row in telegram_inline_keyboard(self._action_codec, incident, choice_options).get("inline_keyboard", [])
                for button in row
                if isinstance(button, dict) and self._action_codec is not None
                and (self._action_codec.decode(str(button.get('callback_data') or '')) or ('',))[0] == 'choice'
            ] if self._action_codec is not None else []
            receipt["choice_ids"] = [
                str(choice.get("choice_id") or "").strip()
                for choice in choice_options[:4]
                if isinstance(choice, dict) and str(choice.get("choice_id") or "").strip()
            ]
            receipt["choice_button_count"] = len(buttons)
            receipt["choice_signed_callback_count"] = sum(
                1
                for button in buttons
                if self._action_codec is not None and self._action_codec.decode(str(button.get("callback_data") or "")) is not None
            )
        if overflow_text is not None:
            receipt.update(self._send_overflow_document(destination, message_id, str(incident["id"]), overflow_text))
        return receipt

    def edit_health_card(
        self,
        payload: dict[str, Any],
        message_id: int,
        chat_id: str,
        overflow_document_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Legacy queued callbacks use the same canonical renderer and message."""
        canonical = (payload.get('target') or {}).get('incident_card')
        if self._center:
            canonical = self._center.original_telegram_card(str(payload['incident']['id'])) or canonical
        card = canonical or {'chat_id': str(chat_id), 'message_id': message_id}
        update = {**payload, 'target': {**(payload.get('target') or {}), 'incident_card': card}}
        return self.send(update)

    @property
    def active_modes(self) -> set[str] | None:
        """Expose the immutable operator mode set to the dispatcher."""
        return set(self._active_modes) if self._active_modes is not None else None


class MatrixCallSender:
    """POST an incident to the LAN-only MatrixRTC bridge and return its answer state."""

    def __init__(self, url: str, token: str, timeout_seconds: float = 150, runner: Any = urllib.request.urlopen) -> None:
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit one incident over one bounded authenticated HTTP request."""
        if not self._url or not self._token:
            raise RuntimeError("Matrix call sender is not configured")
        incident = payload["incident"]
        request = {
            "incident_id": str(incident["id"]),
            "project": str(incident["project"]),
            "severity": str(incident["severity"]),
            "title": str(incident["title"]),
            "body": str(incident["body"]),
        }
        bridge_request = urllib.request.Request(
            self._url,
            data=json.dumps(request, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        try:
            with self._runner(bridge_request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"Matrix call bridge returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError("Matrix call bridge returned invalid JSON") from error
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Matrix call bridge did not start a call")
        answered = result.get("answered") is True
        target = str(result.get("target") or "")
        if answered and not target.startswith("@"):
            raise RuntimeError("Matrix call bridge did not identify the answerer")
        return {"answered": answered, "actor": f"matrix:{target}" if answered else None}


class MatrixMessageSender:
    """Send one idempotent text message through the Matrix Client-Server API."""

    def __init__(
        self,
        homeserver: str,
        token: str,
        *,
        default_room_id: str = "",
        timeout_seconds: float = 8,
        runner: Any = urllib.request.urlopen,
    ) -> None:
        self._homeserver = homeserver.rstrip("/")
        self._token = token
        self._default_room_id = default_room_id
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def send(self, payload: dict[str, Any], delivery_key: str) -> dict[str, Any]:
        """Use a stable Matrix transaction id so retries cannot duplicate a message."""
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        room_id = str(target.get("room_id") or self._default_room_id).strip()
        if not self._homeserver or not self._token or not room_id:
            raise RuntimeError("Matrix message sender is not configured")
        incident = payload["incident"]
        title = str(incident.get("title") or "").strip()
        body = str(incident.get("body") or "").strip()
        source = str(incident.get("correlation_id") or "").strip()
        text = title
        if body:
            text += f"\n\n{body}"
        if source:
            text += f"\n\nSource: {source}"
        transaction_id = "notice-" + hashlib.sha256(delivery_key.encode()).hexdigest()[:32]
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/"
            f"{urllib.parse.quote(room_id, safe='')}/send/m.room.message/{transaction_id}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps({"msgtype": "m.text", "body": text}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="PUT",
        )
        try:
            with self._runner(request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"Matrix message send returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError("Matrix message send returned invalid JSON") from error
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not event_id.startswith("$"):
            raise RuntimeError("Matrix message send returned no event id")
        return {"event_id": event_id, "room_id": room_id, "transaction_id": transaction_id}


class TelegramMessageSender:
    """Send a generic route-controlled text message through the Telegram Bot API."""

    def __init__(
        self,
        token: str,
        *,
        default_chat_id: str = "",
        timeout_seconds: float = 8,
        runner: Any = urllib.request.urlopen,
    ) -> None:
        self._token = token
        self._default_chat_id = default_chat_id
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def _send_overflow_document(
        self,
        target: Mapping[str, Any],
        chat_id: str,
        message_id: int,
        incident_id: str,
        full_text: str,
    ) -> dict[str, Any]:
        fields = {
            "chat_id": chat_id,
            "caption": "📎 Полный текст уведомления",
            "reply_parameters": json.dumps({"message_id": message_id}, separators=(",", ":")),
        }
        topic_id = target.get("topic_id") or target.get("message_thread_id")
        if topic_id is not None:
            fields["message_thread_id"] = str(topic_id)
        request = _telegram_document_request(
            self._token,
            fields,
            _telegram_overflow_filename(incident_id),
            full_text,
        )
        try:
            with self._runner(request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"Telegram returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError("Telegram returned invalid JSON") from error
        message = result.get("result") if isinstance(result, dict) else None
        document_message_id = message.get("message_id") if isinstance(message, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(document_message_id, int):
            raise RuntimeError("Telegram sendDocument response has no valid message_id")
        return {
            "overflow_document_sent": True,
            "overflow_document_message_id": document_message_id,
            "overflow_filename": _telegram_overflow_filename(incident_id),
        }

    def send(self, payload: dict[str, Any], _delivery_key: str) -> dict[str, Any]:
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        chat_id = str(target.get("chat_id") or self._default_chat_id).strip()
        if not self._token or not chat_id:
            raise RuntimeError("Telegram message sender is not configured")
        incident = payload["incident"]
        title = str(incident.get("title") or "").strip()
        body = str(incident.get("body") or "").strip()
        source = str(incident.get("correlation_id") or "").strip()
        text = title
        if body:
            text += f"\n\n{body}"
        if source:
            text += f"\n\nИсточник: {source}"
        visible_text, overflow_text = _telegram_text_with_attachment(text)
        topic_id = target.get("topic_id") or target.get("message_thread_id")
        request_fields = {
            "chat_id": chat_id,
            "text": visible_text,
            "disable_web_page_preview": "true",
        }
        if topic_id is not None:
            request_fields["message_thread_id"] = str(topic_id)
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=urllib.parse.urlencode(request_fields).encode(),
            method="POST",
        )
        try:
            with self._runner(request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"Telegram returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError("Telegram returned invalid JSON") from error
        message = result.get("result") if isinstance(result, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(message_id, int) or message_id <= 0:
            raise RuntimeError("Telegram sendMessage response has no valid message_id")
        resolved_chat_id = str(message.get("chat", {}).get("id") or chat_id) if isinstance(message.get("chat"), dict) else chat_id
        receipt = {"message_id": message_id, "chat_id": resolved_chat_id}
        if overflow_text is not None:
            receipt.update(self._send_overflow_document(target, resolved_chat_id, message_id, str(incident.get("id") or "message"), overflow_text))
        return receipt


class WebhookMessageSender:
    """Send a provider-neutral message to one fixed operator-owned bridge."""

    def __init__(
        self,
        channel: str,
        url: str,
        token: str,
        *,
        timeout_seconds: float = 8,
        runner: Any = urllib.request.urlopen,
    ) -> None:
        self._channel = channel
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def send(self, payload: dict[str, Any], delivery_key: str) -> dict[str, Any]:
        if not self._url or not self._token:
            raise RuntimeError(f"{self._channel} sender is not configured")
        incident = payload["incident"]
        request_payload = {
            "schema": "noticeplace.message.v1",
            "channel": self._channel,
            "delivery_key": delivery_key,
            "target": payload.get("target") if isinstance(payload.get("target"), dict) else {},
            "message": {
                "title": str(incident.get("title") or ""),
                "body": str(incident.get("body") or ""),
                "source": str(incident.get("correlation_id") or ""),
            },
        }
        idempotency_key = "notice-" + hashlib.sha256(delivery_key.encode()).hexdigest()
        request = urllib.request.Request(
            self._url,
            data=json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        try:
            with self._runner(request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"{self._channel} bridge returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{self._channel} bridge returned invalid JSON") from error
        receipt_id = result.get("receipt_id") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(receipt_id, str) or not receipt_id:
            raise RuntimeError(f"{self._channel} bridge returned no receipt")
        return {"receipt_id": receipt_id, "channel": self._channel}


class WebhookCallSender(WebhookMessageSender):
    """Use the same fixed bridge transport with a call-specific contract."""

    def send(self, payload: dict[str, Any], delivery_key: str) -> dict[str, Any]:
        if not self._url or not self._token:
            raise RuntimeError(f"{self._channel} sender is not configured")
        incident = payload["incident"]
        request_payload = {
            "schema": "noticeplace.call.v1",
            "channel": self._channel,
            "delivery_key": delivery_key,
            "target": payload.get("target") if isinstance(payload.get("target"), dict) else {},
            "message": {
                "title": str(incident.get("title") or ""),
                "body": str(incident.get("body") or ""),
                "source": str(incident.get("correlation_id") or ""),
            },
        }
        idempotency_key = "notice-" + hashlib.sha256(delivery_key.encode()).hexdigest()
        request = urllib.request.Request(
            self._url,
            data=json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}", "Idempotency-Key": idempotency_key},
            method="POST",
        )
        try:
            with self._runner(request, timeout=self._timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"{self._channel} bridge returned HTTP {response.status}")
                result = json.loads(response.read())
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{self._channel} bridge returned invalid JSON") from error
        receipt_id = result.get("receipt_id") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(receipt_id, str) or not receipt_id:
            raise RuntimeError(f"{self._channel} bridge returned no receipt")
        return {
            "receipt_id": receipt_id,
            "channel": self._channel,
            "answered": result.get("answered") is True,
            "actor": str(result.get("actor") or "") or None,
        }


class DeliveryWorker:
    """Run due delivery claims through known adapters without hiding failures."""

    def __init__(self, center: NotificationCenter, telegram: TelegramSender, matrix_call: MatrixCallSender | Any | None = None, android_phone: AndroidPhoneAdapter | AgentCallPhoneAdapter | Any | None = None, lease_seconds: float = 180, call_escalation_seconds: float = 0, android_telegram_call_escalation_seconds: float = 0, android_phone_call_escalation_seconds: float = 0, critical_repeat_seconds: float = 0, critical_call_escalation_seconds: float | None = None, emergency_call_escalation_seconds: float = 0, agent_jobs: dict[str, GptAdminAgentJobAdapter | Any] | None = None, message_adapters: Mapping[str, Any] | None = None, call_adapters: Mapping[str, Any] | None = None, android_phone_emergency_call_escalation_seconds: float = 0, android_phone_quiet_start_hour: float = 0, android_phone_quiet_end_hour: float = 12, android_phone_quiet_timezone: str = "Europe/Moscow", telegram_call: Any = None, incident_call_order: tuple[str, ...] = (), task_action_owner: Any = None) -> None:
        """Attach durable delivery state to Telegram, Matrix, and the local S21 adapter."""
        self._center = center
        self._telegram = telegram
        self._matrix_call = matrix_call
        self._android_phone = android_phone
        from .incident_call_chain import ORDER
        if incident_call_order and tuple(incident_call_order) != ORDER:
            raise ValueError('incident call order must be Matrix, Telegram, phone')
        self._incident_call_order = tuple(incident_call_order)
        self._telegram_call = telegram_call
        self._task_action_owner = task_action_owner
        self._agent_jobs = dict(agent_jobs or {})
        self._message_adapters = dict(message_adapters or {})
        self._call_adapters = dict(call_adapters or {})
        self._lease_seconds = lease_seconds
        self._critical_call_escalation_seconds = max(0, call_escalation_seconds if critical_call_escalation_seconds is None else critical_call_escalation_seconds)
        self._emergency_call_escalation_seconds = max(0, emergency_call_escalation_seconds)
        self._critical_repeat_seconds = max(0, critical_repeat_seconds)
        self._android_telegram_call_escalation_seconds = max(0, android_telegram_call_escalation_seconds)
        self._android_phone_call_escalation_seconds = max(0, android_phone_call_escalation_seconds)
        self._android_phone_emergency_call_escalation_seconds = max(0, android_phone_emergency_call_escalation_seconds)
        self._android_phone_quiet_start_hour = android_phone_quiet_start_hour
        self._android_phone_quiet_end_hour = android_phone_quiet_end_hour
        self._android_phone_quiet_timezone = android_phone_quiet_timezone

    def claim_due(self, limit: int = 20, channel_group: str | None = None) -> list[dict[str, Any]]:
        """Claim a bounded batch without blocking the dispatcher heartbeat."""
        deliveries = self._center.claim_due_deliveries(lease_seconds=self._lease_seconds, limit=limit, channel_group=channel_group)
        self._center.mark_dispatcher_healthy()
        return deliveries

    def _matrix_delay_seconds(self, severity: str) -> float:
        if severity == "critical":
            return self._runtime_float("matrix_call_critical_escalation_seconds", self._critical_call_escalation_seconds)
        if severity == "emergency":
            return self._runtime_float("matrix_call_emergency_escalation_seconds", self._emergency_call_escalation_seconds)
        return 0

    def _runtime_float(self, key: str, fallback: float) -> float:
        """Read a non-negative live setting, retaining env as the migration fallback."""
        value = self._center.get_runtime_setting(key)
        if value is None:
            return fallback
        try:
            return max(0, float(value))
        except (TypeError, ValueError):
            return fallback

    def _automatic_calls_enabled(self) -> bool:
        """Read the operator kill switch for each delivery, without a process restart."""
        value = self._center.get_runtime_setting("automatic_calls_enabled")
        if value is None:
            return True
        return value.lower() in {"1", "true", "yes", "on"}

    def _phone_delay_seconds(self, severity: str) -> float:
        if severity == "critical":
            return self._runtime_float("android_phone_call_escalation_seconds", self._android_phone_call_escalation_seconds)
        if severity == "emergency":
            return self._runtime_float("android_phone_emergency_call_escalation_seconds", self._android_phone_emergency_call_escalation_seconds)
        return 0

    def _phone_call_allowed(self, incident: Mapping[str, Any]) -> bool:
        return phone_call_allowed(
            str(incident.get("severity") or ""),
            datetime.now(ZoneInfo(self._android_phone_quiet_timezone)),
            self._runtime_float("android_phone_quiet_start_hour", self._android_phone_quiet_start_hour),
            self._runtime_float("android_phone_quiet_end_hour", self._android_phone_quiet_end_hour),
        )

    @staticmethod
    def _telegram_repeat_sequence(delivery_key: str) -> int | None:
        if delivery_key.endswith(":initial"):
            return 0
        prefix = ":telegram.main:repeat:"
        if prefix not in delivery_key:
            return None
        try:
            return int(delivery_key.rsplit(":", 1)[1])
        except ValueError:
            return None

    def _after_telegram_delivery(self, delivery: dict[str, Any], incident: dict[str, Any]) -> None:
        """Durably schedule policy follow-ups only after Telegram delivery succeeded."""
        target = self._center.delivery_payload(delivery).get("target", {})
        if isinstance(target, dict) and any(isinstance(target.get(key), dict) for key in ("health_session", "health_outcome")):
            return
        incident_id = str(delivery["incident_id"])
        health_mode = telegram_mode(incident) == "health"
        if (self._incident_call_order and str(delivery['delivery_key']).endswith(':initial')
            and str(incident.get('kind') or '') == 'incident'
            and str(incident['severity']) in {'critical','emergency'}
            and self._automatic_calls_enabled()):
            self._center.schedule_incident_call_chain_if_active(
                incident_id, time.time() + self._phone_delay_seconds(str(incident['severity'])))
        if (
            not self._incident_call_order
            and
            not health_mode
            and str(delivery["delivery_key"]).endswith(":initial")
            and self._matrix_call is not None
        ):
            delay = self._matrix_delay_seconds(str(incident["severity"]))
            if delay > 0:
                self._center.schedule_escalation_if_active(incident_id, "matrix.call", time.time() + delay)
        if (
            not self._incident_call_order
            and
            str(delivery["delivery_key"]).endswith(":initial")
            and str(incident["severity"]) in {"critical", "emergency"}
            and self._android_phone is not None
            and getattr(self._android_phone, "can_phone_call", False)
            and self._automatic_calls_enabled()
        ):
            delay = self._phone_delay_seconds(str(incident["severity"]))
            self._center.schedule_escalation_if_active(
                incident_id,
                "android.phone.call",
                time.time() + delay,
            )
        if health_mode:
            return
        sequence = self._telegram_repeat_sequence(str(delivery["delivery_key"]))
        repeat_delay = self._runtime_float("telegram_critical_repeat_seconds", self._critical_repeat_seconds)
        if str(incident["severity"]) == "critical" and sequence is not None and repeat_delay > 0:
            self._center.schedule_telegram_repeat_if_active(incident_id, sequence + 1, time.time() + repeat_delay)

    def _send_critical_pre_call_context(self, payload: dict[str, Any], call_delivery: Mapping[str, Any]) -> str:
        """Send the durable incident-card refresh before a critical phone call."""
        incident = payload["incident"]
        if telegram_mode(incident) == "health":
            return "sent"
        if str(incident["severity"]) != "critical":
            return "sent"
        active_modes = getattr(self._telegram, "active_modes", None)
        if active_modes is not None and telegram_mode(incident) not in active_modes:
            return "blocked"
        context_id = self._center.queue_incident_card_refresh(
            str(incident["id"]), now=time.time(), step=f"pre-call:{call_delivery['id']}"
        )
        if context_id is None:
            return "blocked"
        with self._center._lock:
            row = self._center._connection.execute("SELECT status FROM deliveries WHERE id=?", (context_id,)).fetchone()
        if row is None:
            return "blocked"
        if str(row["status"]) == "sent":
            return "sent"
        if str(row["status"]) in {"uncertain", "failed", "cancelled", "superseded"}:
            return "blocked"
        claimed = self._center.claim_due_deliveries(
            now_epoch=time.time(), limit=1, channel_group="message", delivery_id=context_id
        )
        if claimed:
            self.deliver(claimed[0])
        with self._center._lock:
            row = self._center._connection.execute("SELECT status FROM deliveries WHERE id=?", (context_id,)).fetchone()
        if row is None:
            return "blocked"
        state = str(row["status"])
        return "sent" if state == "sent" else "wait" if state in {"queued", "claimed", "sending"} else "blocked"

    def deliver(self, delivery: dict[str, Any]) -> None:
        """Deliver one claimed job; callers may run this in a bounded worker pool."""
        call_reserved = False
        try:
            from .incident_call_chain import intended
            if intended(delivery) and call_chain_metadata(delivery) is None:
                self._center.complete_delivery(delivery['id'],'cancelled','Некорректная последовательность звонков',
                    claimed_at=delivery.get('claimed_at'),attempt=delivery.get('attempt'))
                return
            payload = self._center.delivery_payload(delivery)
            if str(delivery["channel"]).endswith(".call") and not self._automatic_calls_enabled():
                self._center.complete_delivery(delivery["id"], "cancelled", "automatic calls disabled by operator")
                return
            if str(delivery["channel"]).endswith(".call") and not phone_call_allowed(
                "emergency" if payload["incident"].get("severity") == "emergency" else "critical",
                datetime.now(ZoneInfo(self._android_phone_quiet_timezone)),
                self._runtime_float("android_phone_quiet_start_hour", self._android_phone_quiet_start_hour),
                self._runtime_float("android_phone_quiet_end_hour", self._android_phone_quiet_end_hour),
            ):
                self._center.complete_delivery(
                    delivery["id"], "cancelled", "Автоматический звонок отменён: тихие часы",
                    claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                )
                return
            if delivery["channel"] == "telegram.edit":
                with self._center.delivery_send_lock():
                    payload = self._center.delivery_payload(delivery)
                    if not self._center.delivery_is_claimed(
                        str(delivery["id"]),
                        claimed_at=delivery.get("claimed_at"),
                        attempt=delivery.get("attempt"),
                    ):
                        return
                    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
                    try:
                        message_id = int(target.get("message_id"))
                        chat_id = str(target.get("chat_id") or "")
                        source_delivery_id = str(target.get("source_delivery_id") or "")
                    except (TypeError, ValueError):
                        raise RuntimeError("Telegram health migration target is invalid")
                    if message_id <= 0 or not chat_id or not source_delivery_id:
                        raise RuntimeError("Telegram health migration target is incomplete")
                    if not self._center.reserve_delivery_send(
                        str(delivery["id"]),
                        claimed_at=float(delivery["claimed_at"]),
                        attempt=int(delivery["attempt"]),
                    ):
                        return
                try:
                    overflow_document_message_id = target.get("overflow_document_message_id")
                    if isinstance(overflow_document_message_id, int) and overflow_document_message_id > 0:
                        receipt = self._telegram.edit_health_card(
                            payload,
                            message_id,
                            chat_id,
                            overflow_document_message_id=overflow_document_message_id,
                        )
                    else:
                        receipt = self._telegram.edit_health_card(payload, message_id, chat_id)
                    self._center.record_health_delivery_migrated(source_delivery_id, receipt)
                except Exception as error:
                    self._center.complete_delivery(
                        delivery["id"],
                        "uncertain",
                        f"Telegram health migration outcome is uncertain: {error}",
                        claimed_at=delivery.get("claimed_at"),
                        attempt=delivery.get("attempt"),
                    )
                    return
                self._center.complete_delivery(
                    delivery["id"],
                    "superseded",
                    "legacy Health card edited in place",
                    claimed_at=delivery.get("claimed_at"),
                    attempt=delivery.get("attempt"),
                    result=receipt,
                )
                return
            if delivery["channel"] in {"telegram.main", "telegram.message"} or str(delivery["channel"]).startswith("telegram.consumer:"):
                with self._center.delivery_send_lock():
                    # Rebuild the payload and validate the exact lease while the
                    # coalescer/claim-reclaimer is excluded from the final send.
                    payload = self._center.delivery_payload(delivery)
                    is_health = telegram_mode(payload["incident"]) == "health"
                    if not self._center.delivery_is_claimed(
                        str(delivery["id"]),
                        claimed_at=delivery.get("claimed_at"),
                        attempt=delivery.get("attempt"),
                    ):
                        return
                    if is_health and self._center.health_incident_is_synthetic(str(payload["incident"]["id"])):
                        self._center.complete_delivery(
                            delivery["id"],
                            "cancelled",
                            "synthetic health canary is not user-facing",
                            claimed_at=delivery.get("claimed_at"),
                            attempt=delivery.get("attempt"),
                        )
                        return
                    if delivery["channel"] == "telegram.main":
                        active_modes = getattr(self._telegram, "active_modes", None)
                        if active_modes is not None and telegram_mode(payload["incident"]) not in active_modes:
                            if telegram_mode(payload["incident"]) == "health":
                                self._center.complete_delivery(
                                    delivery["id"],
                                    "retry",
                                    "Telegram health topic route is not active",
                                    retry_after_seconds=300,
                                    claimed_at=delivery.get("claimed_at"),
                                    attempt=delivery.get("attempt"),
                                )
                            else:
                                self._center.complete_delivery(
                                    delivery["id"],
                                    "cancelled",
                                    "Telegram mode is inactive",
                                    claimed_at=delivery.get("claimed_at"),
                                    attempt=delivery.get("attempt"),
                                )
                            return
                    if not self._center.reserve_delivery_send(
                        str(delivery["id"]),
                        claimed_at=float(delivery["claimed_at"]),
                        attempt=int(delivery["attempt"]),
                    ):
                        return
                    from .incident_card import render, projection_key
                    owner = self._center.get_incident(self._center.telegram_card_owner(str(payload['incident']['id']))) or {}
                    text, keyboard = render(payload, getattr(self._telegram, '_action_codec', None))
                    card = (payload.get('target') or {}).get('incident_card')
                    from .telegram_cleanup import should_delete
                    if card and not should_delete(self._center, payload) and not payload.get('health_plans') and owner.get('telegram_card_projection') == projection_key(text, keyboard):
                        self._center.complete_delivery(delivery['id'], 'sent', claimed_at=delivery.get('claimed_at'), attempt=delivery.get('attempt'),
                                                       result={**card, 'coalesced': True})
                        if delivery['channel'] == 'telegram.main':
                            self._after_telegram_delivery(delivery, payload['incident'])
                        return
                try:
                    send_result = self._telegram.send(payload)
                except Exception as error:
                    self._center.complete_delivery(
                        delivery["id"],
                        "uncertain",
                        f"Telegram send outcome is uncertain: {error}",
                        claimed_at=delivery.get("claimed_at"),
                        attempt=delivery.get("attempt"),
                    )
                    return
                incident = payload["incident"]
                self._center.complete_delivery(
                    delivery["id"],
                    "sent",
                    claimed_at=delivery.get("claimed_at"),
                    attempt=delivery.get("attempt"),
                    result=send_result if isinstance(send_result, dict) else None,
                )
                if delivery["channel"] == "telegram.main":
                    self._after_telegram_delivery(delivery, incident)
                return
            elif str(delivery["channel"]).endswith(".message"):
                adapter = self._message_adapters.get(str(delivery["channel"]))
                if adapter is None:
                    raise RuntimeError(f"message channel adapter is not configured: {delivery['channel']}")
                if not self._center.reserve_delivery_send(
                    str(delivery["id"]),
                    claimed_at=float(delivery["claimed_at"]),
                    attempt=int(delivery["attempt"]),
                ):
                    return
                result = adapter.send(payload, str(delivery["delivery_key"]))
                self._center.complete_delivery(
                    delivery["id"], "sent",
                    claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    result=result if isinstance(result, Mapping) else None,
                )
                return
            elif (str(delivery['channel']) in {'matrix.call','telegram.call'}
                  and call_chain_metadata(delivery)):
                from .incident_call_chain import safe_missed
                adapter = self._matrix_call if delivery['channel']=='matrix.call' else self._telegram_call
                if adapter is None: raise RuntimeError('incident call adapter is unavailable')
                if not self._center.reserve_delivery_send(str(delivery['id']),float(delivery['claimed_at']),int(delivery['attempt'])):
                    return
                call_reserved = True
                result = adapter.send(payload,str(delivery['delivery_key']))
                if isinstance(result, Mapping) and result.get('answered') is True and result.get('actor'):
                    outcome,reason='sent',None
                elif safe_missed(result): outcome,reason='failed','Звонок завершён без ответа'
                else: outcome,reason='uncertain','Результат звонка неизвестен; повтор и переход к другому каналу остановлены'
                self._center.complete_delivery(delivery['id'],outcome,reason,
                    claimed_at=delivery.get('claimed_at'),attempt=delivery.get('attempt'),result=result)
                if outcome=='sent':
                    self._center.acknowledge_if_active(str(delivery['incident_id']),str(result['actor']))
                return
            elif delivery["channel"] == "matrix.call":
                if self._matrix_call is None:
                    raise RuntimeError("Matrix call sender is not configured")
                if not self._center.reserve_delivery_send(str(delivery["id"]), float(delivery["claimed_at"]), int(delivery["attempt"])):
                    return
                call_reserved = True
                result = self._matrix_call.send(payload)
                if result.get('answered') is None or result.get('uncertain') is True:
                    self._center.complete_delivery(delivery['id'],'uncertain','Результат звонка неизвестен',
                        claimed_at=delivery.get('claimed_at'),attempt=delivery.get('attempt'),result=result)
                    return
                if result["answered"]:
                    self._center.acknowledge_if_active(str(delivery["incident_id"]), str(result["actor"]))
                else:
                    self._center.complete_delivery(
                        delivery["id"], "failed", "Matrix call was not answered",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
            elif delivery["channel"] == "android.telegram.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                if not self._center.reserve_delivery_send(str(delivery["id"]), float(delivery["claimed_at"]), int(delivery["attempt"])):
                    return
                self._android_phone.telegram_call(payload)
            elif (
                delivery["channel"] in {"android.phone.call", "phone.call"}
                and self._android_phone is not None
            ):
                if not self._phone_call_allowed(payload["incident"]):
                    self._center.complete_delivery(
                        delivery["id"], "cancelled", "critical phone call suppressed by quiet hours",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
                if not self._center.reserve_delivery_send(
                    str(delivery["id"]), claimed_at=float(delivery["claimed_at"]), attempt=int(delivery["attempt"]),
                ):
                    return
                call_reserved = True
                if delivery["channel"] == "android.phone.call":
                    context_status = self._send_critical_pre_call_context(payload, delivery)
                    if context_status != "sent":
                        if context_status == "wait":
                            self._center.complete_delivery(
                                delivery["id"], "retry", "Waiting for a confirmed pre-call Telegram context receipt",
                                retry_after_seconds=30, claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                                schedule_failure_neighbours=False,
                            )
                        else:
                            self._center.complete_delivery(
                                delivery["id"], "cancelled", "Phone call suppressed because pre-call Telegram context is not confirmed",
                                claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                            )
                        return
                try:
                    result = self._android_phone.phone_call(payload)
                except (TimeoutError, ConnectionResetError, BrokenPipeError):
                    self._center.complete_delivery(
                        delivery["id"], "uncertain", "Phone call outcome unknown; automatic redial suppressed",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
                self._center.complete_delivery(
                    delivery["id"], "sent", claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    result=result if isinstance(result, Mapping) else None,
                )
                return
            elif str(delivery["channel"]).endswith(".call"):
                adapter = self._call_adapters.get(str(delivery["channel"]))
                if adapter is None:
                    raise RuntimeError(f"call channel adapter is not configured: {delivery['channel']}")
                if not self._center.reserve_delivery_send(
                    str(delivery["id"]), claimed_at=float(delivery["claimed_at"]), attempt=int(delivery["attempt"]),
                ):
                    return
                result = adapter.send(payload, str(delivery["delivery_key"]))
                if isinstance(result, Mapping) and result.get("answered") is True and result.get("actor"):
                    self._center.acknowledge_if_active(str(delivery["incident_id"]), str(result["actor"]))
                self._center.complete_delivery(
                    delivery["id"], "sent", claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    result=result if isinstance(result, Mapping) else None,
                )
                return
            elif str(delivery["channel"]).startswith("gptadmin.agent:"):
                job_name = str(delivery["channel"])[len("gptadmin.agent:"):]
                adapter = self._agent_jobs.get(job_name)
                if adapter is None:
                    raise RuntimeError(f"GPTAdmin agent job adapter is not configured: {job_name}")
                # Agent jobs can run much longer than the ordinary claim
                # lease. Reserve this exact generation before crossing the
                # GPTAdmin/Hermes boundary so a second worker cannot reclaim
                # and launch the same remediation again.
                if not self._center.reserve_delivery_send(
                    str(delivery["id"]),
                    claimed_at=float(delivery["claimed_at"]),
                    attempt=int(delivery["attempt"]),
                ):
                    return
                if job_name in {"health-diagnosis", "health-remediation"} and callable(getattr(adapter, "send_with_progress", None)):
                    receipt = adapter.send_with_progress(
                        payload,
                        str(delivery["delivery_key"]),
                        lambda job: self._center.record_health_agent_progress(
                            str(delivery["incident_id"]),
                            str(delivery["id"]),
                            payload,
                            job,
                        ),
                    )
                else:
                    receipt = adapter.send(payload, str(delivery["delivery_key"]))
                workflow_result = self._center.record_agent_job_result(
                    str(delivery["incident_id"]),
                    str(delivery["id"]),
                    job_name,
                    receipt,
                    payload.get("health_context") if isinstance(payload.get("health_context"), dict) else None,
                )
                if job_name == "health-remediation" and str(receipt.get("status") or "") == "completed" and workflow_result.get("health_workflow") is not False and workflow_result.get("accepted") is not True:
                    self._center.complete_delivery(
                        delivery["id"], "failed", "Health remediation did not produce a resolved receipt",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
                if str(receipt.get("status") or "") == "failed":
                    self._center.complete_delivery(
                        delivery["id"], "failed", "GPTAdmin agent job reported terminal failure",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
                if str(receipt.get("status") or "") != "completed":
                    raise RuntimeError("GPTAdmin agent job returned a non-terminal result")
            else:
                raise RuntimeError(f"channel adapter is not configured: {delivery['channel']}")
            self._center.complete_delivery(
                delivery["id"], "sent",
                claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
            )
        except Exception as error:
            from .incident_call_chain import metadata
            if intended(delivery) or call_reserved:
                self._center.complete_delivery(delivery['id'],'uncertain' if call_reserved else 'failed',
                    'Звонок не подтверждён; автоматическое повторение остановлено',
                    claimed_at=delivery.get('claimed_at'),attempt=delivery.get('attempt'),
                    result={'answered':None,'uncertain':True,'error':type(error).__name__})
                return
            from .source_gate import HealthSourceGateBlocked
            if isinstance(error, HealthSourceGateBlocked):
                status = "human_stopped" if error.reason == "human_stop_held" else "source_blocked"
                self._center.schedule_health_outcome(str(delivery["incident_id"]), status, f"agent-result:{delivery['id']}")
                self._center.complete_delivery(delivery["id"], "cancelled", str(error), claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"))
                return
            if str(delivery["channel"]) in {"gptadmin.agent:health-diagnosis", "gptadmin.agent:health-remediation"}:
                self._center.schedule_health_outcome(str(delivery["incident_id"]), "interrupted", f"agent-interrupted:{delivery['id']}")
            delay = min(300, 5 * (2 ** min(int(delivery["attempt"]), 6)))
            self._center.complete_delivery(
                delivery["id"],
                "retry",
                str(error),
                delay,
                claimed_at=delivery.get("claimed_at"),
                attempt=delivery.get("attempt"),
            )

    def run_once(self) -> int:
        """Deliver a bounded batch and retry failures; returns claims processed."""
        self.replay_human_completions()
        # The task-question clock and durable controls use this existing worker
        # tick. Missing action-owner integration leaves revocation pending.
        self._center.task_decisions.resolve_due()
        self._dispatch_task_controls()
        deliveries = self.claim_due()
        for delivery in deliveries:
            self.deliver(delivery)
        return len(deliveries)

    def _dispatch_task_controls(self, limit: int = 20) -> int:
        adapter = self._task_action_owner
        if adapter is None:
            return 0
        acknowledged = 0
        for request_id in self._center.task_decisions.pending_controls(limit):
            previous = self._center.task_decisions.control_command(request_id)
            recovering = previous["state"] in {"sending", "unknown"}
            control = self._center.task_decisions.claim_control(request_id)
            if control is None:
                continue
            command = self._center.task_decisions.control_command(request_id)
            if recovering:
                query = getattr(adapter, "query_control", None)
                if not callable(query):
                    self._center.task_decisions.mark_control_unknown(request_id)
                    continue
                try:
                    status = query(command["control_id"])
                except Exception:
                    self._center.task_decisions.mark_control_unknown(request_id)
                    continue
                if isinstance(status, Mapping) and status.get("status") == "acknowledged":
                    receipt = status
                elif isinstance(status, Mapping) and status.get("status") == "not_found" and status.get("retry_safe") is True:
                    receipt = None
                else:
                    # A pending/unknown owner result is not authority to repeat
                    # a potentially accepted revocation.
                    self._center.task_decisions.mark_control_unknown(request_id)
                    continue
            else:
                receipt = None
            if receipt is None:
                try:
                    receipt = adapter.revoke(command, command["control_id"])
                except Exception:
                    self._center.task_decisions.mark_control_unknown(request_id)
                    continue
            if not isinstance(receipt, Mapping) or receipt.get("status") != "acknowledged":
                self._center.task_decisions.mark_control_unknown(request_id)
                continue
            try:
                self._center.task_decisions.acknowledge_control(
                    request_id, control_id=command["control_id"],
                    action_owner=str(getattr(adapter, "owner_id", "action-owner")),
                    receipt_ref=str(receipt.get("receipt_ref") or ""),
                    effective_epoch=int(receipt.get("effective_epoch") or -1),
                    accepted_effects=receipt.get("accepted_effects") if isinstance(receipt.get("accepted_effects"), list) else [],
                    unknown_effects=receipt.get("unknown_effects") if isinstance(receipt.get("unknown_effects"), list) else [],
                )
            except (TypeError, ValueError, ValidationError):
                self._center.task_decisions.mark_control_unknown(request_id)
                continue
            acknowledged += 1
        return acknowledged

    def replay_human_completions(self, limit: int = 8) -> int:
        """Re-send durable native resumes that Herder definitively did not accept.

        This is the same worker tick, not a second scheduler: it uses the same
        durable rows the answer wrote, and an unknown admission is never replayed.
        """
        replayed = 0
        for pending in self._center.due_human_completions(limit=limit):
            try:
                request = self._center.get_human_request_from_telegram(str(pending["request_id"]))
            except ValidationError:
                self._center.mark_human_completion(str(pending["request_id"]), "unknown", "question is gone")
                self._center.mark_human_completion_card(str(pending["request_id"]), "skipped")
                continue
            if deliver_human_completion(self._center, request, str(pending.get("actor") or "worker"))["status"] in {"sent", "resuming", "resumed"}:
                replayed += 1
        for pending in self._center.due_human_completion_cards(limit=limit):
            try:
                request = self._center.get_human_request_from_telegram(str(pending["request_id"]))
            except ValidationError:
                self._center.mark_human_completion_card(str(pending["request_id"]), "skipped")
                continue
            _deliver_human_completion_card(self._center, request, str(pending.get("actor") or "worker"), source="worker")
        return replayed


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """Read one bounded JSON object; raises ValidationError for malformed input."""
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0 or length > 128_000:
        raise ValidationError("JSON body must be between 1 and 128000 bytes")
    try:
        value = json.loads(handler.rfile.read(length))
    except json.JSONDecodeError as error:
        raise ValidationError("invalid JSON body") from error
    if not isinstance(value, dict):
        raise ValidationError("JSON body must be an object")
    return value


def _bearer(handler: BaseHTTPRequestHandler) -> str:
    """Extract a bearer token; raises AuthorizationError when absent or malformed."""
    header = handler.headers.get("Authorization") or ""
    prefix, _, token = header.partition(" ")
    if prefix.lower() != "bearer" or not token:
        raise AuthorizationError("Bearer authorization is required")
    return token


def record_incident_choice_result(
    center: NotificationCenter,
    token: str,
    incident_id: str,
    body: Mapping[str, Any],
    api: Any | None = None,
) -> dict[str, Any]:
    """Record one canonical choice answer for an incident's choice card.

    Any resolver may call this — Telegram, voice, phone, or API. The incident's
    own choice presentation decides the accepted option, so a resolver cannot
    mark a card with a value that never belonged to it, and the first accepted
    answer stays canonical on a later conflicting repeat.
    """
    center.authorize_incident(token, incident_id)
    request_id = str(body.get("request_id") or "").strip()
    requested = str(body.get("choice_id") or "").strip()
    actor = str(body.get("actor") or "api").strip()[:128]
    source = str(body.get("source") or "api").strip()[:32]
    if not request_id or not requested:
        raise ValidationError("choice result requires request_id and choice_id")
    presentation = center.latest_choice_request(incident_id)
    if presentation is None or str(presentation["choice_request_id"]) != request_id:
        raise ValidationError("choice request does not belong to this incident")
    if center.get_incident(incident_id)['state'] == 'resolved' and center.selection_receipt(request_id) is None:
        return {'schema': 'noticeplace.choice_result.v1', 'incident_id': incident_id, 'request_id': request_id,
                'answer_status': 'no_accept', 'accepted': False, 'choice_id': None, 'label': None,
                'native': {'status': 'not_attempted'}, 'card': {'edited': False, 'status': 'skipped'}}
    selection = center.get_telegram_choice(incident_id, requested)
    canonical_id = canonical_choice_ref(str(selection["choice_id"]))
    label = str(selection["label"])
    card = center.original_telegram_card(incident_id)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if card is None or not bot_token:
        # The canonical answer is still recorded; only the card edit waits for
        # a confirmed Telegram delivery receipt.
        receipt = center.record_selection_receipt(
            request_id=request_id, incident_id=incident_id, choice_id=canonical_id,
            label=label, actor=actor, source=source,
        )
        applied: dict[str, Any] = {"selection": receipt, "card": None, "status": "card_is_not_bound"}
    else:
        codec = telegram_action_codec_from_environment()
        if codec is None:
            raise ValidationError("Telegram choice callbacks are not configured")
        from .incident_card import render
        current_text = render(center.incident_card_payload(incident_id), codec)[0]
        applied = apply_selection_receipt(
            center,
            api or (lambda method, body_: telegram_api(bot_token, method, body_)),
            request_id=request_id,
            incident_id=incident_id,
            choice_id=canonical_id,
            label=label,
            actor=actor,
            source=source,
            chat_id=str(card["chat_id"]),
            message_id=int(card["message_id"]),
            base_text=current_text,
            choice_buttons=[{"choice_id": str(choice["choice_id"]), "label": str(choice["label"]),
                             "callback_data": codec.encode("choice", incident_id, choice_id=str(index))}
                            for index, choice in enumerate(presentation["choices"])],
            reply_markup={'inline_keyboard': incident_controls(codec, center.get_incident(incident_id), center.latest_agent_session(incident_id))},
        )
    stored = applied.get("selection") if isinstance(applied.get("selection"), Mapping) else {}
    # The first accepted answer stays canonical, so a later conflicting
    # resolver always sees and confirms the already accepted option.
    accepted_id = str(stored.get("choice_id") or canonical_id)
    accepted_label = str(stored.get("label") or label)
    return {
        "schema": "noticeplace.choice_result.v1",
        "incident_id": incident_id,
        "request_id": request_id,
        "choice_id": accepted_id,
        "label": accepted_label,
        "requested_choice_id": canonical_id,
        "accepted": bool(stored.get("accepted")),
        "conflict": bool(stored.get("conflict")),
        "card": applied.get("card"),
        "card_status": applied.get("status", "marked"),
    }


def complete_human_request(
    center: NotificationCenter,
    request: Mapping[str, Any],
    value: str,
    actor: str,
    source: str,
    api: Any | None = None,
    base_text: str = "",
    entities: Any = None,
    chat_id: str = "",
    message_id: Any = None,
    runner: Any = urllib.request.urlopen,
    choice_codec: TelegramActionCodec | None = None,
) -> dict[str, Any]:
    """One canonical completion for every answer surface.

    HTTP, a Telegram button, and a free-text reply all end here. The order is
    deliberate: the answer is stored first, the owed native resume is recorded
    durably before any Telegram work, and only then is the card rendered. A card
    failure therefore never blocks the native session, and a Herder outage is
    replayed by the existing delivery worker with the same canonical reference.
    """
    request_id = str(request.get("request_id") or "")
    answer = str(value or "").strip()
    if not request_id or not answer:
        raise ValidationError("completion requires a request id and an answer")
    # Re-read under the answer transaction. The handler's snapshot can already
    # be stale when another surface wins, expires or cancels this question.
    resolved = center.resolve_human_request_for_surface(request_id, actor, answer)
    was_pending = bool(resolved.get("answer_accepted"))
    state = str(resolved.get("state") or "")
    response_value = resolved.get("response_value")
    if state != "resolved" or not response_value:
        # Expired or cancelled without an answer: accept nothing, invent nothing.
        return {
            "request_id": request_id, "state": state, "answer_status": "no_accept",
            "value": None, "label": None, "card": "skipped", "native": {"status": "not_attempted"},
        }
    canonical_value = str(response_value)
    canonical_actor = str(resolved.get("response_actor") or actor)
    card: dict[str, Any] = {"status": "skipped"}
    legacy_analysis = canonical_value == "deep_analysis" and not resolved.get("native_harness")
    if (was_pending or center.human_request_completion(request_id)
            or (legacy_analysis and not resolved.get("telegram_progress_confirmed_at"))):
        card = _deliver_human_completion_card(
            center, resolved, canonical_actor, api, base_text=base_text, entities=entities,
            chat_id=chat_id, message_id=message_id, source=source, choice_codec=choice_codec)
    label = next(
        (str(choice.get("label") or "") for choice in (resolved.get("choices") or [])
         if isinstance(choice, Mapping) and str(choice.get("value") or "") == canonical_value),
        "",
    )
    native = ({'status': 'not_attempted', 'reason': 'incident_closed'} if resolved.get('incident_closed')
              else deliver_human_completion(center, resolved, canonical_actor, runner))
    return {
        "request_id": request_id,
        "state": state,
        "answer_status": "accepted",
        "accepted": was_pending,
        "conflict": canonical_value != answer,
        "value": None if legacy_analysis else canonical_value,
        "label": label or None,
        "card": card.get("status", "skipped"),
        "native": native,
    }


def _deliver_human_completion_card(center: NotificationCenter, request: Mapping[str, Any], actor: str,
                                  api: Any | None = None, **kwargs: Any) -> dict[str, Any]:
    # Use the existing send lock to exclude another worker/answer while editing
    # this same card. Native delivery has its own durable admission fence.
    with center.delivery_send_lock():
        pending = center.human_request_completion(str(request["request_id"]))
        if pending.get("card_state") == "sent":
            return {"status": "already_marked"}
        if pending and float(pending["card_next_attempt_at"]) > time.time():
            return {"status": "retryable"}
        try:
            card = mark_human_request_resolved_card(center, request, actor, api, **kwargs)
        except Exception:  # noqa: BLE001 - editing must never suppress native continuation
            card = {"status": "failed"}
        center.mark_human_completion_card(str(request["request_id"]), str(card.get("status") or "failed"))
        return card


def deliver_human_completion(
    center: NotificationCenter,
    request: Mapping[str, Any],
    actor: str,
    runner: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Attempt one owed native resume and classify the outcome durably.

    A definite rejection retries from the durable row; an unknown admission is
    final, because replaying it could resume the session twice.
    """
    request_id = str(request.get("request_id") or "")
    pending = center.human_request_completion(request_id)
    if not pending:
        return {"status": "not_bound"}
    if str(pending.get("state")) in {"sent", "unknown", "sending", 'cancelled'}:
        return {"status": str(pending["state"]), "result_ref": str(pending.get("result_ref") or "")}
    if not center.claim_human_completion(request_id):
        return {"status": str(center.human_request_completion(request_id).get("state") or "unknown")}
    from .agent_job_helper import NativeCompletionUnavailable, resume_bound_native_session

    try:
        outcome = resume_bound_native_session(
            request_id, str(pending["harness"]), str(pending["session_id"]), str(pending["cwd"]),
            str(pending["result_ref"]), runner,
        )
    except NativeCompletionUnavailable as error:
        center.mark_human_completion(request_id, "retryable", str(error))
        return {"status": "retryable", "reason": str(error)[:200]}
    except Exception:  # noqa: BLE001 - timeout/malformed/lost response may follow admission
        center.mark_human_completion(request_id, "unknown", "native completion outcome is unknown")
        return {"status": "unknown"}
    status = str(outcome.get("status") or "")
    if outcome.get("in_flight") or status in {"in_flight", "admission_unknown", "admissionUnknown"}:
        center.mark_human_completion(request_id, "unknown", "native admission is unknown")
        return {"status": "unknown"}
    if status in {"resuming", "resumed"}:
        center.mark_human_completion(request_id, "sent", "")
        return {"status": status, "result_ref": str(pending.get("result_ref") or "")}
    reason = str(outcome.get("reason") or "")
    if (status == "pending" and outcome.get("retry") is True) or (
        status == "resume_failed" and outcome.get("retry") is True
        and reason in {"native-unavailable", "agent-resume:native-unavailable"}
    ):
        center.mark_human_completion(request_id, "retryable", reason)
        return {"status": "retryable", "result_ref": str(pending["result_ref"])}
    center.mark_human_completion(request_id, "unknown", status)
    return {"status": "unknown", "reason": status}


def mark_human_request_resolved_card(
    center: NotificationCenter,
    request: Mapping[str, Any],
    actor: str,
    api: Any | None = None,
    base_text: str = "",
    entities: Any = None,
    chat_id: str = "",
    message_id: Any = None,
    source: str = "api",
    choice_codec: TelegramActionCodec | None = None,
) -> dict[str, Any]:
    """Mark the same card for any non-Telegram resolver of a human request.

    Voice, phone, and API answers share the receipt-driven updater with the
    Telegram poller, so the operator always sees one accepted answer on the one
    message that asked the question.
    """
    if request.get("kind", "user") != "user":
        return {"status": "skipped"}
    request_id = str(request.get("request_id") or "")
    bound_chat = chat_id or str(request.get("telegram_chat_id") or "")
    bound_message = message_id if isinstance(message_id, int) else request.get("telegram_message_id")
    if not request_id or not bound_chat or not isinstance(bound_message, int) or bound_message <= 0:
        return {"status": "card_is_not_bound"}
    chat_id, message_id = bound_chat, bound_message
    value = str(request.get("response_value") or "")
    if not value:
        return {"status": "skipped"}
    choices = request.get("choices") if isinstance(request.get("choices"), list) else []
    label = next(
        (str(choice.get("label") or "") for choice in choices
         if isinstance(choice, Mapping) and str(choice.get("value") or "") == value),
        "",
    )
    label = sanitize_bounded_text(label or value, 128)
    if not label:
        return {"status": "empty_answer"}
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if api is None and not bot_token:
        # The in-process poller already owns a bound Bot API client.
        return {"status": "not_configured"}
    if value == "deep_analysis" and not request.get("native_harness"):
        # Preserve the existing legacy analysis visibility gate. It uses the
        # same canonical resolution, then confirms acceptance on this card.
        from .human_request_progress import edit_progress_card
        if request.get("telegram_progress_confirmed_at"):
            return {"status": "already_marked"}
        edit_progress_card(request, api or (lambda method, body: telegram_api(bot_token, method, body)), center=center)
        center.confirm_human_request_progress_card(request_id)
        return {"status": "marked"}
    rendered = base_text or render_human_request_original({**request, "state": "pending"})[0]
    extra_keyboard = None
    if request.get('incident_id'):
        from .incident_card import render
        codec = choice_codec or telegram_action_codec_from_environment()
        payload = center.incident_card_payload(str(request['incident_id']))
        payload['human_question'] = {**request, 'state': 'pending'}
        rendered = render(payload, codec)[0]
        entities = None
        extra_keyboard = render({**payload, 'human_question': request}, codec)[1]
    buttons = None
    if choices and not request.get('incident_id'):
        codec = choice_codec or telegram_action_codec_from_environment()
        if codec is None:
            return {"status": "not_configured"}
        buttons = [{"choice_id": canonical_choice_ref(str(choice["value"])), "label": str(choice["label"]),
                    "callback_data": codec.encode_human_choice(request_id, str(index))}
                   for index, choice in enumerate(choices)]
    applied = apply_selection_receipt(
        center,
        api or (lambda method, body: telegram_api(bot_token, method, body)),
        request_id=request_id,
        incident_id=request_id,
        choice_id=canonical_choice_ref(value),
        label=label,
        actor=actor,
        source=source,
        base_text=rendered,
        reply_markup=extra_keyboard,
        choice_buttons=buttons,
        entities=entities,
        chat_id=chat_id,
        message_id=message_id,
    )
    card = applied.get("card") if isinstance(applied.get("card"), Mapping) else {}
    # Report what Telegram actually did. An identical edit already proves the
    # marker is on the card; a keyboard-only fallback removed the buttons but is
    # not a confirmed checkmark and must not claim one.
    card_status = "marked" if card.get("edited") else str(card.get("reason") or "not_edited")
    if card.get("reason") == "already_marked":
        card_status = "already_marked"
    return {"status": card_status, "card": {"edited": bool(card.get("edited"))}, "selection": applied.get("selection")}


def resume_human_request_native_session(
    center: NotificationCenter,
    request: Mapping[str, Any],
    actor: str,
    runner: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Backwards-compatible wrapper around the durable completion delivery."""
    outcome = deliver_human_completion(center, request, actor, runner)
    return {} if outcome.get("status") == "not_bound" else outcome


def send_human_request_telegram(
    center: NotificationCenter,
    producer_token: str,
    request: Mapping[str, Any],
    api: Any | None = None,
) -> dict[str, Any]:
    """Send one AskHuman request through the service's existing Telegram bot."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    default_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not default_chat_id:
        return {"status": "not_configured"}
    is_task_decision = isinstance(request.get("task_decision"), Mapping)
    if is_task_decision and not center.task_decisions.reserve_notification(str(request["request_id"])):
        state = center.task_decisions.notification_state(str(request["request_id"]))
        if state["state"] == "sent" and request.get("telegram_chat_id") and request.get("telegram_message_id"):
            return {"status": "sent", "chat_id": request["telegram_chat_id"], "message_id": request["telegram_message_id"]}
        return {"status": "unknown" if state["state"] in {"sending", "unknown"} else "not_sent"}
    if request.get('incident_id') and request.get('kind', 'user') == 'user':
        # Questions share the incident's card, even if its first creation is
        # delayed or uncertain. No private-card fallback may bypass that fence.
        with center.delivery_send_lock():
            card = center.original_telegram_card(str(request['incident_id']))
            if card is None:
                with center._connection:
                    center.queue_incident_card_refresh(str(request['incident_id']))
                return {'status': 'queued', 'incident_id': request['incident_id']}
            center.bind_human_request_message(producer_token, str(request['request_id']), card['chat_id'], card['message_id'])
            center.queue_incident_card_refresh(str(request['incident_id']))
            return {'status': 'queued', **card, 'edited_in_place': True}
    destination: dict[str, str] = {"chat_id": default_chat_id}
    raw_routes = center.get_runtime_setting("telegram_topics_json")
    if raw_routes:
        try:
            routes = json.loads(raw_routes)
        except json.JSONDecodeError:
            routes = {}
        project_route = routes.get(str(request.get("project") or "")) if isinstance(routes, dict) else None
        if isinstance(project_route, dict):
            if project_route.get("enabled") is False:
                raise RuntimeError("Telegram HumanRequest project route is disabled")
            route_chat_id = str(project_route.get("chat_id") or "").strip()
            route_thread_id = project_route.get("message_thread_id")
            if not re.fullmatch(r"-?[0-9]+", route_chat_id):
                raise RuntimeError("Telegram HumanRequest project route has an invalid chat_id")
            if type(route_thread_id) is not int or route_thread_id <= 0:
                raise RuntimeError("Telegram HumanRequest project route has an invalid message_thread_id")
            destination = {
                "chat_id": route_chat_id,
                "message_thread_id": str(route_thread_id),
            }
    card_text, document_text = render_human_request_original(request)
    payload: dict[str, Any] = {**destination, "text": card_text}
    mode = str(request["mode"])
    if mode == "choice":
        secret = os.environ.get("TELEGRAM_CALLBACK_SECRET", "").strip()
        if not secret:
            raise RuntimeError("TELEGRAM_CALLBACK_SECRET is required for AskHuman choices")
        codec = TelegramActionCodec(secret)
        rows = [
            [{"text": str(choice["label"])[:80], "callback_data": codec.encode_human_choice(str(request["request_id"]), str(index))}]
            for index, choice in enumerate(request.get("choices") or [])
        ]
        payload["reply_markup"] = json.dumps({"inline_keyboard": rows}, ensure_ascii=False, separators=(",", ":"))
    elif mode == "question":
        payload["reply_markup"] = json.dumps({"force_reply": True, "selective": True}, separators=(",", ":"))
    try:
        response = (api or (lambda method, body: telegram_api(bot_token, method, body)))("sendMessage", payload)
    except Exception:
        if is_task_decision:
            center.task_decisions.mark_notification_unknown(str(request["request_id"]))
        raise
    message = response.get("result") if isinstance(response, dict) else None
    message_id = message.get("message_id") if isinstance(message, dict) else None
    response_chat_id = str(message.get("chat", {}).get("id") or destination["chat_id"]) if isinstance(message, dict) else destination["chat_id"]
    if not isinstance(message_id, int) or message_id <= 0:
        if is_task_decision:
            center.task_decisions.mark_notification_unknown(str(request["request_id"]))
        raise RuntimeError("Telegram AskHuman sendMessage returned no message id")
    center.bind_human_request_message(
        producer_token, str(request["request_id"]), response_chat_id, message_id
    )
    receipt = {"status": "sent", "chat_id": response_chat_id, "message_id": message_id}
    if document_text is not None:
        center.set_human_request_document_state(producer_token, str(request["request_id"]), "sending")
        try:
            document = TelegramSender(bot_token, response_chat_id).send_document(
                {**destination, "chat_id": response_chat_id},
                _human_request_document_filename(str(request["request_id"])),
                document_text,
                "📎 Полное сообщение и разбор",
                message_id,
            )
        except Exception:
            center.set_human_request_document_state(producer_token, str(request["request_id"]), "uncertain")
            raise
        document_message_id = int(document["message_id"])
        document_file_id = document.get("file_id")
        center.bind_human_request_document(
            producer_token,
            str(request["request_id"]),
            document_message_id,
            str(document_file_id) if document_file_id is not None else None,
        )
        receipt["document_message_id"] = document_message_id
        receipt["document_filename"] = str(document["filename"])
        if isinstance(document_file_id, str) and document_file_id:
            receipt["document_file_id"] = document_file_id
    return receipt


def refresh_human_request_original(
    center: NotificationCenter, token: str, request_id: str, original_text: str,
    actor: str = "operator", api: Any | None = None,
) -> dict[str, Any]:
    """Enrich and edit the existing card; never create a second decision request."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValidationError("Telegram HumanRequest delivery is not configured")
    send = api or (lambda method, body: telegram_api(bot_token, method, body))
    with center.delivery_send_lock():
        request = center.enrich_human_request_original(token, request_id, original_text, actor)
        card_text, document_text = render_human_request_original(request)
        if request.get('incident_id'):
            document_text = None
        if document_text is not None and request.get("telegram_document_message_id") is None and request.get("telegram_document_state") is not None:
            raise ValidationError("human request document outcome requires reconciliation")
        chat_id, message_id = str(request["telegram_chat_id"]), int(request["telegram_message_id"])
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": card_text}
        rows = []
        if request.get("progress"):
            from .human_request_progress import progress_keyboard
            rows = progress_keyboard(request)["inline_keyboard"]
        if request['mode'] == 'choice':
            from .human_request_progress import progress_keyboard
            if not os.environ.get('TELEGRAM_CALLBACK_SECRET', '').strip():
                raise ValidationError('Telegram callback secret is not configured')
            rows = progress_keyboard(request)['inline_keyboard']
        if request["mode"] == "choice" or request["state"] != "pending":
            payload["reply_markup"] = json.dumps({"inline_keyboard": rows}, ensure_ascii=False)
        if request.get('incident_id'):
            from .incident_card import render
            projection = center.incident_card_payload(str(request['incident_id']))
            projection['human_question'] = request
            text, keyboard = render(projection, telegram_action_codec_from_environment())
            payload['text'] = text
            payload['reply_markup'] = json.dumps(keyboard, ensure_ascii=False)
        try:
            response = send("editMessageText", payload)
        except Exception as error:
            description = str(error)
            if isinstance(error, urllib.error.HTTPError) and error.code == 400:
                try:
                    failure = json.loads(error.read(4096))
                    if failure.get("ok") is False and failure.get("error_code") == 400:
                        description = str(failure.get("description") or "")
                except (ValueError, AttributeError):
                    pass
            if not description.lower().startswith("bad request: message is not modified"):
                raise
            response = {"ok": True, "result": {"message_id": message_id, "chat": {"id": chat_id}}}
        edited = response.get("result") if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get("ok") is not True
                or not isinstance(edited, dict) or edited.get("message_id") != message_id
                or str(edited.get("chat", {}).get("id")) != chat_id):
            raise RuntimeError("Telegram original edit returned a mismatched card receipt")
        if request.get("response_value") == "deep_analysis":
            center.confirm_human_request_progress_card(request_id)
        if document_text is not None and request.get("telegram_document_message_id") is None:
            target = {"chat_id": chat_id}
            thread_id = edited.get("message_thread_id")
            if type(thread_id) is int and thread_id > 0:
                target["message_thread_id"] = str(thread_id)
            else:
                chat = edited.get("chat", {})
                if chat.get("type") not in {"private", "group"} and not (
                    chat.get("type") == "supergroup" and chat.get("is_forum") is False
                ):
                    raise ValidationError("original topic receipt is required before document backfill")
            if not center.reserve_human_request_document(token, request_id):
                return _human_request_http_result(center.get_human_request(token, request_id))
            try:
                receipt = TelegramSender(bot_token, chat_id).send_document(
                    target, _human_request_document_filename(request_id), document_text,
                    "📎 Полное сообщение и разбор", message_id,
                )
                center.bind_human_request_document(token, request_id, int(receipt["message_id"]), receipt.get("file_id"))
            except Exception:
                center.set_human_request_document_state(token, request_id, "uncertain")
                raise
        return _human_request_http_result(center.get_human_request(token, request_id))


def _human_request_http_result(request: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the document requirement computed from the persisted card inputs."""
    result = NotificationCenter.human_request_producer_view(request)
    result["telegram_document_required"] = render_human_request_original(result)[1] is not None
    return result


def refresh_human_request_progress(
    center: NotificationCenter, token: str, request_id: str, update: Mapping[str, Any], api: Any | None = None,
) -> dict[str, Any]:
    """Persist then edit; an uncertain edit is safely retried on the same card."""
    from .human_request_progress import edit_progress_card
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValidationError("Telegram HumanRequest delivery is not configured")
    send = api or (lambda method, body: telegram_api(bot_token, method, body))
    with center.delivery_send_lock():
        request = center.update_human_request_progress(token, request_id, update)
        if request.get('incident_id'):
            center.queue_incident_card_refresh(str(request['incident_id']))
            return _human_request_http_result(request)
        edit_progress_card(request, send, center=center)
        request = center.confirm_human_request_progress_card(request_id)
        return _human_request_http_result(request)


def build_handler(center: NotificationCenter, health_token: str, mcp_token: str | None = None, *, producer_only: bool = False) -> type[BaseHTTPRequestHandler]:
    """Build an HTTP handler bound to one center and two dedicated bearer tokens."""
    if not health_token:
        raise RuntimeError("NOTIFY_CENTER_HEALTH_TOKEN must be configured")
    configured_mcp_token = mcp_token if mcp_token is not None else os.environ.get("NOTIFY_MCP_TOKEN", "")
    if not producer_only and not configured_mcp_token:
        raise RuntimeError("NOTIFY_MCP_TOKEN must be configured")
    health_workflow = HealthWorkflow(center, os.environ.get("TELEGRAM_CALLBACK_SECRET", "").strip() or None)

    class ApiHandler(BaseHTTPRequestHandler):
        """Expose the v1 event and incident state-transition endpoints."""

        def _reply(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            """Write a JSON response with no cacheable credentials or state."""
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, status: HTTPStatus, body: bytes) -> None:
            """Serve the public landing page without any incident data or tokens."""
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _empty(self, status: HTTPStatus) -> None:
            """Return an empty response for MCP notifications without leaking state."""
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _mcp(self, payload: dict[str, Any]) -> None:
            """Return one MCP JSON-RPC response with the same manifest as stdio."""
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _request_meta(self) -> dict[str, str]:
            """Capture secret-safe ingress provenance behind explicitly trusted proxies."""
            peer_ip = str(self.client_address[0])
            trusted = {item.strip() for item in os.environ.get("NOTIFY_TRUSTED_PROXY_IPS", "127.0.0.1").split(",") if item.strip()}
            source_ip = peer_ip
            forwarded_for = self.headers.get("X-Forwarded-For", "").strip()
            real_ip = self.headers.get("X-Real-IP", "").strip()
            if peer_ip in trusted and real_ip:
                try:
                    ipaddress.ip_address(real_ip)
                except ValueError:
                    real_ip = ""
                if real_ip:
                    source_ip = real_ip
            return {
                "peer_ip": peer_ip,
                "source_ip": source_ip,
                "proxy_ip": peer_ip if source_ip != peer_ip else "",
                "forwarded_for": forwarded_for[:512],
            }

        def do_GET(self) -> None:
            """Serve authenticated health and incident reads."""
            parsed_path = urllib.parse.urlparse(self.path)
            if self.path == "/":
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/admin/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if self.path == "/v1/instructions":
                self._reply(HTTPStatus.OK, noticeplace_instructions())
                return
            if self.path == "/health":
                try:
                    token = _bearer(self)
                except AuthorizationError:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return
                if not secrets.compare_digest(token, health_token):
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return
                try:
                    health = center.health()
                except Exception:
                    self._reply(HTTPStatus.SERVICE_UNAVAILABLE, {"schema": "notify.health.v1", "service": "notification-center", "status": "degraded", "storage_ready": False, "dispatcher_ready": False})
                    return
                self._reply(HTTPStatus.OK if health["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE, health)
                return
            if parsed_path.path == "/v1/mutes":
                try:
                    project = str(urllib.parse.parse_qs(parsed_path.query).get("project", [""])[0]).strip()
                    if not project:
                        raise ValidationError("project is required")
                    center.authorize_project(_bearer(self), project)
                    self._reply(HTTPStatus.OK, {"project": project, "mutes": center.list_notification_mutes(project=project)})
                except AuthorizationError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                except ValidationError as error:
                    self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if self.path.startswith("/v1/incidents/"):
                try:
                    incident_id = self.path.rsplit("/", 1)[-1]
                    center.authorize_incident(_bearer(self), incident_id)
                    incident = center.get_incident(incident_id)
                    self._reply(HTTPStatus.OK if incident else HTTPStatus.NOT_FOUND, incident or {"error": "incident not found"})
                except NotificationCenterError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                return
            if parsed_path.path == "/v1/human-requests":
                try:
                    query = urllib.parse.parse_qs(parsed_path.query)
                    questions = center.list_human_requests(
                        _bearer(self),
                        str((query.get("state") or ["pending"])[0]),
                        int((query.get("limit") or ["20"])[0]),
                    )
                    self._reply(HTTPStatus.OK, {"schema": "ask_human.pending_list.v1", "questions": questions})
                except AuthorizationError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                except ValidationError as error:
                    self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if parsed_path.path.startswith("/v1/human-requests/") and parsed_path.path.endswith("/result"):
                try:
                    request_id = parsed_path.path[len("/v1/human-requests/"):-len("/result")]
                    self._reply(HTTPStatus.OK, center.human_request_result_receipt(_bearer(self), request_id))
                except AuthorizationError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                except ValidationError as error:
                    self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if self.path.startswith("/v1/human-requests/"):
                try:
                    request_id = self.path.rsplit("/", 1)[-1]
                    self._reply(HTTPStatus.OK, _human_request_http_result(
                        center.get_human_request(_bearer(self), request_id)
                    ))
                except AuthorizationError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                except ValidationError as error:
                    self._reply(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            """Accept events and explicit incident actions, returning JSON errors safely."""
            # A producer replica persists events; native workers retain card,
            # completion, MCP job and session delivery ownership.
            if producer_only and self.path != "/v1/events":
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                if self.path == "/mcp":
                    token = _bearer(self)
                    if not secrets.compare_digest(token, configured_mcp_token):
                        self._reply(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                        return
                    body = _json_body(self)
                    response = notify_mcp_dispatch(body)
                    if response is None:
                        self._empty(HTTPStatus.NO_CONTENT)
                        return
                    self._mcp(response)
                    return
                token = _bearer(self)
                body = _json_body(self)
                if self.path == "/v1/events":
                    key = self.headers.get("Idempotency-Key") or ""
                    if body.get("action") == "resolve":
                        self._reply(HTTPStatus.OK, center.resolve_event(token, key, body))
                        return
                    # Agent Herder owns the question binding and delivery, so a
                    # plain ingress event never becomes a second question card.
                    self._reply(HTTPStatus.ACCEPTED, center.create_event(token, key, body, request_meta=self._request_meta()))
                    return
                if self.path == "/v1/human-requests":
                    # Agent Herder dedups its retries with this key, so a repeat
                    # must describe exactly the same question.
                    key = self.headers.get("Idempotency-Key") or ""
                    request_id = str(body.get("request_id") or "")
                    if key and key != f"human-request:{request_id}":
                        raise IdempotencyConflict("Idempotency-Key does not match this human request id")
                    if not body.get("allowed_actors"):
                        configured_actors = {
                            item.strip()
                            for item in os.environ.get(
                                "TELEGRAM_CALLBACK_ALLOWED_USER_IDS", os.environ.get("TELEGRAM_CHAT_ID", "")
                            ).split(",")
                            if item.strip()
                        }
                        body["allowed_actors"] = [f"telegram:{actor}" for actor in sorted(configured_actors)]
                    created = center.create_human_request(token, body)
                    idempotent = bool(created["idempotent"])
                    if created["idempotent"]:
                        chat_id = str(created.get("telegram_chat_id") or "")
                        message_id = created.get("telegram_message_id")
                        document_message_id = created.get("telegram_document_message_id")
                        document_file_id = created.get("telegram_document_file_id")
                        if chat_id and isinstance(message_id, int) and message_id > 0:
                            created["telegram"] = {
                                "status": "sent", "chat_id": chat_id, "message_id": message_id,
                                **(
                                    {"document_message_id": document_message_id,
                                     "document_filename": _human_request_document_filename(str(created["request_id"])),
                                     **({"document_file_id": document_file_id}
                                        if isinstance(document_file_id, str) and document_file_id else {})}
                                    if isinstance(document_message_id, int) else {}
                                ),
                            }
                            if (
                                render_human_request_original(created)[1] is not None
                                and not isinstance(document_message_id, int)
                            ):
                                created["telegram"]["status"] = "uncertain"
                        else:
                            created["telegram"] = {"status": "not_configured"}
                    else:
                        created["telegram"] = send_human_request_telegram(center, token, created)
                    telegram_receipt = created["telegram"]
                    refreshed = center.get_human_request(token, str(created["request_id"]))
                    refreshed["idempotent"] = idempotent
                    refreshed["telegram"] = telegram_receipt
                    self._reply(HTTPStatus.CREATED, _human_request_http_result(refreshed))
                    return
                parts = self.path.split("/")
                if len(parts) == 5 and parts[:3] == ["", "v1", "human-requests"] and parts[4] == "result":
                    if set(body) - {"actor"}:
                        raise ValidationError("question result accepts no payload fields")
                    self._reply(HTTPStatus.OK, center.human_request_result_receipt(token, parts[3]))
                    return
                if len(parts) == 5 and parts[:3] == ["", "v1", "human-requests"]:
                    request_id, action = parts[3], parts[4]
                    if action == "original":
                        if set(body) - {"original_text", "actor"}:
                            raise ValidationError("original backfill accepts only original_text and actor")
                        result = refresh_human_request_original(
                            center, token, request_id, body.get("original_text"), str(body.get("actor") or "operator"),
                        )
                    elif action == "resolve":
                        actor_value = str(body.get("actor") or "api")
                        pending = center.get_human_request(token, request_id)
                        completed = complete_human_request(
                            center, pending, str(body.get("value") or ""), actor_value, "api")
                        result = {**center.get_human_request(token, request_id), **completed}
                    elif action == "progress":
                        result = refresh_human_request_progress(center, token, request_id, body)
                    elif action == "cancel":
                        reason = str(body.get("reason") or "")
                        if center.human_request_is_bound(request_id) and not reason:
                            raise ValidationError("cancelling a bound question requires an explicit reason")
                        result = center.cancel_human_request(token, request_id, str(body.get("actor") or "api"),
                                                             reason=reason or None)
                    else:
                        self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown human request action"})
                        return
                    self._reply(HTTPStatus.OK, NotificationCenter.human_request_producer_view(result))
                    return
                if len(parts) == 5 and parts[:3] == ["", "v1", "incidents"]:
                    incident_id, action = parts[3], parts[4]
                    center.authorize_incident(token, incident_id)
                    actor = str(body.get("actor") or "api")
                    if action == "choice-result":
                        self._reply(HTTPStatus.OK, record_incident_choice_result(center, token, incident_id, body))
                        return
                    if action == "ack":
                        self._reply(HTTPStatus.OK, center.acknowledge(incident_id, actor))
                    elif action == "resolve":
                        self._reply(HTTPStatus.OK, center.resolve(incident_id, actor))
                    elif action == "snooze":
                        self._reply(HTTPStatus.OK, center.snooze(incident_id, float(body.get("until_epoch") or 0), actor))
                    elif action in {"mute", "unmute"}:
                        self._reply(HTTPStatus.OK, center.set_incident_notifications_muted(incident_id, action == "mute", actor))
                    else:
                        self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown action"})
                    return
                if len(parts) == 6 and parts[:3] == ["", "v1", "incidents"] and parts[4] == "health":
                    incident_id, action = parts[3], parts[5]
                    center.authorize_incident(token, incident_id)
                    key = self.headers.get("Idempotency-Key") or ""
                    actor = str(body.get("actor") or "api")
                    if action == "plans":
                        plans = body.get("plans")
                        if not isinstance(plans, list):
                            raise ValidationError("health plans must be a JSON array")
                        result = health_workflow.attach_plans(
                            incident_id,
                            key,
                            plans,
                            actor=actor,
                            correlation_id=str(body.get("correlation_id") or "") or None,
                            trace_refs=body.get("trace_refs") if isinstance(body.get("trace_refs"), list) else [],
                            evidence_refs=body.get("evidence_refs") if isinstance(body.get("evidence_refs"), list) else [],
                            orchestration=body.get("orchestration") if isinstance(body.get("orchestration"), Mapping) else None,
                            recommendation=body.get("recommendation") if isinstance(body.get("recommendation"), Mapping) else None,
                        )
                        # A confident orchestrator recommendation executes the
                        # recommended plan immediately, exactly like a human
                        # tap. Fail-open: only an explicit needs_human=false
                        # with a pinned plan id auto-selects; any conflict
                        # leaves the operator-facing selection card untouched.
                        recommendation = normalize_health_recommendation(body.get("recommendation"))
                        if recommendation is not None and recommendation["needs_human"] is False and recommendation["plan_id"]:
                            try:
                                health_workflow.select_plan(
                                    incident_id,
                                    f"{incident_id}:health.plan:{recommendation['plan_id']}",
                                    recommendation["plan_id"],
                                    "gptadmin:health-orchestrator",
                                )
                                result["auto_selected_plan"] = recommendation["plan_id"]
                            except (ValidationError, IdempotencyConflict):
                                result["auto_selected_plan"] = ""
                    elif action == "select":
                        result = health_workflow.select_plan(incident_id, key, str(body.get("plan_id") or ""), actor)
                    elif action == "progress":
                        result = health_workflow.record_progress(
                            incident_id,
                            key,
                            plan_id=str(body.get("plan_id") or ""),
                            step=str(body.get("step") or ""),
                            evidence_refs=body.get("evidence_refs") if isinstance(body.get("evidence_refs"), list) else [],
                            progress_fingerprint=str(body.get("progress_fingerprint") or body.get("fingerprint") or ""),
                            heartbeat_at=float(body["heartbeat_at"]) if body.get("heartbeat_at") is not None else None,
                            actor=actor,
                        )
                    elif action == "session":
                        result = center.record_health_agent_session(
                            incident_id,
                            key,
                            plan_id=str(body.get("plan_id") or ""),
                            harness=str(body.get("harness") or ""),
                            session_id=str(body.get("session_id") or ""),
                            public_base_url=os.environ.get("AGENT_HERDER_PUBLIC_URL", "https://agent.bezrabotnyi.com"),
                            actor=actor,
                            stage=str(body.get("stage") or "health-remediation"),
                            model=str(body.get("model") or ""),
                        )
                    elif action == "verification":
                        result = health_workflow.record_verification(
                            incident_id,
                            key,
                            source_id=str(body.get("source_id") or ""),
                            verification_id=str(body.get("verification_id") or ""),
                            observed_state=str(body.get("observed_state") or ""),
                            evidence_refs=body.get("evidence_refs") if isinstance(body.get("evidence_refs"), list) else [],
                            fingerprint=str(body.get("fingerprint") or body.get("source_fingerprint") or "") or None,
                            actor=actor,
                        )
                    elif action == "resolve":
                        result = health_workflow.resolve(
                            incident_id,
                            str(body.get("source_id") or ""),
                            str(body.get("verification_id") or ""),
                            actor,
                            elapsed_ms=int(body.get("elapsed_ms") or 0),
                            trace_refs=body.get("trace_refs") if isinstance(body.get("trace_refs"), list) else [],
                            executor_plan_id=str(body.get("plan_id") or "") or None,
                        )
                    else:
                        self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown health action"})
                        return
                    self._reply(HTTPStatus.OK, result)
                    return
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except AuthorizationError as error:
                self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
            except IdempotencyConflict as error:
                self._reply(HTTPStatus.CONFLICT, {"error": str(error)})
            except ValidationError as error:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception as error:
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error", "type": error.__class__.__name__})

        def log_message(self, _format: str, *_args: object) -> None:
            """Suppress request logs; deployment should use structured service logs."""

    return ApiHandler


def run_http(center: NotificationCenter, host: str, port: int, health_token: str | None = None, mcp_token: str | None = None, *, producer_only: bool = False) -> None:
    """Run the blocking HTTP server with a mandatory dedicated health token."""
    configured_health_token = health_token if health_token is not None else os.environ.get("NOTIFY_CENTER_HEALTH_TOKEN", "")
    configured_mcp_token = mcp_token if mcp_token is not None else os.environ.get("NOTIFY_MCP_TOKEN", "")
    ThreadingHTTPServer((host, port), build_handler(center, configured_health_token, configured_mcp_token, producer_only=producer_only)).serve_forever()


def telegram_action_codec_from_environment() -> TelegramActionCodec | None:
    """Enable signed inline controls only when the dedicated callback secret exists."""
    secret = os.environ.get("TELEGRAM_CALLBACK_SECRET", "")
    return TelegramActionCodec(secret) if secret else None


def telegram_routes_from_environment() -> dict[str, dict[str, Any]]:
    """Load optional severity-to-chat/topic routing from one trusted config value."""
    raw = os.environ.get("TELEGRAM_SEVERITY_ROUTES_JSON", "").strip()
    if not raw:
        return {}
    try:
        routes = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("TELEGRAM_SEVERITY_ROUTES_JSON must be a JSON object") from error
    if not isinstance(routes, dict):
        raise RuntimeError("TELEGRAM_SEVERITY_ROUTES_JSON must be a JSON object")
    validated: dict[str, dict[str, Any]] = {}
    for severity, route in routes.items():
        if not isinstance(severity, str) or not isinstance(route, dict) or not str(route.get("chat_id") or "").strip():
            raise RuntimeError("Telegram severity route must contain a chat_id")
        if route.get("message_thread_id") is not None and (not isinstance(route["message_thread_id"], int) or route["message_thread_id"] <= 0):
            raise RuntimeError("Telegram message_thread_id must be a positive integer")
        validated[severity] = {"chat_id": str(route["chat_id"]).strip()}
        if route.get("message_thread_id") is not None:
            validated[severity]["message_thread_id"] = route["message_thread_id"]
    return validated


def telegram_active_modes_from_environment() -> set[str]:
    """Load the small operator-selected mode set from the service environment."""
    return telegram_active_modes(os.environ.get("TELEGRAM_ACTIVE_MODES_JSON", ""))


def telegram_from_environment(action_codec: TelegramActionCodec | None = None, center: NotificationCenter | None = None) -> TelegramSender:
    """Build the Telegram adapter from env vars without ever logging credentials."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    active_modes = telegram_active_modes_from_environment()
    configured_routes = telegram_routes_from_environment()
    topic_chat_id = next(
        (str(route["chat_id"]) for mode, route in configured_routes.items() if mode in active_modes and route.get("chat_id")),
        chat_id,
    )
    routes = telegram_routes_with_auto_topics(
        token,
        topic_chat_id,
        configured_routes,
        active_modes,
        os.environ.get("TELEGRAM_TOPIC_STATE_PATH", "/var/lib/notification-center/telegram-topics.json"),
        enabled=os.environ.get("TELEGRAM_AUTO_CREATE_TOPICS", "false").lower() in {"1", "true", "yes"},
    ) if token and topic_chat_id else configured_routes
    return TelegramSender(token, chat_id, action_codec=action_codec, severity_routes=routes, active_modes=active_modes, center=center)


def telegram_interactions_from_environment(center: NotificationCenter, codec: TelegramActionCodec | None) -> TelegramInteractionPoller | None:
    """Build the optional in-process Bot API callback poller from strict allowlists."""
    if codec is None:
        return None
    allowed = {part.strip() for part in os.environ.get("TELEGRAM_CALLBACK_ALLOWED_USER_IDS", os.environ.get("TELEGRAM_CHAT_ID", "")).split(",") if part.strip()}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    callback_url = os.environ.get("AGENT_HERDER_AUTOPILOT_CHOICE_CALLBACK_URL", "").strip()
    callback_token = os.environ.get("AGENT_HERDER_AUTOPILOT_CHOICE_CALLBACK_TOKEN", "").strip()
    choice_callback = None
    if callback_url and not callback_token and urllib.parse.urlparse(callback_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Agent Herder choice callback requires a token outside loopback")
    if callback_url:
        choice_callback = lambda request_id, choice_id, actor: agent_herder_choice_callback(callback_url, callback_token, request_id, choice_id, actor)
    inbox_sink = telegram_inbox_sink_from_environment()
    inbox_chat_ids = {
        part.strip()
        for part in os.environ.get("UNIVERSAL_INBOX_TELEGRAM_CHAT_IDS", "").split(",")
        if part.strip()
    }
    inbox_sender_ids = {
        part.strip()
        for part in os.environ.get("UNIVERSAL_INBOX_TELEGRAM_SENDER_IDS", "").split(",")
        if part.strip()
    }
    return TelegramInteractionPoller(
        center,
        token,
        allowed,
        codec,
        health_plan_codec=TelegramHealthPlanCodec(codec.secret),
        choice_callback=choice_callback,
        inbox_sink=inbox_sink,
        inbox_chat_ids=inbox_chat_ids,
        inbox_sender_ids=inbox_sender_ids or allowed,
    ) if token and allowed else None


def telegram_inbox_sink_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    runner: Any = urllib.request.urlopen,
):
    """Build one fixed loopback Universal Inbox ingress client for Telegram updates."""
    environment = environment or os.environ
    url = environment.get("UNIVERSAL_INBOX_INGRESS_URL", "").strip()
    token = environment.get("UNIVERSAL_INBOX_INGRESS_TOKEN", "").strip()
    if not url and not token:
        return None
    if not url or not token:
        raise RuntimeError("Universal Inbox ingress requires URL and token")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Universal Inbox ingress URL is invalid")
    timeout_seconds = float(environment.get("UNIVERSAL_INBOX_INGRESS_TIMEOUT_SECONDS", "8"))

    def send(payload: dict[str, object]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with runner(request, timeout=timeout_seconds) as response:
            if int(response.status) != 202:
                raise RuntimeError(f"Universal Inbox ingress returned HTTP {response.status}")
            try:
                result = json.loads(response.read())
            except json.JSONDecodeError as error:
                raise RuntimeError("Universal Inbox ingress returned invalid JSON") from error
        if not isinstance(result, dict) or not isinstance(result.get("event_id"), str):
            raise RuntimeError("Universal Inbox ingress returned an invalid receipt")
        return result

    return send


def matrix_call_from_environment() -> MatrixCallSender | None:
    """Build the optional remote MatrixRTC adapter without loading its credentials locally."""
    url = os.environ.get("MATRIX_CALL_URL", "")
    token = os.environ.get("MATRIX_CALL_TOKEN", "")
    return MatrixCallSender(url, token, float(os.environ.get("MATRIX_CALL_TIMEOUT_SECONDS", "150"))) if url or token else None


def message_adapters_from_environment(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build fixed message adapters; event producers never choose URLs or credentials."""
    environment = environment or os.environ
    adapters: dict[str, Any] = {}
    telegram_token = environment.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = environment.get("TELEGRAM_CHAT_ID", "").strip()
    if telegram_token:
        adapters["telegram.message"] = TelegramMessageSender(
            telegram_token,
            default_chat_id=telegram_chat_id,
            timeout_seconds=float(environment.get("TELEGRAM_MESSAGE_TIMEOUT_SECONDS", "8")),
        )
    elif telegram_chat_id:
        raise RuntimeError("Telegram message adapter requires a bot token")
    homeserver = environment.get("MATRIX_MESSAGE_HOMESERVER", "").strip()
    matrix_token = environment.get("MATRIX_MESSAGE_ACCESS_TOKEN", "").strip()
    room_id = environment.get("MATRIX_MESSAGE_ROOM_ID", "").strip()
    if homeserver or matrix_token or room_id:
        if not homeserver or not matrix_token or not room_id:
            raise RuntimeError("Matrix message adapter requires homeserver, access token, and room id")
        adapters["matrix.message"] = MatrixMessageSender(
            homeserver,
            matrix_token,
            default_room_id=room_id,
            timeout_seconds=float(environment.get("MATRIX_MESSAGE_TIMEOUT_SECONDS", "8")),
        )
    raw_webhooks = environment.get("NOTIFY_MESSAGE_WEBHOOKS_JSON", "").strip()
    if not raw_webhooks:
        return adapters
    try:
        webhooks = json.loads(raw_webhooks)
    except json.JSONDecodeError as error:
        raise RuntimeError("NOTIFY_MESSAGE_WEBHOOKS_JSON must be a JSON object") from error
    if not isinstance(webhooks, dict):
        raise RuntimeError("NOTIFY_MESSAGE_WEBHOOKS_JSON must be a JSON object")
    allowed = {"whatsapp.message", "vk.message"}
    for channel, config in webhooks.items():
        if channel not in allowed or not isinstance(config, dict):
            raise RuntimeError("unsupported message webhook adapter")
        url = str(config.get("url") or "").strip()
        token = str(config.get("token") or "").strip()
        if not url or not token:
            raise RuntimeError(f"{channel} webhook requires url and token")
        adapters[channel] = WebhookMessageSender(
            channel,
            url,
            token,
            timeout_seconds=float(config.get("timeout_seconds") or 8),
        )
    return adapters


def call_adapters_from_environment(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build fixed call bridges for generic `phone.call` and `whatsapp.call` policy steps."""
    environment = environment or os.environ
    raw = environment.get("NOTIFY_CALL_WEBHOOKS_JSON", "").strip()
    if not raw:
        return {}
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("NOTIFY_CALL_WEBHOOKS_JSON must be a JSON object") from error
    if not isinstance(configured, dict):
        raise RuntimeError("NOTIFY_CALL_WEBHOOKS_JSON must be a JSON object")
    allowed = {"phone.call", "telegram.call", "whatsapp.call"}
    adapters: dict[str, Any] = {}
    for channel, config in configured.items():
        if channel not in allowed or not isinstance(config, dict):
            raise RuntimeError("unsupported call webhook adapter")
        url = str(config.get("url") or "").strip()
        token = str(config.get("token") or "").strip()
        if not url or not token:
            raise RuntimeError(f"{channel} webhook requires url and token")
        adapters[channel] = WebhookCallSender(channel, url, token, timeout_seconds=float(config.get("timeout_seconds") or 150))
    return adapters


def gptadmin_agent_jobs_from_environment() -> dict[str, Any]:
    """Build fixed signed agent-job routes from one root-owned JSON setting."""
    raw = os.environ.get("NOTIFY_GPTADMIN_AGENT_JOBS_JSON", "").strip()
    if not raw:
        return {}
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("NOTIFY_GPTADMIN_AGENT_JOBS_JSON must be a JSON object") from error
    if not isinstance(configured, dict):
        raise RuntimeError("NOTIFY_GPTADMIN_AGENT_JOBS_JSON must be a JSON object")
    result: dict[str, GptAdminAgentJobAdapter] = {}
    for job_id, value in configured.items():
        if not isinstance(job_id, str) or not isinstance(value, dict):
            raise RuntimeError("each GPTAdmin agent job must be a named object")
        result[job_id] = GptAdminAgentJobAdapter(
            job_id,
            str(value.get("url") or ""),
            str(value.get("hmac_secret") or ""),
            float(value.get("timeout_seconds") or 90),
            float(value.get("poll_interval_seconds") or 1),
            float(value.get("stale_progress_seconds") or 120),
        )
    if "health-remediation" in result:
        result["health-remediation"] = DurableHealthRemediationAdapter(
            result["health-remediation"],
            DirectHealthRemediationAdapter(),
        )
    return result


def android_phone_from_environment() -> AndroidPhoneAdapter | GptAdminPhoneAdapter | AgentCallPhoneAdapter | None:
    """Build either the direct ADB adapter or the narrow fixed GPTAdmin call path."""
    agentcall_socket = os.environ.get("AGENTCALL_PHONE_SOCKET", "").strip()
    if agentcall_socket:
        if os.environ.get("NOTICEPLACE_PHONE_PEERS_JSON"):
            from .mesh_phone import PhoneMeshRequester
            requester = PhoneMeshRequester(
                json.loads(os.environ["NOTICEPLACE_PHONE_PEERS_JSON"]),
                os.environ["NOTICEPLACE_MESH_CORE_SOCKET"], os.environ["NOTICEPLACE_MESH_HOST_ID"],
                os.environ["NOTICEPLACE_MESH_CA"], os.environ["NOTICEPLACE_MESH_CERT"], os.environ["NOTICEPLACE_MESH_KEY"],
            )
            return AgentCallPhoneAdapter(agentcall_socket, float(os.environ.get("AGENTCALL_PHONE_TIMEOUT_SECONDS", "30")), requester=requester)
        return AgentCallPhoneAdapter(agentcall_socket, float(os.environ.get("AGENTCALL_PHONE_TIMEOUT_SECONDS", "30")))
    gptadmin_url = os.environ.get("GPTADMIN_ANDROID_PHONE_CALL_URL", "").strip()
    gptadmin_token = os.environ.get("GPTADMIN_ANDROID_PHONE_CALL_TOKEN", "").strip()
    serial = os.environ.get("ANDROID_ADB_SERIAL", "").strip()
    target = os.environ.get("ANDROID_TELEGRAM_TARGET", "").strip()
    if gptadmin_url or gptadmin_token:
        if serial or target:
            raise RuntimeError("GPTADMIN_ANDROID_PHONE_CALL_* cannot be combined with ANDROID_ADB_*/ANDROID_TELEGRAM_TARGET")
        if not gptadmin_url or not gptadmin_token:
            raise RuntimeError("GPTADMIN_ANDROID_PHONE_CALL_URL and GPTADMIN_ANDROID_PHONE_CALL_TOKEN must be configured together")
        return GptAdminPhoneAdapter(gptadmin_url, gptadmin_token, float(os.environ.get("GPTADMIN_ANDROID_PHONE_CALL_TIMEOUT_SECONDS", "20")))
    if not serial and not target:
        return None
    if not serial or not target:
        raise RuntimeError("ANDROID_ADB_SERIAL and ANDROID_TELEGRAM_TARGET must be configured together")
    labels = tuple(part.strip() for part in os.environ.get("ANDROID_TELEGRAM_CALL_LABELS", "Voice call,Call,Позвонить,Голосовой звонок").split(",") if part.strip())
    return AndroidPhoneAdapter(AndroidPhoneConfig(
        adb_path=os.environ.get("ANDROID_ADB_PATH", "/usr/local/bin/adb"),
        serial=serial,
        telegram_target=target,
        phone_number=os.environ.get("ANDROID_PHONE_TARGET", "").strip(),
        call_labels=labels,
        command_timeout_seconds=float(os.environ.get("ANDROID_ADB_TIMEOUT_SECONDS", "12")),
    ))
