"""SSO-gated HTML admin surface for Notify Center configuration."""

from __future__ import annotations

import hmac
import html
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .admin import AdminConfigStore, ROUTE_SEVERITIES
from .core import SEVERITIES, ValidationError


def _csrf(secret: str, lifetime_seconds: int = 1800) -> str:
    expiry = str(int(time.time()) + lifetime_seconds)
    nonce = secrets.token_urlsafe(18)
    signature = hmac.new(secret.encode(), f"{expiry}.{nonce}".encode(), "sha256").hexdigest()
    return f"{expiry}.{nonce}.{signature}"


def _valid_csrf(secret: str, token: str) -> bool:
    try:
        expiry, nonce, signature = token.split(".", 2)
        expected = hmac.new(secret.encode(), f"{expiry}.{nonce}".encode(), "sha256").hexdigest()
        return int(expiry) >= int(time.time()) and hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        return False


def build_admin_handler(store: AdminConfigStore, csrf_secret: str) -> type[BaseHTTPRequestHandler]:
    """Build an HTML-only handler that trusts nginx's overwritten auth header."""
    if not csrf_secret:
        raise RuntimeError("NOTIFY_ADMIN_CSRF_SECRET must be configured")

    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "NotifyCenterAdmin"

        def _authorized(self) -> bool:
            return self.headers.get("X-Notify-Admin") == "1"

        def _reply(self, status: HTTPStatus, body: str) -> None:
            rendered = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(rendered)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(rendered)

        def _deny(self) -> None:
            self._reply(HTTPStatus.FORBIDDEN, _page("Access denied", "This endpoint requires the protected operator route."))

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length") or "0")
            if length < 1 or length > 65536:
                raise ValidationError("invalid form body")
            values = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            return {key: value[-1] for key, value in values.items()}

        def _require_form(self) -> dict[str, str]:
            values = self._form()
            if not _valid_csrf(csrf_secret, values.get("csrf", "")):
                raise ValidationError("invalid or expired form token")
            return values

        def do_GET(self) -> None:
            if not self._authorized():
                self._deny()
                return
            if self.path not in ("/", "/admin", "/admin/"):
                self._reply(HTTPStatus.NOT_FOUND, _page("Not found", "Unknown admin path."))
                return
            self._reply(HTTPStatus.OK, _dashboard(store.snapshot(), _csrf(csrf_secret)))

        def do_POST(self) -> None:
            if not self._authorized():
                self._deny()
                return
            try:
                form = self._require_form()
                actor = "sso:operator"
                if self.path in ("/projects", "/admin/projects"):
                    token = store.create_project(form.get("project", ""), form.get("max_severity", ""), actor)
                    self._reply(HTTPStatus.OK, _token_page(form.get("project", ""), token))
                    return
                if self.path.endswith("/severity"):
                    project = urllib.parse.unquote(self.path.rsplit("/", 2)[-2])
                    store.set_project_severity(project, form.get("max_severity", ""), actor)
                elif self.path.endswith("/revoke"):
                    project = urllib.parse.unquote(self.path.rsplit("/", 2)[-2])
                    store.revoke_project(project, actor)
                elif self.path in ("/routes", "/admin/routes"):
                    routes = {severity: {"chat_id": form.get(f"{severity}_chat", ""), "message_thread_id": form.get(f"{severity}_topic", "")} for severity in ROUTE_SEVERITIES}
                    store.set_routes(routes, actor)
                else:
                    self._reply(HTTPStatus.NOT_FOUND, _page("Not found", "Unknown admin action."))
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/admin/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except ValidationError as error:
                self._reply(HTTPStatus.BAD_REQUEST, _page("Configuration rejected", str(error)))
            except Exception:
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, _page("Configuration failed", "No change was accepted. Check the protected admin audit."))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return AdminHandler


def _page(title: str, message: str) -> str:
    return f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></main>"


