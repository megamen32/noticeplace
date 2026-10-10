"""SSO-gated HTML admin surface for NoticePlace configuration."""

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
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
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
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path not in ("/", "/admin", "/admin/"):
                self._reply(HTTPStatus.NOT_FOUND, _page("Not found", "Unknown admin path."))
                return
            query = urllib.parse.parse_qs(parsed.query).get("history", [""])[-1][:128]
            self._reply(HTTPStatus.OK, _dashboard(store.snapshot(query), _csrf(csrf_secret)))

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
                        form.get("max_severity", "critical"), actor, form.get("policy_json", ""), form.get("operator_note", ""),
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
                if self.path in ("/test-adapter", "/admin/test-adapter"):
                    result = store.test_adapter(form.get("adapter", ""), form.get("message", ""), actor)
                    adapter = "phone" if form.get("adapter") == "phone" else "message"
                    self._reply(HTTPStatus.OK, _test_result_page(adapter, result))
                    return
                if self.path in ("/settings", "/admin/settings"):
                    store.set_runtime_settings(form, actor)
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/admin/")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if self.path in ("/health-settings", "/admin/health-settings"):
                    try:
                        health_config = json.loads(form.get("config_json", ""))
                    except json.JSONDecodeError as error:
                        raise ValidationError("health monitor config must be valid JSON") from error
                    store.save_health_config(health_config, actor, run_monitor=form.get("run_monitor") == "true")
                    self._reply(HTTPStatus.OK, _page("Health monitor updated", "The bounded fleet config was saved atomically."))
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
                LOGGER.exception("NoticePlace admin mutation failed")
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, _page("Configuration failed", "No change was accepted. Check the protected admin audit."))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return AdminHandler


