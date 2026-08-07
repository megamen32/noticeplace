"""SSO-gated HTML admin surface for Notify Center configuration."""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .admin import AdminConfigStore, ROUTE_SEVERITIES
from .core import SEVERITIES, ValidationError

LOGGER = logging.getLogger(__name__)

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
                if self.path in ("/consumers", "/admin/consumers"):
                    created = store.create_consumer(
                        form.get("project", ""), form.get("name", ""), form.get("chat_id", ""),
                        form.get("topic_id", ""), form.get("matrix_delay_seconds", ""), form.get("phone_delay_seconds", ""),
                        form.get("max_severity", "critical"), actor, form.get("policy_json", ""),
                    )
                    self._reply(HTTPStatus.OK, _consumer_token_page(form.get("name", ""), created["intake_token"]))
                    return
                if self.path in ("/calls", "/admin/calls"):
                    enabled = form.get("enabled", "") == "true"
                    store.set_automatic_calls(enabled, actor)
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/admin/")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if self.path in ("/settings", "/admin/settings"):
                    store.set_runtime_settings(form, actor)
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/admin/")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if self.path in ("/topics/save", "/admin/topics/save"):
                    store.save_topic(
                        form.get("topic_id", ""), form.get("name", ""), form.get("chat_id", ""),
                        form.get("message_thread_id", ""), form.get("enabled", "true") == "true", actor,
                    )
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/admin/")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if self.path in ("/topics/delete", "/admin/topics/delete"):
                    store.delete_topic(form.get("topic_id", ""), actor)
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/admin/")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
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
                LOGGER.exception("Notify Center admin mutation failed")
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
    topics = snapshot.get("topics", [])
    topic_rows = "".join(
        f'<tr><td>{html.escape(topic["name"])}</td><td>{html.escape(topic["id"])}</td><td>{html.escape(topic["chat_id"])}</td><td>{html.escape(str(topic["message_thread_id"] or ""))}</td><td><a href="#topic-{html.escape(topic["id"])}">Edit</a></td></tr>'
        for topic in topics
    ) or '<tr><td colspan="5">No topics yet.</td></tr>'
    topic_forms = "".join(
        f'<form id="topic-{html.escape(topic["id"])}" method="post" action="/admin/topics/save"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="{html.escape(topic["id"])}"><input required name="name" value="{html.escape(topic["name"])}" placeholder="Topic name"><input required name="chat_id" value="{html.escape(topic["chat_id"])}" placeholder="-100…"><input name="message_thread_id" value="{html.escape(str(topic["message_thread_id"] or ""))}" placeholder="blank = create" inputmode="numeric"><label><input type="checkbox" name="enabled" value="true" {"checked" if topic["enabled"] else ""}> active</label><button>Save</button></form><form method="post" action="/admin/topics/delete"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="{html.escape(topic["id"])}"><button class="danger">Delete</button></form>'
        for topic in topics
    )
    topic_forms += f'<form method="post" action="/admin/topics/save"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="new-topic"><input required name="name" placeholder="New topic name"><input required name="chat_id" placeholder="-100…"><input name="message_thread_id" placeholder="blank = create" inputmode="numeric"><label><input type="checkbox" name="enabled" value="true" checked> active</label><button>Create topic</button></form>'
    consumer_rows = "".join(
        f'<tr><td>{html.escape(item["name"])}</td><td>{html.escape(item["project"])}</td><td><code>{html.escape(item["token_fingerprint"])}</code></td><td>{html.escape(_policy_display(item["policy"]))}</td></tr>'
        for item in snapshot["consumers"]
    ) or '<tr><td colspan="4">No scoped consumers yet.</td></tr>'
    calls_enabled = bool(snapshot.get("automatic_calls_enabled", True))
    calls_label = "Enabled" if calls_enabled else "Disabled"
    calls_action = "false" if calls_enabled else "true"
    calls_button = "Disable automatic calls" if calls_enabled else "Enable automatic calls"
    runtime_settings = snapshot.get("runtime_settings", {})
    setting_labels = {
        "matrix_call_critical_escalation_seconds": "Matrix critical call delay (s)",
        "matrix_call_emergency_escalation_seconds": "Matrix emergency call delay (s)",
        "android_phone_call_escalation_seconds": "Android phone call delay (s)",
        "android_telegram_call_escalation_seconds": "Android Telegram call delay (s)",
        "telegram_critical_repeat_seconds": "Critical Telegram repeat delay (s)",
    }
    setting_inputs = "".join(
        f'<label>{html.escape(label)} <input required name="{key}" type="number" min="0" step="any" value="{html.escape(str(runtime_settings.get(key, "0")))}"></label>'
        for key, label in setting_labels.items()
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Notify Center Admin</title><style>
body{{margin:0;background:#091222;color:#e9edf7;font:16px system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:42px 20px 80px}}h1{{font-size:2.4rem;margin:0 0 8px}}p,.hint{{color:#aeb9cf}}section{{margin-top:24px;padding:24px;border:1px solid #31466f;border-radius:18px;background:#101d33}}h2{{margin-top:0}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px 8px;border-top:1px solid #31466f;text-align:left;vertical-align:top}}input,select,button{{padding:9px;border-radius:8px;border:1px solid #405a88;background:#0b172b;color:#e9edf7}}button{{background:#796ef0;border:0;cursor:pointer}}.danger{{background:#8a3647}}form{{display:inline-flex;gap:7px;margin:3px 5px 3px 0;flex-wrap:wrap}}code{{color:#c4bcff}}@media(max-width:760px){{table{{display:block;overflow:auto}}}}
</style><body><main><div class="hint">Protected operator console</div><h1>Notify Center</h1><p>Producer scopes and Telegram topic routing. Delivery credentials remain server-only.</p>
<section><h2>Add producer</h2><form method="post" action="/admin/projects"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input required name="project" pattern="[A-Za-z0-9._-]+" placeholder="my-service"><select name="max_severity">{options}</select><button>Create one-time token</button></form></section>
<section><h2>Producer projects</h2><table><tr><th>Project</th><th>Maximum level</th><th>Token fingerprint</th><th>Actions</th></tr>{project_rows}</table></section>
<section><h2>Automatic call escalation</h2><p class="hint">{calls_label}. This controls future Android phone, Telegram-call, and Matrix-call escalations. Text notifications are unchanged; an already active phone call cannot be interrupted.</p><form method="post" action="/admin/calls"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="enabled" value="{calls_action}"><button>{calls_button}</button></form></section>
<section><h2>Live delivery timers</h2><p class="hint">Changes apply to newly scheduled/retried deliveries and do not restart Notify. Zero disables that timer. An already executing adapter call is unchanged.</p><form method="post" action="/admin/settings"><input type="hidden" name="csrf" value="{html.escape(csrf)}">{setting_inputs}<button>Save live settings</button></form></section>
<section><h2>Add scoped consumer</h2><p class="hint">Build any escalation chain. Each step is a JSON object with platform, action, target, retry_interval_seconds, max_repeats, and optional previous_step_id. The successor points to its predecessor; no platform order is imposed. Leave policy JSON blank for the legacy Telegram/Matrix/Phone form.</p><form method="post" action="/admin/consumers"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input required name="name" placeholder="Gateway producer"><input required name="project" pattern="[A-Za-z0-9._-]+" placeholder="hermes"><select name="max_severity">{options}</select><textarea name="policy_json" rows="5" cols="70" placeholder='[{{"id":"telegram-1","platform":"telegram","action":"message","target":{{"chat_id":-100123}},"retry_interval_seconds":10800,"max_repeats":10}}]'></textarea><input name="chat_id" inputmode="numeric" placeholder="Legacy Telegram chat ID"><input name="topic_id" inputmode="numeric" placeholder="Legacy topic ID"><input name="matrix_delay_seconds" type="number" min="1" step="1" placeholder="Legacy Matrix delay (seconds)"><input name="phone_delay_seconds" type="number" min="1" step="1" placeholder="Legacy phone delay (seconds)"><button>Create consumer intake</button></form></section>
<section><h2>Scoped consumer policies</h2><table><tr><th>Name</th><th>Project</th><th>Token fingerprint</th><th>Ordered delivery policy</th></tr>{consumer_rows}</table></section>
<section><h2>Telegram topics</h2><p class="hint">All topics are equal. Some were created by the initial configuration, but they can be edited or deleted exactly like any other topic. Changes apply live without restarting Notify.</p><table><tr><th>Name</th><th>Key</th><th>Chat</th><th>Topic</th><th>Action</th></tr>{topic_rows}</table><div class="topic-forms">{topic_forms}</div></section></main></body></html>"""


def _policy_display(policy: list[dict[str, Any]]) -> str:
    labels = []
    for stage in policy:
        if not stage["enabled"]:
            continue
        if "platform" in stage:
            target = json.dumps(stage.get("target", {}), ensure_ascii=False, sort_keys=True)
            previous = stage.get("previous_step_id")
            suffix = f", after {previous}" if previous else ", first"
            labels.append(f'{stage["platform"]}.{stage["action"]}: {target}, every {stage.get("retry_interval_seconds", 0):g}s × {stage.get("max_repeats", 0)}{suffix}')
        elif stage["kind"] == "telegram":
            destination = f'chat {stage["chat_id"]}'
            if "topic_id" in stage:
                destination += f', topic {stage["topic_id"]}'
            labels.append(f"Telegram: {destination} (immediate)")
        elif stage["kind"] == "phone":
            labels.append(f'Phone: fixed adapter after {stage["delay_seconds"]:g}s')
        elif stage["kind"] == "matrix":
            labels.append(f'Matrix call after {stage["delay_seconds"]:g}s (server-owned target)')
    return " → ".join(labels)


def _token_page(project: str, token: str) -> str:
    safe_project = html.escape(project)
    env_path = f"/etc/{safe_project}/notify.env"
    curl = f"""curl --fail-with-body --silent --show-error --noproxy '*' \\
  --request POST \"$NOTIFY_CENTER_EVENT_URL\" \\
  --header \"Authorization: Bearer $NOTIFY_CENTER_TOKEN\" \\
  --header \"Idempotency-Key: deploy-$(date +%s)\" \\
  --header 'Content-Type: application/json' \\
  --data '{{\"schema\":\"notify.event.v1\",\"project\":\"{safe_project}\",\"recipient\":\"me\",\"kind\":\"incident\",\"severity\":\"important\",\"title\":\"Deploy failed\",\"body\":\"Replace this message.\",\"dedup_key\":\"deploy:production\"}}'"""
    python = f"""from notify_center_client import NotificationCenterClient

client = NotificationCenterClient.from_environment()
client.emit(project=\"{safe_project}\", severity=\"important\", title=\"Deploy failed\", dedup_key=\"deploy:production\")"""
    node = f"""import {{ NotificationCenterClient }} from \"notify-mcp/notification-center\";

const client = NotificationCenterClient.fromEnvironment();
await client.emit({{ project: \"{safe_project}\", severity: \"important\", title: \"Deploy failed\", dedupKey: \"deploy:production\" }});"""
    return f"""<!doctype html><meta charset=utf-8><title>Connect {safe_project}</title><style>body{{margin:0;background:#091222;color:#e9edf7;font:16px system-ui,sans-serif}}main{{max-width:900px;margin:8vh auto;padding:28px}}section{{margin:18px 0;padding:22px;border:1px solid #405a88;border-radius:18px;background:#101d33}}code,pre{{display:block;padding:16px;background:#08101e;overflow:auto;overflow-wrap:anywhere;color:#c4bcff;white-space:pre-wrap}}.warning{{color:#ffd28a}}a{{color:#bdb6ff}}</style><main><h1>Connect {safe_project}</h1><p class=warning>Copy the token now. It is not available after leaving this page; the console retains only a fingerprint.</p><code>{html.escape(token)}</code><section><h2>1. Store it as a service secret</h2><pre># {env_path} (owner root, mode 0600)
NOTIFY_CENTER_EVENT_URL=https://notify.bezrabotnyi.com/v1/events
NOTIFY_CENTER_TOKEN=&lt;paste the token above here&gt;</pre><p>For systemd use <code>EnvironmentFile={env_path}</code>. Do not put the token in a unit command, Git, or shell history.</p></section><section><h2>2. Send with curl</h2><pre>{html.escape(curl)}</pre></section><section><h2>Or use a small SDK</h2><pre># Python: pip install 'git+https://github.com/megamen32/notify.git#subdirectory=python'
{html.escape(python)}

# Node.js: npm install github:megamen32/notify
{html.escape(node)}</pre></section><p><a href=\"https://github.com/megamen32/notify\">GitHub repository</a> · <a href=\"https://github.com/megamen32/notify/blob/main/docs/producer-sdk.md\">Full producer guide</a> · <a href=\"/admin/\">Back to admin</a></p></main>"""


def _consumer_token_page(name: str, token: str) -> str:
    safe_name = html.escape(name)
    intake_url = "https://notify.bezrabotnyi.com/v1/events"
    return f"""<!doctype html><meta charset=utf-8><title>Connect {safe_name}</title><style>body{{margin:0;background:#091222;color:#e9edf7;font:16px system-ui,sans-serif}}main{{max-width:900px;margin:8vh auto;padding:28px}}section{{margin:18px 0;padding:22px;border:1px solid #405a88;border-radius:18px;background:#101d33}}code{{display:block;padding:16px;background:#08101e;overflow:auto;overflow-wrap:anywhere;color:#c4bcff}}.warning{{color:#ffd28a}}a{{color:#bdb6ff}}</style><main><h1>Connect {safe_name}</h1><p class=warning>Copy this intake URL and token now. The token is shown only on this page; the console retains only its fingerprint.</p><section><h2>Intake URL</h2><code>{intake_url}</code></section><section><h2>Scoped bearer token</h2><code>{html.escape(token)}</code></section><p>Delivery targets are fixed by the operator policy and cannot be selected by producer events.</p><p><a href=\"/admin/\">Back to admin</a></p></main>"""


def run_admin_http(store: AdminConfigStore, csrf_secret: str, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), build_admin_handler(store, csrf_secret)).serve_forever()
