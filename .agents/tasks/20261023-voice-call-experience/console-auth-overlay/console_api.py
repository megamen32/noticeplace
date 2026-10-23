#!/usr/bin/env python3
"""NoticePlace operator console — backend service (mesh side).

One service, one port. It serves the static operator panel (the frontend) and
the JSON API the panel talks to. nginx proxies the whole host to this process
behind the shared SSO guard, so the public surface is a single location and
the panel never talks to the mesh itself.

This service lives on the mesh side: it reads the dqlite mesh socket directly
and never imports a Notice Place release.

Trust model: nginx injects `X-Notify-Admin: 1` after the SSO cookie check, so
requests that did not pass the guard never reach this process. Mutations also
require a CSRF token issued by GET /api/csrf.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mesh_data import (  # noqa: E402
    agent_job_profiles,
    ai_sessions,
    consumers_with_stages,
    counter_row,
    csrf_token,
    escalation_settings,
    herder_policy,
    incident_detail,
    journal,
    kill_action,
    kill_status,
    project_stats,
    recent_incidents,
    repeat_incidents,
    scopes_from_env,
    selftest_state,
    valid_csrf,
)

LOG = logging.getLogger("noticeplace-console-api")
HOST = os.environ.get("NOTICE_CONSOLE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("NOTICE_CONSOLE_API_PORT", "8094"))
PANEL_DIR = Path(os.environ.get("NOTICE_CONSOLE_PANEL_DIR", str(Path(__file__).resolve().parent / "panel")))
PROJECT_LIMIT = 50


def _admin_state_dir() -> Path:
    """Return a state directory this service can append its audit record to.

    The audited admin operation writes into the state directory, but the
    console unit runs with ProtectSystem=strict and exposes only its own
    state directory as writable. When the configured directory cannot be
    appended to, the console's own directory keeps the operator action
    audited instead of failing after the consumer was already created.
    """
    configured = Path(os.environ.get("NOTIFY_ADMIN_STATE_DIR", "/var/lib/notification-center-admin"))
    fallback = Path(os.environ.get("NOTIFY_CONSOLE_ADMIN_STATE_DIR", "/var/lib/noticeplace-console/admin-state"))
    for candidate in (configured, fallback):
        try:
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (candidate / "audit.jsonl").open("a", encoding="utf-8"):
                pass
            return candidate
        except OSError:
            continue
    raise ValueError("no writable admin state directory for the consumer audit")


def create_consumer(form: dict[str, Any]) -> dict[str, Any]:
    """Reuse the current release's audited operation against the existing mesh."""
    sys.path.insert(0, "/opt/noticeplace")
    from notification_center.admin import AdminConfigStore, parse_environment
    from notification_center.core import ValidationError

    primary = Path(os.environ.get("NOTIFY_ADMIN_PRIMARY_ENV", "/etc/notification-center.env"))
    if not parse_environment(primary).get("NOTIFY_CENTER_DB", "").startswith("mesh:"):
        raise ValueError("consumer operation requires the configured mesh database")
    store = AdminConfigStore(
        primary,
        Path(os.environ.get("NOTIFY_ADMIN_ROUTES_ENV", "/etc/notification-center-telegram-routes.env")),
        _admin_state_dir(),
    )
    try:
        return store.create_consumer(
            form.get("project", ""), form.get("name", ""), "", "", "", "",
            form.get("max_severity", "notice"), "sso:operator",
            policy_json=form["policy_json"], operator_note=form.get("operator_note", ""),
        )
    except ValidationError as error:
        raise ValueError(str(error)) from None
    finally:
        if store._consumer_center is not None:
            store._consumer_center._connection.close()


def ai_state() -> dict[str, Any]:
    """The single source of truth for the AI lever, shared by every page."""
    scopes = scopes_from_env()
    health = [scope for scope in scopes if scope["agent_jobs"]]
    armed = [scope for scope in health if scope["armed"]]
    return {
        "on": bool(armed),
        "state": "on" if armed else "off",
        "label": "ИИ включён" if armed else "ИИ выключен",
        "health_scopes": len(health),
        "armed_scopes": len(armed),
        "armed_projects": sorted(scope["project"] for scope in armed),
        "detail": (
            f"авто-диагностика активна в {len(armed)} из {len(health)} health-скоупах"
            if armed and len(armed) < len(health)
            else (
                f"авто-диагностика активна во всех {len(health)} health-скоупах"
                if armed
                else f"авто-диагностика выключена во всех {len(health)} health-скоупах"
            )
        ),
    }


def dashboard_payload() -> dict[str, Any]:
    return {
        "ai": ai_state(),
        "selftest": selftest_state(),
        "counters": counter_row(time.time() - 86400),
        "recent": recent_incidents(),
        "repeats": repeat_incidents(),
        "project_stats": project_stats(),
    }


def incidents_payload(params: dict[str, list[str]]) -> dict[str, Any]:
    page = max(1, int((params.get("page") or ["1"])[-1] or 1))
    project = (params.get("project") or [""])[-1]
    rows, total = journal(
        project=project,
        state=(params.get("state") or [""])[-1],
        severity=(params.get("severity") or [""])[-1],
        text=(params.get("q") or [""])[-1][:128],
        sort=(params.get("sort") or ["updated"])[-1],
        limit=PROJECT_LIMIT,
        offset=(page - 1) * PROJECT_LIMIT,
    )
    projects = sorted({str(name or "") for name in project_stats().keys()})
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "limit": PROJECT_LIMIT,
        "projects": [name for name in projects if name],
        "filters": {
            "project": project,
            "state": (params.get("state") or [""])[-1],
            "severity": (params.get("severity") or [""])[-1],
            "q": (params.get("q") or [""])[-1][:128],
            "sort": (params.get("sort") or ["updated"])[-1],
        },
        "ai": ai_state(),
    }


