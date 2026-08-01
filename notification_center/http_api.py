"""Stdlib HTTP API and Telegram worker for the notification-center MVP."""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .android_phone import AndroidPhoneAdapter, AndroidPhoneConfig
from .core import AuthorizationError, IdempotencyConflict, NotificationCenter, NotificationCenterError, ValidationError
from .telegram_interactions import TelegramActionCodec, TelegramInteractionPoller


def telegram_inline_keyboard(action_codec: TelegramActionCodec, incident: dict[str, Any]) -> dict[str, list[list[dict[str, str]]]]:
    """Return the narrow interactive contract for this incident severity.

    Only exact critical incidents ask the recipient to acknowledge or snooze;
    every notification retains the non-blocking Ask action.
    """
    incident_id = str(incident["id"])
    ask = {"text": "Ask", "callback_data": action_codec.encode("ask", incident_id)}
    if str(incident["severity"]) != "critical":
        return {"inline_keyboard": [[ask]]}
    return {"inline_keyboard": [
        [
            {"text": "ACK", "callback_data": action_codec.encode("ack", incident_id)},
            {"text": "Snooze 15m", "callback_data": action_codec.encode("snz", incident_id)},
        ],
        [ask],
    ]}


class TelegramSender:
    """Send compact incident cards via Telegram's HTTPS Bot API."""

    def __init__(self, token: str, chat_id: str, timeout_seconds: float = 5, action_codec: TelegramActionCodec | None = None) -> None:
        """Create a sender; empty credentials intentionally leave it unavailable."""
        self._token = token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._action_codec = action_codec

    def send(self, payload: dict[str, Any]) -> None:
        """Deliver one card; raises transport errors so the core can retry it."""
        if not self._token or not self._chat_id:
            raise RuntimeError("Telegram sender is not configured")
        incident = payload["incident"]
        text = f"{str(incident['severity']).upper()} · {incident['project']}\n\n{incident['title']}\n\n{incident['body']}\n\nIncident: {incident['id']}"
        request_data: dict[str, str] = {"chat_id": self._chat_id, "text": text, "disable_web_page_preview": "true"}
        if self._action_codec is not None:
            request_data["reply_markup"] = json.dumps(telegram_inline_keyboard(self._action_codec, incident), separators=(",", ":"))
        data = urllib.parse.urlencode(request_data).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{self._token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")


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


class DeliveryWorker:
    """Run due delivery claims through known adapters without hiding failures."""

    def __init__(self, center: NotificationCenter, telegram: TelegramSender, matrix_call: MatrixCallSender | Any | None = None, android_phone: AndroidPhoneAdapter | Any | None = None, lease_seconds: float = 180, call_escalation_seconds: float = 0, android_telegram_call_escalation_seconds: float = 0, android_phone_call_escalation_seconds: float = 0) -> None:
        """Attach durable delivery state to Telegram, Matrix, and the local S21 adapter."""
        self._center = center
        self._telegram = telegram
        self._matrix_call = matrix_call
        self._android_phone = android_phone
        self._lease_seconds = lease_seconds
        self._call_escalation_seconds = max(0, call_escalation_seconds)
        self._android_telegram_call_escalation_seconds = max(0, android_telegram_call_escalation_seconds)
        self._android_phone_call_escalation_seconds = max(0, android_phone_call_escalation_seconds)

    def claim_due(self) -> list[dict[str, Any]]:
        """Claim a bounded batch without blocking the dispatcher heartbeat."""
        deliveries = self._center.claim_due_deliveries(lease_seconds=self._lease_seconds)
        self._center.mark_dispatcher_healthy()
        return deliveries

    def deliver(self, delivery: dict[str, Any]) -> None:
        """Deliver one claimed job; callers may run this in a bounded worker pool."""
        try:
            payload = self._center.delivery_payload(delivery)
            if delivery["channel"] == "telegram.main":
                self._telegram.send(payload)
                incident = payload["incident"]
                if (
                    self._matrix_call is not None
                    and self._call_escalation_seconds > 0
                    and incident["severity"] == "critical"
                    and str(delivery["delivery_key"]).endswith(":initial")
                ):
                    self._center.schedule_escalation(str(delivery["incident_id"]), "matrix.call", time.time() + self._call_escalation_seconds)
                if (
                    self._android_phone is not None
                    and self._android_telegram_call_escalation_seconds > 0
                    and incident["severity"] == "critical"
                    and str(delivery["delivery_key"]).endswith(":initial")
                ):
                    self._center.schedule_escalation(str(delivery["incident_id"]), "android.telegram.call", time.time() + self._android_telegram_call_escalation_seconds)
                if (
                    self._android_phone is not None
                    and getattr(self._android_phone, "can_phone_call", False)
                    and self._android_phone_call_escalation_seconds > 0
                    and incident["severity"] == "critical"
                    and str(delivery["delivery_key"]).endswith(":initial")
                ):
                    self._center.schedule_escalation(str(delivery["incident_id"]), "android.phone.call", time.time() + self._android_phone_call_escalation_seconds)
            elif delivery["channel"] == "matrix.call":
                if self._matrix_call is None:
                    raise RuntimeError("Matrix call sender is not configured")
                result = self._matrix_call.send(payload)
                if result["answered"]:
                    self._center.acknowledge_if_active(str(delivery["incident_id"]), str(result["actor"]))
            elif delivery["channel"] == "android.telegram.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                self._android_phone.telegram_call(payload)
            elif delivery["channel"] == "android.phone.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                self._android_phone.phone_call(payload)
            else:
                raise RuntimeError(f"channel adapter is not configured: {delivery['channel']}")
            self._center.complete_delivery(delivery["id"], "sent")
        except Exception as error:
            delay = min(300, 5 * (2 ** min(int(delivery["attempt"]), 6)))
            self._center.complete_delivery(delivery["id"], "retry", str(error), delay)

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


def build_handler(center: NotificationCenter, health_token: str) -> type[BaseHTTPRequestHandler]:
    """Build an HTTP handler bound to one center and one dedicated probe token."""
    if not health_token:
        raise RuntimeError("NOTIFY_CENTER_HEALTH_TOKEN must be configured")

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

        def do_GET(self) -> None:
            """Serve authenticated health and incident reads."""
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
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            """Accept events and explicit incident actions, returning JSON errors safely."""
            try:
                token = _bearer(self)
                body = _json_body(self)
                if self.path == "/v1/events":
                    key = self.headers.get("Idempotency-Key") or ""
                    if body.get("action") == "resolve":
                        self._reply(HTTPStatus.OK, center.resolve_event(token, key, body))
                        return
                    self._reply(HTTPStatus.ACCEPTED, center.create_event(token, key, body))
                    return
                parts = self.path.split("/")
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


def run_http(center: NotificationCenter, host: str, port: int, health_token: str | None = None) -> None:
    """Run the blocking HTTP server with a mandatory dedicated health token."""
    configured_health_token = health_token if health_token is not None else os.environ.get("NOTIFY_CENTER_HEALTH_TOKEN", "")
    ThreadingHTTPServer((host, port), build_handler(center, configured_health_token)).serve_forever()


def telegram_action_codec_from_environment() -> TelegramActionCodec | None:
    """Enable signed inline controls only when the dedicated callback secret exists."""
    secret = os.environ.get("TELEGRAM_CALLBACK_SECRET", "")
    return TelegramActionCodec(secret) if secret else None


def telegram_from_environment(action_codec: TelegramActionCodec | None = None) -> TelegramSender:
    """Build the Telegram adapter from env vars without ever logging credentials."""
    return TelegramSender(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), action_codec=action_codec)