_ADMIN_CSS = """:root{--bg:#0b0e14;--surface:#10151d;--surface-2:#171e29;--surface-3:#1e2734;--border:#212b3a;--border-strong:#2e3c52;--text:#e6edf3;--muted:#8fa0b3;--faint:#5f6f81;--accent:#4c8dff;--accent-soft:rgba(76,141,255,.16);--green:#3fb950;--orange:#f0883e;--red:#f85149;--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}html{color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,Cantarell,sans-serif;-webkit-font-smoothing:antialiased}
a{color:#8ab4ff;text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{margin:0;font-weight:650;letter-spacing:-.01em}
p{margin:8px 0}.hint{color:var(--muted);font-size:12.5px}
code{font-family:var(--mono);font-size:12px;color:#b3c7ff;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:1px 6px;word-break:break-all}
pre{background:#0a0f16;border:1px solid var(--border);border-radius:10px;padding:14px 16px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--mono);font-size:12.5px;line-height:1.55;color:#b3c7ff}
.topbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:14px;padding:11px 24px;background:rgba(11,14,20,.92);backdrop-filter:blur(6px);border-bottom:1px solid var(--border)}
.topbar .brand{font-weight:700;font-size:15px}.topbar .brand small{color:var(--muted);font-weight:500;font-size:12px}
.topbar .shield{margin-left:auto;display:inline-flex;align-items:center;gap:8px;font-size:11.5px;color:var(--muted);border:1px solid var(--border-strong);border-radius:999px;padding:4px 12px;background:var(--surface)}
.topbar .shield::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green)}
main{max-width:1240px;margin:0 auto;padding:26px 24px 90px}
main.narrow{max-width:860px}
.page-head h1{font-size:22px}.page-head>p{color:var(--muted);margin:6px 0 0;max-width:720px}
section.card,.card{margin-top:18px;padding:20px 22px;background:var(--surface);border:1px solid var(--border);border-radius:12px}
.card>h2,.card-head h2{font-size:15.5px}
.card-head{display:flex;flex-direction:column;gap:5px;margin-bottom:14px}
.card-head .hint{margin:0}
.eyebrow{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:var(--accent)}
.card h3{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:20px 0 8px}
input,select,textarea{padding:8px 10px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);font:inherit;font-size:13px}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
input::placeholder,textarea::placeholder{color:var(--faint)}
button{padding:8px 14px;border-radius:8px;border:1px solid transparent;background:var(--accent);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer;transition:filter .12s,background .12s}
button:hover{filter:brightness(1.15)}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:none;box-shadow:0 0 0 3px var(--accent-soft)}
button.danger{background:rgba(248,81,73,.12);border-color:rgba(248,81,73,.45);color:#ff8f88}
button.danger:hover{background:rgba(248,81,73,.22);filter:none}
button[type=button]{background:var(--surface-3);border:1px solid var(--border-strong);color:var(--text);font-weight:550}
button[type=button]:hover{filter:brightness(1.25)}
form.consumer-form>button[type=submit]{align-self:flex-start}
form{display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 8px 4px 0;vertical-align:middle}
form.card-form{margin:0}
form.card-form input[name=project]{min-width:220px}
form.settings{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px 18px;margin:12px 0 0;align-items:end}
form.settings label{display:flex;flex-direction:column;gap:6px;font-size:12.5px;color:var(--muted)}
form.settings button{justify-self:start}
form.toolbar{display:flex;flex:1;gap:8px;margin:10px 0 16px;flex-wrap:wrap}
form.toolbar input{flex:1;min-width:240px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:650;letter-spacing:.03em;border:1px solid var(--border-strong);color:#a8b3bf;background:var(--surface-2);white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.9}
.badge.sev-emergency{color:#ff7b74;background:rgba(248,81,73,.13);border-color:rgba(248,81,73,.5)}
.badge.sev-critical{color:#ffa198;background:rgba(248,81,73,.09);border-color:rgba(248,81,73,.35)}
.badge.sev-important{color:#ffb26b;background:rgba(240,136,62,.12);border-color:rgba(240,136,62,.45)}
.badge.sev-notice{color:#79b8ff;background:rgba(76,141,255,.12);border-color:rgba(76,141,255,.45)}
.badge.sev-health,.badge.sev-ok{color:#56d364;background:rgba(63,185,80,.12);border-color:rgba(63,185,80,.45)}
.badge.sev-failed{color:#ff7b74;background:rgba(248,81,73,.13);border-color:rgba(248,81,73,.5)}
.table-wrap{overflow-x:auto;margin:4px 0;border:1px solid var(--border);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{padding:9px 12px;text-align:left;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);background:var(--surface-2);border-bottom:1px solid var(--border-strong);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:rgba(255,255,255,.018)}
td.row-actions form{margin:2px 4px 2px 0}
td.row-actions select{width:112px}
.health-hero{border-color:rgba(76,141,255,.4);background:linear-gradient(180deg,rgba(76,141,255,.07),rgba(16,21,29,0) 55%),var(--surface)}
.health-heading{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;flex-wrap:wrap}
.status-card{padding:12px 16px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface-2);min-width:260px;font-size:13px}
.status-card small{color:var(--muted)}
.pulse{display:inline-block;width:8px;height:8px;margin-right:8px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green)}
details{margin-top:18px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2)}
summary{cursor:pointer;padding:12px 16px;font-weight:600;color:#9ec1ff;font-size:13.5px;list-style:none}
summary::before{content:"▸ ";color:var(--muted)}
details[open] summary::before{content:"▾ "}
details[open] summary{border-bottom:1px solid var(--border)}
details>form,details>p{margin:14px 16px}
.health-config{display:grid;gap:12px}
.health-config button{justify-self:start}
.health-config textarea{width:100%;max-width:920px;font-family:var(--mono);font-size:12.5px;line-height:1.5}
.topic-forms{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:12px;margin-top:14px}
.topic-card{padding:12px;background:var(--surface-2);border:1px solid var(--border);border-radius:10px;display:flex;flex-direction:column;gap:8px}
.topic-card form{margin:0;display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:center}
.topic-card form label{display:flex;gap:6px;align-items:center;font-size:12.5px;color:var(--muted);grid-column:span 2}
.topic-card form button{justify-self:start}
fieldset{border:1px solid var(--border-strong);border-radius:10px;padding:10px 12px 12px;margin:8px 0;background:var(--surface-2)}
legend{font-size:12px;color:var(--muted);padding:0 6px}
#adapter-steps fieldset{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
form.consumer-form{display:flex;flex-direction:column;align-items:stretch;gap:10px;margin:0;max-width:920px}
#adapter-builder{width:100%}
#adapter-steps fieldset input[data-target]{flex:1;min-width:200px}
.token-box{display:block;padding:14px 16px;background:var(--surface-2);border:1px dashed var(--border-strong);border-radius:10px}
.token-box code{display:block;background:transparent;border:0;padding:0;color:#ffd28a;font-family:var(--mono);font-size:13px}
.warning{color:#ffb26b}
.page-center{max-width:560px;margin:16vh auto 0;padding:30px;background:var(--surface);border:1px solid var(--border);border-radius:14px;text-align:center}
.page-center h1{font-size:19px}
.health-plans{margin-top:6px;font-size:12.5px;color:var(--muted)}
.health-plans span{display:inline-block}
@media(max-width:1280px){main{padding:20px 16px 70px}.topbar{padding:11px 16px}}
@media(max-width:860px){.health-heading{display:block}.status-card{margin-top:12px;min-width:0}.topic-forms{grid-template-columns:1fr}form.settings{grid-template-columns:1fr}}"""

