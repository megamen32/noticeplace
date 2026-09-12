"""Stdlib HTTP API and Telegram worker for the notification-center MVP."""

from __future__ import annotations

import json
import hashlib
import ipaddress
import os
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
from .health_workflow import HealthWorkflow, TelegramHealthPlanCodec, health_plan_keyboard as health_plan_keyboard_cards, validate_health_plans
from .telegram_interactions import TelegramActionCodec, TelegramInteractionPoller, agent_herder_choice_callback, telegram_api
from mcp.notify_mcp import dispatch as notify_mcp_dispatch

ACTIVE_TELEGRAM_MODES = frozenset(("emergency", "important", "log"))
SUPPORTED_TELEGRAM_MODES = frozenset((*ACTIVE_TELEGRAM_MODES, "health"))


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
    every notification retains the non-blocking Ask action.
    """
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
            return {"inline_keyboard": buttons}
    ask = {"text": "Ask", "callback_data": action_codec.encode("ask", incident_id)}
    ai = {"text": "AI", "callback_data": action_codec.encode("ai", incident_id)}
    if str(incident["severity"]) != "critical":
        return {"inline_keyboard": [[ask, ai]]}
    return {"inline_keyboard": [
        [
            {"text": "ACK", "callback_data": action_codec.encode("ack", incident_id)},
            {"text": "Snooze 15m", "callback_data": action_codec.encode("snz", incident_id)},
        ],
        [ask, ai],
    ]}


def _health_plans_ready(payload: Mapping[str, Any]) -> bool:
    """Require the exact three validated plans before a health card can send."""
    try:
        validate_health_plans(payload.get("health_plans"))
    except ValidationError:
        return False
    return True


def telegram_destination(default_chat_id: str, severity_routes: dict[str, dict[str, Any]], incident: dict[str, Any], active_modes: set[str] | None = None) -> dict[str, str]:
    """Resolve an allowlisted severity route without trusting event routing data."""
    mode = telegram_mode(incident)
    if active_modes is not None and mode not in active_modes:
        return {}
    configured = severity_routes.get(mode, severity_routes.get(str(incident.get("severity") or ""), {}))
    if configured.get("enabled") is False:
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

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver one card and retain the Bot API message identity for audits."""
        if not self._token or not self._chat_id:
            raise RuntimeError("Telegram sender is not configured")
        incident = payload["incident"]
        note = str(incident.get("operator_note") or "").strip()
        note_block = f"\n\nПримечание: {note}" if note else ""
        health_plans = payload.get("health_plans")
        choice_options = payload.get("choices")
        health_outcome = payload.get("health_outcome")
        plan_block = ""
        if isinstance(health_plans, list) and health_plans:
            plan_lines = [
                f"{index}. {str(plan.get('title') or plan.get('plan_id') or '')[:128]} — {str(plan.get('summary') or '')[:256]}"
                for index, plan in enumerate(health_plans[:3], 1)
                if isinstance(plan, dict)
            ]
            if plan_lines:
                plan_block = "\n\nПланы:\n" + "\n".join(plan_lines)
        if isinstance(health_outcome, dict) and str(health_outcome.get("observed_state") or "") == "degraded":
            plan_id = str(health_outcome.get("plan_id") or "выбранный план")[:64]
            step = str(health_outcome.get("step") or "выполнено")[:128]
            text = (
                f"ЗДОРОВЬЕ · {incident['project']}\n\n"
                f"План {plan_id} выполнен, но источник всё ещё в состоянии деградации.\n\n"
                f"Выполненный шаг: {step}\n"
                "Инцидент остаётся открытым. Выберите или создайте план, который устранит оставшийся сигнал."
                f"\n\nИнцидент: {incident['id']}"
            )
        else:
            text = f"{str(incident['severity']).upper()} · {incident['project']}\n\n{incident['title']}\n\n{incident['body']}{plan_block}{note_block}\n\nIncident: {incident['id']}"
        destination = telegram_delivery_destination(self._chat_id, self._routes(), payload, self._active_modes)
        mode = telegram_mode(incident)
        if self._active_modes is not None and mode not in self._active_modes:
            raise RuntimeError(f"Telegram mode is inactive: {mode}")
        if not destination:
            if mode == "health":
                raise RuntimeError("Telegram health topic route is not configured")
            raise RuntimeError(f"Telegram destination is not configured: {mode}")
        request_data: dict[str, str] = {**destination, "text": text, "disable_web_page_preview": "true"}
        health_keyboard: dict[str, list[list[dict[str, str]]]] | None = None
        action_keyboard: dict[str, list[list[dict[str, str]]]] | None = None
        if isinstance(health_outcome, dict):
            pass
        elif self._action_codec is not None:
            if isinstance(health_plans, list) and health_plans:
                health_keyboard = health_plan_keyboard_cards(TelegramHealthPlanCodec(self._action_codec.secret), incident["id"], health_plans)
                request_data["reply_markup"] = json.dumps(health_keyboard, separators=(",", ":"))
            else:
                action_keyboard = telegram_inline_keyboard(self._action_codec, incident, choice_options if isinstance(choice_options, list) else None)
                request_data["reply_markup"] = json.dumps(action_keyboard, separators=(",", ":"))
        elif mode == "health":
            raise RuntimeError("Telegram health delivery requires signed plan callback configuration")
        data = urllib.parse.urlencode(request_data).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{self._token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")
            try:
                result = json.loads(response.read())
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError("Telegram returned invalid JSON") from error
        message = result.get("result") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(message, dict):
            raise RuntimeError("Telegram returned an invalid sendMessage response")
        message_id = message.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            raise RuntimeError("Telegram sendMessage response has no valid message_id")
        receipt: dict[str, Any] = {
            "message_id": message_id,
            "chat_id": str(message.get("chat", {}).get("id") or destination["chat_id"]) if isinstance(message.get("chat"), dict) else destination["chat_id"],
        }
        action_buttons = [
            button
            for row in (action_keyboard or {}).get("inline_keyboard", [])
            for button in row
            if isinstance(button, dict)
        ]
        ai_buttons = [button for button in action_buttons if str(button.get("text") or "") == "AI"]
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
            receipt["health_button_count"] = len(buttons)
            receipt["health_signed_callback_count"] = sum(
                1
                for button in buttons
                if codec is not None and codec.decode(str(button.get("callback_data") or "")) is not None
            )
        if isinstance(choice_options, list) and choice_options:
            buttons = [
                button
                for row in telegram_inline_keyboard(self._action_codec, incident, choice_options).get("inline_keyboard", [])
                for button in row
                if isinstance(button, dict)
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
        return receipt

    def edit_health_card(self, payload: dict[str, Any], message_id: int, chat_id: str) -> dict[str, Any]:
        """Edit an existing Health card in place with the signed three-plan keyboard."""
        if not self._token or not self._chat_id:
            raise RuntimeError("Telegram sender is not configured")
        if not self._action_codec:
            raise RuntimeError("Telegram health migration requires signed plan callback configuration")
        incident = payload["incident"]
        health_plans = payload.get("health_plans")
        if not _health_plans_ready(payload):
            raise RuntimeError("Health plans are not attached")
        plan_lines = [
            f"{index}. {str(plan.get('title') or plan.get('plan_id') or '')[:128]} — {str(plan.get('summary') or '')[:256]}"
            for index, plan in enumerate(health_plans[:3], 1)
            if isinstance(plan, dict)
        ]
        note = str(incident.get("operator_note") or "").strip()
        note_block = f"\n\nNote: {note}" if note else ""
        text = f"{str(incident['severity']).upper()} · {incident['project']}\n\n{incident['title']}\n\n{incident['body']}\n\nPlans:\n" + "\n".join(plan_lines) + f"{note_block}\n\nIncident: {incident['id']}"
        keyboard = health_plan_keyboard_cards(TelegramHealthPlanCodec(self._action_codec.secret), incident["id"], health_plans)
        request_data = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "text": text,
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(keyboard, separators=(",", ":")),
        }
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/editMessageText",
            data=urllib.parse.urlencode(request_data).encode(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")
            try:
                result = json.loads(response.read())
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError("Telegram returned invalid JSON") from error
        message = result.get("result") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(message, dict):
            raise RuntimeError("Telegram returned an invalid editMessageText response")
        edited_message_id = message.get("message_id")
        if not isinstance(edited_message_id, int) or edited_message_id <= 0:
            edited_message_id = message_id
        return {
            "message_id": edited_message_id,
            "chat_id": str(message.get("chat", {}).get("id") or chat_id) if isinstance(message.get("chat"), dict) else str(chat_id),
            "health_plan_ids": [str(plan.get("plan_id") or "").strip() for plan in health_plans[:3] if isinstance(plan, dict)],
            "health_button_count": 3,
            "health_signed_callback_count": 3,
            "edited_in_place": True,
        }

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
            text += f"\n\nSource: {source}"
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": text[:4096],
                "disable_web_page_preview": "true",
            }).encode(),
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
        return {"message_id": message_id, "chat_id": resolved_chat_id}


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

    def __init__(self, center: NotificationCenter, telegram: TelegramSender, matrix_call: MatrixCallSender | Any | None = None, android_phone: AndroidPhoneAdapter | AgentCallPhoneAdapter | Any | None = None, lease_seconds: float = 180, call_escalation_seconds: float = 0, android_telegram_call_escalation_seconds: float = 0, android_phone_call_escalation_seconds: float = 0, critical_repeat_seconds: float = 0, critical_call_escalation_seconds: float | None = None, emergency_call_escalation_seconds: float = 0, agent_jobs: dict[str, GptAdminAgentJobAdapter | Any] | None = None, message_adapters: Mapping[str, Any] | None = None, call_adapters: Mapping[str, Any] | None = None, android_phone_emergency_call_escalation_seconds: float = 0, android_phone_quiet_start_hour: float = 0, android_phone_quiet_end_hour: float = 12, android_phone_quiet_timezone: str = "Europe/Moscow") -> None:
        """Attach durable delivery state to Telegram, Matrix, and the local S21 adapter."""
        self._center = center
        self._telegram = telegram
        self._matrix_call = matrix_call
        self._android_phone = android_phone
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

    def claim_due(self) -> list[dict[str, Any]]:
        """Claim a bounded batch without blocking the dispatcher heartbeat."""
        deliveries = self._center.claim_due_deliveries(lease_seconds=self._lease_seconds)
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
        incident_id = str(delivery["incident_id"])
        if telegram_mode(incident) == "health":
            return
        if str(delivery["delivery_key"]).endswith(":initial") and self._matrix_call is not None:
            delay = self._matrix_delay_seconds(str(incident["severity"]))
            if delay > 0:
                self._center.schedule_escalation_if_active(incident_id, "matrix.call", time.time() + delay)
        if (
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
        sequence = self._telegram_repeat_sequence(str(delivery["delivery_key"]))
        repeat_delay = self._runtime_float("telegram_critical_repeat_seconds", self._critical_repeat_seconds)
        if str(incident["severity"]) == "critical" and sequence is not None and repeat_delay > 0:
            self._center.schedule_telegram_repeat_if_active(incident_id, sequence + 1, time.time() + repeat_delay)

    def _send_critical_pre_call_context(self, payload: dict[str, Any]) -> None:
        """Send the incident context immediately before a critical phone call."""
        incident = payload["incident"]
        if telegram_mode(incident) == "health":
            return
        if str(incident["severity"]) != "critical":
            return
        active_modes = getattr(self._telegram, "active_modes", None)
        if active_modes is not None and telegram_mode(incident) not in active_modes:
            raise RuntimeError("Telegram mode is inactive for critical pre-call context")
        self._telegram.send(payload)

    def deliver(self, delivery: dict[str, Any]) -> None:
        """Deliver one claimed job; callers may run this in a bounded worker pool."""
        try:
            payload = self._center.delivery_payload(delivery)
            if str(delivery["channel"]).endswith(".call") and not self._automatic_calls_enabled():
                self._center.complete_delivery(delivery["id"], "cancelled", "automatic calls disabled by operator")
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
            elif delivery["channel"] == "matrix.call":
                if self._matrix_call is None:
                    raise RuntimeError("Matrix call sender is not configured")
                result = self._matrix_call.send(payload)
                if result["answered"]:
                    self._center.acknowledge_if_active(str(delivery["incident_id"]), str(result["actor"]))
                elif self._automatic_calls_enabled() and self._android_phone is not None and getattr(self._android_phone, "can_phone_call", False):
                    self._center.schedule_escalation_if_active(str(delivery["incident_id"]), "android.phone.call", time.time())
            elif delivery["channel"] == "android.telegram.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                self._android_phone.telegram_call(payload)
            elif delivery["channel"] == "android.phone.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                if not self._phone_call_allowed(payload["incident"]):
                    self._center.complete_delivery(
                        delivery["id"], "cancelled", "critical phone call suppressed by quiet hours",
                        claimed_at=delivery.get("claimed_at"), attempt=delivery.get("attempt"),
                    )
                    return
                self._send_critical_pre_call_context(payload)
                self._android_phone.phone_call(payload)
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
                if job_name == "health-remediation" and callable(getattr(adapter, "send_with_progress", None)):
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
        deliveries = self.claim_due()
        for delivery in deliveries:
            self.deliver(delivery)
        return len(deliveries)


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


def send_human_request_telegram(
    center: NotificationCenter,
    producer_token: str,
    request: Mapping[str, Any],
    api: Any | None = None,
) -> dict[str, Any]:
    """Send one AskHuman request through the service's existing Telegram bot."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return {"status": "not_configured"}
    payload: dict[str, Any] = {"chat_id": chat_id, "text": str(request["message"])[:4096]}
    mode = str(request["mode"])
    if mode == "choice":
        secret = os.environ.get("TELEGRAM_CALLBACK_SECRET", "").strip()
        if not secret:
            raise RuntimeError("TELEGRAM_CALLBACK_SECRET is required for AskHuman choices")
        codec = TelegramActionCodec(secret)
        rows = [
            [{"text": str(choice["label"])[:80], "callback_data": codec.encode("human_choice", str(request["request_id"]), choice_id=str(index))}]
            for index, choice in enumerate(request.get("choices") or [])
        ]
        payload["reply_markup"] = json.dumps({"inline_keyboard": rows}, ensure_ascii=False, separators=(",", ":"))
    elif mode == "question":
        payload["reply_markup"] = json.dumps({"force_reply": True, "selective": True}, separators=(",", ":"))
    response = (api or (lambda method, body: telegram_api(bot_token, method, body)))("sendMessage", payload)
    message = response.get("result") if isinstance(response, dict) else None
    message_id = message.get("message_id") if isinstance(message, dict) else None
    response_chat_id = str(message.get("chat", {}).get("id") or chat_id) if isinstance(message, dict) else chat_id
    if not isinstance(message_id, int) or message_id <= 0:
        raise RuntimeError("Telegram AskHuman sendMessage returned no message id")
    if mode == "question":
        center.bind_human_request_message(producer_token, str(request["request_id"]), response_chat_id, message_id)
    return {"status": "sent", "chat_id": response_chat_id, "message_id": message_id}


def build_handler(center: NotificationCenter, health_token: str, mcp_token: str | None = None) -> type[BaseHTTPRequestHandler]:
    """Build an HTTP handler bound to one center and two dedicated bearer tokens."""
    if not health_token:
        raise RuntimeError("NOTIFY_CENTER_HEALTH_TOKEN must be configured")
    configured_mcp_token = mcp_token if mcp_token is not None else os.environ.get("NOTIFY_MCP_TOKEN", "")
    if not configured_mcp_token:
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
            if self.path == "/":
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/admin/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
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
            if self.path.startswith("/v1/incidents/"):
                try:
                    incident_id = self.path.rsplit("/", 1)[-1]
                    center.authorize_incident(_bearer(self), incident_id)
                    incident = center.get_incident(incident_id)
                    self._reply(HTTPStatus.OK if incident else HTTPStatus.NOT_FOUND, incident or {"error": "incident not found"})
                except NotificationCenterError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                return
            if self.path.startswith("/v1/human-requests/"):
                try:
                    request_id = self.path.rsplit("/", 1)[-1]
                    self._reply(HTTPStatus.OK, center.get_human_request(_bearer(self), request_id))
                except AuthorizationError as error:
                    self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                except ValidationError as error:
                    self._reply(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            """Accept events and explicit incident actions, returning JSON errors safely."""
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
                    self._reply(HTTPStatus.ACCEPTED, center.create_event(token, key, body, request_meta=self._request_meta()))
                    return
                if self.path == "/v1/human-requests":
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
                    created["telegram"] = send_human_request_telegram(center, token, created)
                    self._reply(HTTPStatus.CREATED, created)
                    return
                parts = self.path.split("/")
                if len(parts) == 5 and parts[:3] == ["", "v1", "human-requests"]:
                    request_id, action = parts[3], parts[4]
                    if action == "resolve":
                        result = center.resolve_human_request(token, request_id, str(body.get("actor") or ""), str(body.get("value") or ""))
                    elif action == "cancel":
                        result = center.cancel_human_request(token, request_id, str(body.get("actor") or "api"))
                    else:
                        self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown human request action"})
                        return
                    self._reply(HTTPStatus.OK, result)
                    return
                if len(parts) == 5 and parts[:3] == ["", "v1", "incidents"]:
                    incident_id, action = parts[3], parts[4]
                    center.authorize_incident(token, incident_id)
                    actor = str(body.get("actor") or "api")
                    if action == "ack":
                        self._reply(HTTPStatus.OK, center.acknowledge(incident_id, actor))
                    elif action == "resolve":
                        self._reply(HTTPStatus.OK, center.resolve(incident_id, actor))
                    elif action == "snooze":
                        self._reply(HTTPStatus.OK, center.snooze(incident_id, float(body.get("until_epoch") or 0), actor))
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
                        )
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


def run_http(center: NotificationCenter, host: str, port: int, health_token: str | None = None, mcp_token: str | None = None) -> None:
    """Run the blocking HTTP server with a mandatory dedicated health token."""
    configured_health_token = health_token if health_token is not None else os.environ.get("NOTIFY_CENTER_HEALTH_TOKEN", "")
    configured_mcp_token = mcp_token if mcp_token is not None else os.environ.get("NOTIFY_MCP_TOKEN", "")
    ThreadingHTTPServer((host, port), build_handler(center, configured_health_token, configured_mcp_token)).serve_forever()


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