def telegram_interactions_from_environment(center: NotificationCenter, codec: TelegramActionCodec | None) -> TelegramInteractionPoller | None:
    """Build the optional in-process Bot API callback poller from strict allowlists."""
    if codec is None:
        return None
    allowed = {part.strip() for part in os.environ.get("TELEGRAM_CALLBACK_ALLOWED_USER_IDS", os.environ.get("TELEGRAM_CHAT_ID", "")).split(",") if part.strip()}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return TelegramInteractionPoller(center, token, allowed, codec) if token and allowed else None


def matrix_call_from_environment() -> MatrixCallSender | None:
    """Build the optional remote MatrixRTC adapter without loading its credentials locally."""
    url = os.environ.get("MATRIX_CALL_URL", "")
    token = os.environ.get("MATRIX_CALL_TOKEN", "")
    return MatrixCallSender(url, token, float(os.environ.get("MATRIX_CALL_TIMEOUT_SECONDS", "150"))) if url or token else None


def android_phone_from_environment() -> AndroidPhoneAdapter | None:
    """Build the optional direct S21 adapter; all commands remain in this process."""
    serial = os.environ.get("ANDROID_ADB_SERIAL", "").strip()
    target = os.environ.get("ANDROID_TELEGRAM_TARGET", "").strip()
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