_SEVERITY_BADGE_CLASSES = {
    "emergency": "sev-emergency",
    "critical": "sev-critical",
    "important": "sev-important",
    "notice": "sev-notice",
    "info": "sev-info",
    "debug": "sev-debug",
    "log": "sev-log",
    "health": "sev-health",
}

_STATE_BADGE_CLASSES = {
    "open": "sev-important",
    "in_progress": "sev-notice",
    "selected": "sev-notice",
    "resolved": "sev-health",
    "healthy": "sev-health",
    "closed": "sev-neutral",
    "degraded": "sev-important",
    "failed": "sev-failed",
    "sent": "sev-health",
    "delivered": "sev-health",
}


def _badge(value: Any, classes: dict[str, str] | None = None) -> str:
    """Render a small severity/status pill; presentational only."""
    table = classes if classes is not None else _SEVERITY_BADGE_CLASSES
    text = str(value or "").strip()
    if not text:
        return ""
    cls = table.get(text.lower(), "sev-neutral")
    return f'<span class="badge {cls}">{html.escape(text)}</span>'


def _page(title: str, message: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_ADMIN_CSS}</style></head><body>"
        f'<main class="page-center"><h1>{html.escape(title)}</h1>'
        f'<p class="hint">{html.escape(message)}</p></main></body></html>'
    )


def _test_result_page(adapter: str, result: dict[str, Any]) -> str:
    """Show a display-safe result without exposing tokens or raw transport data."""
    detail = "Phone call started." if adapter == "phone" else "Message accepted by Notify."
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Adapter test</title><style>{_ADMIN_CSS}</style></head><body>"
        f'<main class="page-center"><h1>Adapter test accepted</h1>'
        f'<p class="hint">{html.escape(detail)}</p>'
        f'<p><a href="/admin/">Back to admin</a></p></main></body></html>'
    )


def _history_notification_display(item: dict[str, Any]) -> str:
    """Format delivery states without exposing adapter payloads or secrets."""
    badges = []
    for entry in item.get("notifications", []):
        text = f'{entry.get("channel")}={entry.get("status")}'
        cls = _STATE_BADGE_CLASSES.get(str(entry.get("status") or "").lower(), "sev-neutral")
        badges.append(f'<span class="badge {cls}">{html.escape(text)}</span>')
    return "<br>".join(badges) or "—"


def _history_time_display(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(float(value)))
    except (TypeError, ValueError, OverflowError):
        return "unknown time"


def _history_incident_link(incident_id: Any) -> str:
    safe_id = html.escape(str(incident_id or ""))
    href = urllib.parse.quote(str(incident_id or ""), safe="")
    return f'<a href="/admin/?history={href}#event-history"><code>{safe_id}</code></a>'


def _history_children_display(item: dict[str, Any]) -> str:
    children = item.get("children", [])
    if not children:
        return "none"
    return "<br>".join(_history_incident_link(child.get("incident_id")) for child in children)


def _history_health_plans_display(item: dict[str, Any]) -> str:
    plans = item.get("health_plans")
    if not isinstance(plans, list) or not plans:
        return ""
    labels = []
    for plan in plans[:3]:
        if not isinstance(plan, dict):
            continue
        plan_id = html.escape(str(plan.get("plan_id") or ""))
        title = html.escape(str(plan.get("title") or plan.get("plan_id") or ""))
        labels.append(f"<span>{plan_id}: {title}</span>")
    if not labels:
        return ""
    return f'<div class="health-plans"><span class="hint">Health plans ({len(labels)}):</span><br>' + "<br>".join(labels) + "</div>"