def projects_payload() -> dict[str, Any]:
    return {
        "scopes": scopes_from_env(),
        "project_stats": project_stats(),
        "ai": ai_state(),
    }


def escalations_payload() -> dict[str, Any]:
    data = escalation_settings()
    return {
        "consumers": consumers_with_stages(),
        "settings": data["settings"],
        "topics": data["topics"],
        "mutes": data["mutes"],
        "ai": ai_state(),
    }


def ai_payload() -> dict[str, Any]:
    return {
        "ai": ai_state(),
        "brake": kill_status(),
        "policy": herder_policy(),
        "profiles": agent_job_profiles(),
        "sessions": ai_sessions(),
    }


def build_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "NoticePlaceConsoleApi"

        def _authorized(self) -> bool:
            return self.headers.get("X-Notify-Admin") == "1"

        def _reply(self, status: HTTPStatus, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._reply(status, {"error": message})

        def _panel_file(self, path: str) -> Path:
            """Resolve one panel asset, falling back to the SPA shell."""
            root = PANEL_DIR.resolve()
            relative = path.lstrip("/")
            if relative and not relative.startswith("api/"):
                candidate = (root / relative).resolve()
                if candidate.is_file() and str(candidate).startswith(str(root) + "/"):
                    return candidate
            return root / "index.html"

        def _serve_panel(self, path: str) -> None:
            target = self._panel_file(path)
            try:
                payload = target.read_bytes()
            except OSError:
                self._error(HTTPStatus.NOT_FOUND, "panel asset not found")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/html"):
                content_type = "text/html; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'self'; script-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                    "form-action 'self'; frame-ancestors 'none'",
                )
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if not self._authorized():
                self._error(HTTPStatus.FORBIDDEN, "operator route required")
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if self._not_api(path):
                    self._serve_panel(path)
                    return
                if path == "/api/csrf":
                    self._reply(HTTPStatus.OK, {"token": csrf_token()})
                    return
                if path == "/api/dashboard":
                    self._reply(HTTPStatus.OK, dashboard_payload())
                    return
                if path == "/api/incidents":
                    self._reply(HTTPStatus.OK, incidents_payload(params))
                    return
                if path.startswith("/api/incidents/"):
                    incident = incident_detail(path.split("/api/incidents/", 1)[1])
                    if not incident:
                        self._error(HTTPStatus.NOT_FOUND, "incident not found")
                        return
                    self._reply(HTTPStatus.OK, {"incident": incident, "ai": ai_state()})
                    return
                if path == "/api/projects":
                    self._reply(HTTPStatus.OK, projects_payload())
                    return
                if path == "/api/escalations":
                    self._reply(HTTPStatus.OK, escalations_payload())
                    return
                if path == "/api/ai":
                    self._reply(HTTPStatus.OK, ai_payload())
                    return
                if path == "/api/health":
                    self._reply(HTTPStatus.OK, {"status": "ok", "ai": ai_state(), "selftest": selftest_state()})
                    return
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            except Exception as error:  # noqa: BLE001 - one bad query must not kill the API
                LOG.exception("api read failed")
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)[:300])

        def _not_api(self, path: str) -> bool:
            return not (path == "/api" or path.startswith("/api/"))

        def do_POST(self) -> None:
            if not self._authorized():
                self._error(HTTPStatus.FORBIDDEN, "operator route required")
                return
            path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            token = self.headers.get("X-Notify-CSRF", "")
            if not valid_csrf(token):
                self._error(HTTPStatus.BAD_REQUEST, "invalid or expired CSRF token")
                return
            try:
                if path == "/consumers":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("invalid consumer request size")
                    form = json.loads(self.rfile.read(length))
                    if not isinstance(form, dict) or any(not isinstance(v, str) for v in form.values()):
                        raise ValueError("consumer fields must be strings")
                    policy = json.loads(form.get("policy_json", "null"))
                    if not isinstance(policy, list) or not policy:
                        raise ValueError("explicit nonempty consumer policy required")
                    self._reply(HTTPStatus.CREATED, create_consumer(form))
                    return
                elif path == "/api/ai/arm":
                    ok, output = kill_action("start")
                elif path == "/api/ai/disarm":
                    ok, output = kill_action("stop")
                else:
                    self._error(HTTPStatus.NOT_FOUND, "unknown action")
                    return
                self._reply(
                    HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": ok, "message": output[-2000:], "ai": ai_state()},
                )
            except (ValueError, TypeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid consumer request")
            except Exception as error:  # noqa: BLE001
                LOG.exception("api action failed")
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "operator action failed")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def main() -> int:
    from mesh_data import CSRF_SECRET

    if not CSRF_SECRET:
        print("NOTICE_CONSOLE_CSRF_SECRET or NOTIFY_ADMIN_CSRF_SECRET must be set", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((HOST, PORT), build_handler())
    server.daemon_threads = True
    print(f"noticeplace console api listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