def _dashboard(snapshot: dict[str, Any], csrf: str) -> str:
    options = "".join(f'<option value="{severity}">{severity}</option>' for severity in SEVERITIES)
    project_rows = "".join(
        f'<tr><td>{html.escape(item["project"])}</td><td>{html.escape(item["max_severity"])}</td><td><code>{html.escape(item["fingerprint"])}</code></td><td><form method="post" action="/admin/projects/{urllib.parse.quote(item["project"], safe="")}/severity"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><select name="max_severity">{options}</select><button>Save level</button></form><form method="post" action="/admin/projects/{urllib.parse.quote(item["project"], safe="")}/revoke"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><button class="danger">Revoke</button></form></td></tr>'
        for item in snapshot["projects"]
    ) or '<tr><td colspan="4">No producer projects yet.</td></tr>'
    routes = snapshot["routes"]
    route_rows = "".join(
        f'<tr><td>{severity}</td><td><input name="{severity}_chat" value="{html.escape(str(routes.get(severity, {}).get("chat_id", "")))}" placeholder="-100…"></td><td><input name="{severity}_topic" value="{html.escape(str(routes.get(severity, {}).get("message_thread_id", "")))}" placeholder="topic id"></td></tr>'
        for severity in ROUTE_SEVERITIES
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Notify Center Admin</title><style>
body{{margin:0;background:#091222;color:#e9edf7;font:16px system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:42px 20px 80px}}h1{{font-size:2.4rem;margin:0 0 8px}}p,.hint{{color:#aeb9cf}}section{{margin-top:24px;padding:24px;border:1px solid #31466f;border-radius:18px;background:#101d33}}h2{{margin-top:0}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px 8px;border-top:1px solid #31466f;text-align:left;vertical-align:top}}input,select,button{{padding:9px;border-radius:8px;border:1px solid #405a88;background:#0b172b;color:#e9edf7}}button{{background:#796ef0;border:0;cursor:pointer}}.danger{{background:#8a3647}}form{{display:inline-flex;gap:7px;margin:3px 5px 3px 0;flex-wrap:wrap}}code{{color:#c4bcff}}@media(max-width:760px){{table{{display:block;overflow:auto}}}}
</style><body><main><div class="hint">Protected operator console</div><h1>Notify Center</h1><p>Producer scopes and Telegram topic routing. Delivery credentials remain server-only.</p>
<section><h2>Add producer</h2><form method="post" action="/admin/projects"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input required name="project" pattern="[A-Za-z0-9._-]+" placeholder="my-service"><select name="max_severity">{options}</select><button>Create one-time token</button></form></section>
<section><h2>Producer projects</h2><table><tr><th>Project</th><th>Maximum level</th><th>Token fingerprint</th><th>Actions</th></tr>{project_rows}</table></section>
<section><h2>Telegram topic routes</h2><form method="post" action="/admin/routes"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><table><tr><th>Severity</th><th>Chat ID</th><th>Topic ID</th></tr>{route_rows}</table><p class="hint">Leave a row blank to use the default route.</p><button>Save routes</button></form></section></main></body></html>"""


def _token_page(project: str, token: str) -> str:
    return f"""<!doctype html><meta charset=utf-8><title>Copy producer token</title><style>body{{margin:0;background:#091222;color:#e9edf7;font:16px system-ui,sans-serif}}main{{max-width:680px;margin:15vh auto;padding:28px;border:1px solid #405a88;border-radius:18px;background:#101d33}}code{{display:block;padding:16px;background:#08101e;overflow-wrap:anywhere;color:#c4bcff}}a{{color:#bdb6ff}}</style><main><h1>Copy this token now</h1><p>It is shown only on this response. Store it in the producer's secret store; the console will later keep only its fingerprint.</p><code>{html.escape(token)}</code><p><a href="/admin/">Back to admin</a></p></main>"""


def run_admin_http(store: AdminConfigStore, csrf_secret: str, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), build_admin_handler(store, csrf_secret)).serve_forever()