def _health_value(value: Any, *keys: str) -> str:
    if not isinstance(value, dict):
        return "—"
    for key in keys:
        candidate = value.get(key)
        if candidate not in (None, "", []):
            if isinstance(candidate, bool):
                return "healthy" if candidate else "degraded"
            if isinstance(candidate, list):
                return ", ".join(str(item) for item in candidate[:4]) or "—"
            return str(candidate)
    return "—"


def _health_dashboard(health: dict[str, Any], csrf: str) -> str:
    config = health.get("config", {}) if isinstance(health, dict) else {}
    targets = []
    for target in config.get("targets", []) if isinstance(config, dict) else []:
        if not isinstance(target, dict):
            continue
        keywords = [str(keyword) for rule in target.get("log_rules", []) if isinstance(rule, dict) for keyword in rule.get("keywords", [])[:32]]
        targets.append(f'<tr><td><strong>{html.escape(str(target.get("host_id") or target.get("target") or ""))}</strong><br><span class="hint">{html.escape(str(target.get("target") or ""))}</span></td><td>{html.escape(str(target.get("cpu_warn_percent", 85)))}</td><td>{html.escape(str(target.get("ram_warn_percent", 85)))}</td><td>{html.escape(str(target.get("disk_warn_percent", 90)))}</td><td>{html.escape(", ".join(keywords) or "none")}</td></tr>')
    incidents = []
    for item in health.get("incidents", []) if isinstance(health, dict) else []:
        if not isinstance(item, dict):
            continue
        incident = item.get("incident") if isinstance(item.get("incident"), dict) else {}
        plans = item.get("plans") if isinstance(item.get("plans"), list) else []
        plan_titles = ", ".join(str(plan.get("title") or plan.get("plan_id") or "") for plan in plans[:3] if isinstance(plan, dict))
        incidents.append(f'<tr><td>{_history_incident_link(incident.get("id"))}<br><strong>{html.escape(str(incident.get("title") or "Health incident"))}</strong><br>{_badge(incident.get("state"), _STATE_BADGE_CLASSES)}</td><td>{html.escape(plan_titles or "—")}</td><td>{html.escape(_health_value(item.get("selection"), "plan_id"))}</td><td>{html.escape(_health_value(item.get("progress"), "step", "observed_state", "status"))}<br><span class="hint">{html.escape(_health_value(item.get("progress"), "evidence_refs", "evidence"))}</span></td><td>{html.escape(_health_value(item.get("verification"), "observed_state", "status", "healthy"))}<br><span class="hint">{html.escape(_health_value(item.get("verification"), "verification_id", "evidence"))}</span></td></tr>')
    integration = health.get("integration", {}) if isinstance(health, dict) else {}
    return f'''<section id="health-dashboard" class="card health-hero"><div class="health-heading"><div class="card-head"><span class="eyebrow">Fleet operations</span><h2>Health dashboard</h2><p class="hint">Targets, active incidents, remediation receipts, and independent verification from the live NoticePlace store.</p></div><div class="status-card"><span class="pulse"></span>{html.escape(str(health.get("config_status") or "Monitor config unavailable"))}<br><small>NoticePlace {html.escape(str(integration.get("noticeplace") or "unknown"))} · monitor {html.escape(str(integration.get("fleet_monitor") or "unknown"))} · {html.escape(str(integration.get("targets") or 0))} targets</small></div></div><h3>Fleet targets</h3><div class="table-wrap"><table><tr><th>Target</th><th>CPU %</th><th>RAM %</th><th>Disk %</th><th>Log keywords</th></tr>{''.join(targets) or '<tr><td colspan="5">No fleet targets configured.</td></tr>'}</table></div><h3>Open health incidents</h3><div class="table-wrap"><table><tr><th>Incident</th><th>Latest plans</th><th>Selected</th><th>Progress</th><th>Verification</th></tr>{''.join(incidents) or '<tr><td colspan="5">No open health incidents.</td></tr>'}</table></div><details><summary>Edit monitor thresholds and log keywords</summary><p class="hint">Operator-owned file: <code>{html.escape(str(health.get("config_path") or ""))}</code>. Only bounded fleet targets, thresholds, and explicit log rules are accepted.</p><form class="health-config" method="post" action="/admin/health-settings"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><textarea name="config_json" rows="18" spellcheck="false">{html.escape(str(health.get("config_json") or '{"targets": []}'))}</textarea><label><input type="checkbox" name="run_monitor" value="true"> Run health monitor once after save</label><button>Save health settings</button></form></details></section>'''


