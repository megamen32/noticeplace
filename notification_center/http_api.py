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
from .gptadmin_phone import GptAdminPhoneAdapter
from .gptadmin_agent import GptAdminAgentJobAdapter
from .landing import LANDING_PAGE
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


def telegram_destination(default_chat_id: str, severity_routes: dict[str, dict[str, Any]], incident: dict[str, Any]) -> dict[str, str]:
    """Resolve an allowlisted severity route without trusting event routing data."""
    configured = severity_routes.get(str(incident["severity"]), {})
    destination = {"chat_id": str(configured.get("chat_id") or default_chat_id)}
    thread_id = configured.get("message_thread_id")
    if thread_id is not None:
        destination["message_thread_id"] = str(thread_id)
    return destination


class TelegramSender:
    """Send compact incident cards via Telegram's HTTPS Bot API."""

    def __init__(self, token: str, chat_id: str, timeout_seconds: float = 5, action_codec: TelegramActionCodec | None = None, severity_routes: dict[str, dict[str, Any]] | None = None) -> None:
        """Create a sender; empty credentials intentionally leave it unavailable."""
        self._token = token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._action_codec = action_codec
        self._severity_routes = severity_routes or {}

    def send(self, payload: dict[str, Any]) -> None:
        """Deliver one card; raises transport errors so the core can retry it."""
        if not self._token or not self._chat_id:
            raise RuntimeError("Telegram sender is not configured")
        incident = payload["incident"]
        text = f"{str(incident['severity']).upper()} · {incident['project']}\n\n{incident['title']}\n\n{incident['body']}\n\nIncident: {incident['id']}"
        request_data: dict[str, str] = {**telegram_destination(self._chat_id, self._severity_routes, incident), "text": text, "disable_web_page_preview": "true"}
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

    def __init__(self, center: NotificationCenter, telegram: TelegramSender, matrix_call: MatrixCallSender | Any | None = None, android_phone: AndroidPhoneAdapter | Any | None = None, lease_seconds: float = 180, call_escalation_seconds: float = 0, android_telegram_call_escalation_seconds: float = 0, android_phone_call_escalation_seconds: float = 0, critical_repeat_seconds: float = 0, critical_call_escalation_seconds: float | None = None, emergency_call_escalation_seconds: float = 0, agent_jobs: dict[str, GptAdminAgentJobAdapter | Any] | None = None) -> None:
        """Attach durable delivery state to Telegram, Matrix, and the local S21 adapter."""
        self._center = center
        self._telegram = telegram
        self._matrix_call = matrix_call
        self._android_phone = android_phone
        self._agent_jobs = dict(agent_jobs or {})
        self._lease_seconds = lease_seconds
        self._critical_call_escalation_seconds = max(0, call_escalation_seconds if critical_call_escalation_seconds is None else critical_call_escalation_seconds)
        self._emergency_call_escalation_seconds = max(0, emergency_call_escalation_seconds)
        self._critical_repeat_seconds = max(0, critical_repeat_seconds)
        self._android_telegram_call_escalation_seconds = max(0, android_telegram_call_escalation_seconds)
        self._android_phone_call_escalation_seconds = max(0, android_phone_call_escalation_seconds)

    def claim_due(self) -> list[dict[str, Any]]:
        """Claim a bounded batch without blocking the dispatcher heartbeat."""
        deliveries = self._center.claim_due_deliveries(lease_seconds=self._lease_seconds)
        self._center.mark_dispatcher_healthy()
        return deliveries

    def _matrix_delay_seconds(self, severity: str) -> float:
        if severity == "critical":
            return self._critical_call_escalation_seconds
        if severity == "emergency":
            return self._emergency_call_escalation_seconds
        return 0

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
        if str(delivery["delivery_key"]).endswith(":initial") and self._matrix_call is not None:
            delay = self._matrix_delay_seconds(str(incident["severity"]))
            if delay > 0:
                self._center.schedule_escalation_if_active(incident_id, "matrix.call", time.time() + delay)
        if (
            str(delivery["delivery_key"]).endswith(":initial")
            and str(incident["severity"]) == "critical"
            and self._android_phone is not None
            and getattr(self._android_phone, "can_phone_call", False)
            and self._android_phone_call_escalation_seconds > 0
        ):
            self._center.schedule_escalation_if_active(
                incident_id,
                "android.phone.call",
                time.time() + self._android_phone_call_escalation_seconds,
            )
        sequence = self._telegram_repeat_sequence(str(delivery["delivery_key"]))
        if str(incident["severity"]) == "critical" and sequence is not None and self._critical_repeat_seconds > 0:
            self._center.schedule_telegram_repeat_if_active(incident_id, sequence + 1, time.time() + self._critical_repeat_seconds)

    def deliver(self, delivery: dict[str, Any]) -> None:
        """Deliver one claimed job; callers may run this in a bounded worker pool."""
        try:
            payload = self._center.delivery_payload(delivery)
            if delivery["channel"] == "telegram.main":
                self._telegram.send(payload)
                incident = payload["incident"]
                self._center.complete_delivery(delivery["id"], "sent")
                self._after_telegram_delivery(delivery, incident)
                return
            elif delivery["channel"] == "matrix.call":
                if self._matrix_call is None:
                    raise RuntimeError("Matrix call sender is not configured")
                result = self._matrix_call.send(payload)
                if result["answered"]:
                    self._center.acknowledge_if_active(str(delivery["incident_id"]), str(result["actor"]))
                elif self._android_phone is not None and getattr(self._android_phone, "can_phone_call", False):
                    self._center.schedule_escalation_if_active(str(delivery["incident_id"]), "android.phone.call", time.time())
            elif delivery["channel"] == "android.telegram.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                self._android_phone.telegram_call(payload)
            elif delivery["channel"] == "android.phone.call":
                if self._android_phone is None:
                    raise RuntimeError("Android phone adapter is not configured")
                self._android_phone.phone_call(payload)
            elif str(delivery["channel"]).startswith("gptadmin.agent:"):
                job_name = str(delivery["channel"])[len("gptadmin.agent:"):]
                adapter = self._agent_jobs.get(job_name)
                if adapter is None:
                    raise RuntimeError(f"GPTAdmin agent job adapter is not configured: {job_name}")
                receipt = adapter.send(payload, str(delivery["delivery_key"]))
                self._center.record_agent_job_result(str(delivery["incident_id"]), str(delivery["id"]), job_name, receipt)
                if str(receipt.get("status") or "") == "failed":
                    self._center.complete_delivery(delivery["id"], "failed", "GPTAdmin agent job reported terminal failure")
                    return
                if str(receipt.get("status") or "") != "completed":
                    raise RuntimeError("GPTAdmin agent job returned a non-terminal result")
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

        def _html(self, status: HTTPStatus, body: bytes) -> None:
            """Serve the public landing page without any incident data or tokens."""
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            """Serve authenticated health and incident reads."""
            if self.path == "/":
                self._html(HTTPStatus.OK, LANDING_PAGE)
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


def telegram_from_environment(action_codec: TelegramActionCodec | None = None) -> TelegramSender:
    """Build the Telegram adapter from env vars without ever logging credentials."""
    return TelegramSender(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), action_codec=action_codec, severity_routes=telegram_routes_from_environment())


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


def gptadmin_agent_jobs_from_environment() -> dict[str, GptAdminAgentJobAdapter]:
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
        )
    return result


def android_phone_from_environment() -> AndroidPhoneAdapter | GptAdminPhoneAdapter | None:
    """Build either the direct ADB adapter or the narrow fixed GPTAdmin call path."""
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