def _dashboard(snapshot: dict[str, Any], csrf: str) -> str:
    health_dashboard = _health_dashboard(snapshot.get("health", {}), csrf)
    options = "".join(f'<option value="{severity}">{severity}</option>' for severity in SEVERITIES)
    project_rows = "".join(
        f'<tr><td><strong>{html.escape(item["project"])}</strong></td><td>{_badge(item["max_severity"])}</td><td><code>{html.escape(item["fingerprint"])}</code></td><td class="row-actions"><form method="post" action="/admin/projects/{urllib.parse.quote(item["project"], safe="")}/severity"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><select name="max_severity">{options}</select><button>Save level</button></form><form method="post" action="/admin/projects/{urllib.parse.quote(item["project"], safe="")}/revoke"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><button class="danger">Revoke</button></form></td></tr>'
        for item in snapshot["projects"]
    ) or '<tr><td colspan="4">No producer projects yet.</td></tr>'
    topics = snapshot.get("topics", [])
    topic_rows = "".join(
        f'<tr><td>{html.escape(topic["name"])}</td><td>{html.escape(topic["id"])}</td><td>{html.escape(topic["chat_id"])}</td><td>{html.escape(str(topic["message_thread_id"] or ""))}</td><td><a href="#topic-{html.escape(topic["id"])}">Edit</a></td></tr>'
        for topic in topics
    ) or '<tr><td colspan="5">No topics yet.</td></tr>'
    topic_forms = "".join(
        f'<div class="topic-card"><form id="topic-{html.escape(topic["id"])}" method="post" action="/admin/topics/save"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="{html.escape(topic["id"])}"><input required name="name" value="{html.escape(topic["name"])}" placeholder="Topic name"><input required name="chat_id" value="{html.escape(topic["chat_id"])}" placeholder="-100…"><input name="message_thread_id" value="{html.escape(str(topic["message_thread_id"] or ""))}" placeholder="blank = create" inputmode="numeric"><label><input type="checkbox" name="enabled" value="true" {"checked" if topic["enabled"] else ""}> active</label><button>Save</button></form><form method="post" action="/admin/topics/delete"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="{html.escape(topic["id"])}"><button class="danger">Delete</button></form></div>'
        for topic in topics
    )
    topic_forms += f'<div class="topic-card"><form method="post" action="/admin/topics/save"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="topic_id" value="new-topic"><input required name="name" placeholder="New topic name"><input required name="chat_id" placeholder="-100…"><input name="message_thread_id" placeholder="blank = create" inputmode="numeric"><label><input type="checkbox" name="enabled" value="true" checked> active</label><button>Create topic</button></form></div>'
    consumer_rows = "".join(
        f'<tr><td>{html.escape(item["name"])}</td><td>{html.escape(item.get("profile_key") or "custom")}</td><td>{html.escape(item["project"])}</td><td><code>{html.escape(item["token_fingerprint"])}</code></td><td>{html.escape(_policy_display(item["policy"]))}</td><td>{html.escape(_quiet_hours_display(item.get("quiet_hours", [])))}</td><td>{html.escape(item.get("operator_note") or "")}</td></tr>'
        for item in snapshot["consumers"]
    ) or '<tr><td colspan="7">No delivery profiles yet.</td></tr>'
    history_rows = "".join(
        f'<tr><td><code>{html.escape(str(item["event_id"]))}</code><br>{_history_incident_link(item["incident_id"])}<br><span class="hint">{html.escape(_history_time_display(item.get("event_created_at")))}</span></td><td><strong>{html.escape(str(item.get("event_type") or ""))}</strong><br>{html.escape(str(item.get("title") or ""))}<br><span class="hint">{html.escape(str(item.get("project") or ""))}/{html.escape(str(item.get("recipient") or ""))}</span>{_history_health_plans_display(item)}</td><td>{html.escape(str(item.get("producer") or ""))}<br><span class="hint">{html.escape(str(item.get("plugin") or ""))}</span></td><td>{html.escape(str(item.get("source_ip") or ""))}<br><span class="hint">proxy: {html.escape(str(item.get("proxy_ip") or "direct"))}</span></td><td>{_history_incident_link(item["parent_incident_id"]) if item.get("parent_incident_id") else "root"}<br><span class="hint">children:</span><br>{_history_children_display(item)}</td><td>{_history_notification_display(item)}</td><td>{_badge(item.get("outcome", {}).get("incident_state") or item.get("state"), _STATE_BADGE_CLASSES)}<br><span class="hint">{html.escape(str(item.get("correlation_id") or ""))}</span></td></tr>'
        for item in snapshot.get("event_history", [])
    ) or '<tr><td colspan="7">No events recorded yet.</td></tr>'
    calls_enabled = bool(snapshot.get("automatic_calls_enabled", True))
    calls_label = "Enabled" if calls_enabled else "Disabled"
    calls_action = "false" if calls_enabled else "true"
    calls_button = "Disable automatic calls" if calls_enabled else "Enable automatic calls"
    runtime_settings = snapshot.get("runtime_settings", {})
    setting_labels = {
        "call_repeat_min_interval_seconds": "Пауза между звонками об одной проблеме (сек.)",
        "matrix_call_critical_escalation_seconds": "Matrix critical call delay (s)",
        "matrix_call_emergency_escalation_seconds": "Matrix emergency call delay (s)",
        "android_phone_call_escalation_seconds": "Android phone call delay (s)",
        "android_phone_emergency_call_escalation_seconds": "Emergency phone call delay (s)",
        "android_phone_quiet_start_hour": "Phone quiet hours start (Moscow hour)",
        "android_phone_quiet_end_hour": "Phone quiet hours end (Moscow hour)",
        "android_telegram_call_escalation_seconds": "Android Telegram call delay (s)",
        "telegram_critical_repeat_seconds": "Critical Telegram repeat delay (s)",
    }
    setting_inputs = "".join(
        f'<label>{html.escape(label)} <input required name="{key}" type="number" min="0" step="any" value="{html.escape(str(runtime_settings.get(key, "0")))}"></label>'
        for key, label in setting_labels.items()
    )
    adapter_builder = """<div id="adapter-steps"></div>
<button type="button" id="add-adapter-step">+ Add adapter step</button>
<script>
const steps = document.getElementById('adapter-steps');
function refreshPrevious() {
  [...steps.querySelectorAll('[data-step]')].forEach((row, index) => {
    const select = row.querySelector('[data-previous]');
    const current = select.value;
    select.innerHTML = '<option value="">First step</option>' + [...steps.querySelectorAll('[data-step]')].slice(0, index).map((_, i) => `<option value="step-${i + 1}">After step ${i + 1}</option>`).join('');
    select.value = current;
  });
}
function addStep() {
  const index = steps.children.length + 1;
  const row = document.createElement('fieldset');
  row.dataset.step = index;
  row.innerHTML = `<legend>Step ${index}</legend><select data-platform><option>telegram</option><option>matrix</option><option>whatsapp</option><option>phone</option></select><select data-action><option>message</option><option>call</option></select><input data-target required placeholder='{"chat_id":-100123}'><input data-retry type="number" min="1" value="3600"><input data-repeats type="number" min="1" value="1"><select data-previous><option value="">First step</option></select><button type="button" data-remove>Remove</button>`;
  row.querySelector('[data-remove]').onclick = () => { row.remove(); [...steps.children].forEach((item, i) => item.querySelector('legend').textContent = `Step ${i + 1}`); refreshPrevious(); };
  steps.append(row); refreshPrevious();
}
document.getElementById('add-adapter-step').onclick = addStep;
</script>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NoticePlace Admin</title><style>{_ADMIN_CSS}</style></head><body><header class="topbar"><span class="brand">NoticePlace <small>admin console</small></span><span class="shield">Protected operator route</span></header><main>
<div class="page-head"><h1>NoticePlace</h1><p>Producer scopes, delivery chains, and Telegram topics. Delivery credentials remain server-only.</p></div>
{health_dashboard}
<section class="card" id="add-producer"><div class="card-head"><h2>Add producer</h2></div><form class="card-form" method="post" action="/admin/projects"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input required name="project" pattern="[A-Za-z0-9._-]+" placeholder="my-service"><select name="max_severity">{options}</select><button>Create one-time token</button></form></section>
<section class="card"><div class="card-head"><h2>Producer projects</h2></div><div class="table-wrap"><table><tr><th>Project</th><th>Maximum level</th><th>Token fingerprint</th><th>Actions</th></tr>{project_rows}</table></div></section>
<section class="card"><div class="card-head"><h2>Automatic call escalation</h2><p class="hint">{calls_label}. This controls future Android phone, Telegram-call, and Matrix-call escalations. Text notifications are unchanged; an already active phone call cannot be interrupted.</p></div><form class="card-form" method="post" action="/admin/calls"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="enabled" value="{calls_action}"><button>{calls_button}</button></form></section>
<section class="card"><div class="card-head"><h2>Test adapters</h2><p class="hint">These buttons use the central Notify HTTP/MCP boundary. Phone starts one short test call; message sends one notice to the configured test recipient.</p></div><form class="card-form" method="post" action="/admin/test-adapter" onsubmit="return confirm('Start one test phone call now?')"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="adapter" value="phone"><input name="message" value="NoticePlace phone adapter test" maxlength="300"><button>Call me</button></form><form class="card-form" method="post" action="/admin/test-adapter"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input type="hidden" name="adapter" value="message"><input name="message" value="NoticePlace message adapter test" maxlength="300"><button>Send message</button></form></section>
<section class="card"><div class="card-head"><h2>Live delivery timers</h2><p class="hint">Changes apply to newly scheduled/retried deliveries and do not restart Notify. Zero disables that timer. An already executing adapter call is unchanged.</p></div><form class="settings" method="post" action="/admin/settings"><input type="hidden" name="csrf" value="{html.escape(csrf)}">{setting_inputs}<button>Save live settings</button></form></section>
<section class="card"><div class="card-head"><h2>Add scoped consumer</h2><p class="hint">Create the delivery chain visually. Choose platform, action, target, retry interval, repeats, optional predecessor, and an operator note.</p></div><form class="consumer-form" method="post" action="/admin/consumers"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><input required name="name" placeholder="Gateway producer"><input required name="project" pattern="[A-Za-z0-9._-]+" placeholder="hermes"><input name="operator_note" maxlength="500" placeholder="Optional operator note"><select name="max_severity">{options}</select><input type="hidden" name="policy_json" id="policy-json"><div id="adapter-builder">{adapter_builder}</div><button type="submit" onclick="return buildPolicy()">Create consumer intake</button></form><p class="hint">Target example: <code>{{"chat_id":-100123,"topic_id":122}}</code> or <code>{{"phone_number":"+79990000000"}}</code>.</p></section>
<section class="card"><div class="card-head"><h2>Delivery profiles</h2></div><div class="table-wrap"><table><tr><th>Name</th><th>Profile</th><th>Project</th><th>Token fingerprint</th><th>Ordered delivery policy</th><th>Quiet hours</th><th>Operator note</th></tr>{consumer_rows}</table></div></section>
<section class="card" id="event-history"><div class="card-head"><h2>Event history</h2><p class="hint">Ingress, parent/child links, source/proxy metadata, notifications, and final state. Bearer tokens and secrets are never shown.</p></div><form class="toolbar" method="get" action="/admin/"><input name="history" value="{html.escape(str(snapshot.get("event_history_query") or ""))}" placeholder="event type, producer, plugin, correlation, incident"><button>Filter</button><a href="/admin/#event-history">Reset</a></form><div class="table-wrap"><table><tr><th>Event / incident / time</th><th>Type / title / project</th><th>Producer / plugin</th><th>Source IP / proxy</th><th>Parent / children</th><th>Notifications</th><th>Outcome / correlation</th></tr>{history_rows}</table></div></section>
<section class="card"><div class="card-head"><h2>Telegram topics</h2><p class="hint">All topics are equal. Some were created by the initial configuration, but they can be edited or deleted exactly like any other topic. Changes apply live without restarting Notify.</p></div><div class="table-wrap"><table><tr><th>Name</th><th>Key</th><th>Chat</th><th>Topic</th><th>Action</th></tr>{topic_rows}</table></div><div class="topic-forms">{topic_forms}</div></section></main><script>
function buildPolicy() {{ const rows = [...document.querySelectorAll('#adapter-steps [data-step]')]; const policy = rows.map((row, index) => {{ let target; try {{ target = JSON.parse(row.querySelector('[data-target]').value); }} catch (_) {{ target = {{}}; }} return {{id:`step-${{index + 1}}`, platform:row.querySelector('[data-platform]').value, action:row.querySelector('[data-action]').value, target, retry_interval_seconds:Number(row.querySelector('[data-retry]').value), max_repeats:Number(row.querySelector('[data-repeats]').value), previous_step_id:row.querySelector('[data-previous]').value || null}}; }}); document.getElementById('policy-json').value = JSON.stringify(policy); return policy.length > 0; }}
addStep();
</script></body></html>"""


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


def _quiet_hours_display(quiet_hours: list[dict[str, Any]]) -> str:
    """Keep per-consumer quiet-hour policy visible without exposing secrets."""
    labels = []
    for rule in quiet_hours:
        suppressed = ", ".join(str(item) for item in rule.get("suppress", [])) or "nothing"
        labels.append(f'{rule.get("start", "")}-{rule.get("end", "")} {rule.get("timezone", "")}: {suppressed}')
    return " · ".join(labels) or "none"


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
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect {safe_project}</title><style>{_ADMIN_CSS}</style></head><body><header class="topbar"><span class="brand">NoticePlace <small>producer onboarding</small></span></header><main class="narrow"><div class="page-head"><h1>Connect {safe_project}</h1><p class="warning">Copy the token now. It is not available after leaving this page; the console retains only a fingerprint.</p></div><div class="token-box"><code>{html.escape(token)}</code></div><section class="card"><div class="card-head"><h2>1. Store it as a service secret</h2></div><pre># {env_path} (owner root, mode 0600)
NOTIFY_CENTER_EVENT_URL=https://notify.bezrabotnyi.com/v1/events
NOTIFY_CENTER_TOKEN=&lt;paste the token above here&gt;</pre><p class="hint">For systemd use <code>EnvironmentFile={env_path}</code>. Do not put the token in a unit command, Git, or shell history.</p></section><section class="card"><div class="card-head"><h2>2. Send with curl</h2></div><pre>{html.escape(curl)}</pre></section><section class="card"><div class="card-head"><h2>Or use a small SDK</h2></div><pre># Python: pip install 'git+https://github.com/megamen32/noticeplace.git#subdirectory=python'
{html.escape(python)}

# Node.js: npm install github:megamen32/noticeplace
{html.escape(node)}</pre></section><p class="hint"><a href="https://github.com/megamen32/noticeplace">GitHub repository</a> · <a href="https://github.com/megamen32/noticeplace/blob/main/docs/producer-sdk.md">Full producer guide</a> · <a href="/admin/">Back to admin</a></p></main></body></html>"""


def _consumer_token_page(name: str, token: str) -> str:
    safe_name = html.escape(name)
    intake_url = "https://notify.bezrabotnyi.com/v1/events"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect {safe_name}</title><style>{_ADMIN_CSS}</style></head><body><header class="topbar"><span class="brand">NoticePlace <small>consumer onboarding</small></span></header><main class="narrow"><div class="page-head"><h1>Connect {safe_name}</h1><p class="warning">Copy this intake URL and token now. The token is shown only on this page; the console retains only its fingerprint.</p></div><section class="card"><div class="card-head"><h2>Intake URL</h2></div><div class="token-box"><code>{intake_url}</code></div></section><section class="card"><div class="card-head"><h2>Scoped bearer token</h2></div><div class="token-box"><code>{html.escape(token)}</code></div></section><p class="hint">Delivery targets are fixed by the operator policy and cannot be selected by producer events.</p><p class="hint"><a href="/admin/">Back to admin</a></p></main></body></html>"""


def run_admin_http(store: AdminConfigStore, csrf_secret: str, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), build_admin_handler(store, csrf_secret)).serve_forever()
