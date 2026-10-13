"""Bounded native readback in the existing delivery pool; no extra scheduler."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any, Mapping

from .agent_job_helper import _read_json_request, _session_endpoint
from .health_session_lifecycle import _archive_profile
from .health_workflow import sanitize_bounded_text
from .incident_card import setting


def safe_projection(details: Mapping[str, Any]) -> dict[str, Any]:
    session = details.get('session') if isinstance(details.get('session'), Mapping) else {}
    history = details.get('history') if isinstance(details.get('history'), Mapping) else {}
    activity = session.get('lastActivity')
    result = {'status': str(session.get('status') or '')[:40], 'needs_permission': session.get('needsPermission') is True,
              'last_activity': activity if isinstance(activity, (str, int, float)) else None, 'history_complete': history.get('complete') is True}
    messages = history.get('messages') if isinstance(history, Mapping) else None
    if not isinstance(messages, list):
        messages = details.get('messages')
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, Mapping) or message.get('role') != 'assistant':
            continue
        parts = message.get('parts')
        text = '\n'.join(p['text'] for p in parts if isinstance(p, Mapping) and p.get('type') == 'text' and isinstance(p.get('text'), str)) if isinstance(parts, list) else ''
        text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})\b',
                      '[redacted]', sanitize_bounded_text(text, 2000))[:500]
        if text:
            result.update(assistant_text=text, assistant_id=str(message.get('id') or '')[:128],
                          assistant_timestamp=str(message.get('timestamp') or message.get('createdAt') or '')[:40])
            break
    return result


class IncidentProgressReader:
    def __init__(self, center: Any, profile: Mapping[str, str] | None = None, runner: Any = urllib.request.urlopen) -> None:
        self.center, self.profile, self.runner = center, profile, runner

    def run_once(self, limit: int = 2) -> int:
        if not setting(self.center, 'telegram_native_progress_enabled'):
            return 0
        now = time.time()
        with self.center._lock:
            rows = self.center._connection.execute(
                "SELECT id FROM incidents WHERE state!='resolved' AND (native_polled_at IS NULL OR native_polled_at<=?) "
                "AND EXISTS (SELECT 1 FROM events e WHERE e.incident_id=incidents.id AND e.event_type IN "
                "('health.diagnosis_session_started','health.orchestrator_session_started','health.remediation_session_started')) "
                "ORDER BY COALESCE(native_polled_at,0) LIMIT ?", (now-15, max(1, min(2, limit))),
            ).fetchall()
        count = 0
        for row in rows:
            incident_id = row['id']
            session = self.center.latest_agent_session(incident_id) or {}
            if not session.get('harness') or not session.get('session_id'):
                continue
            with self.center._lock, self.center._connection:
                claimed = self.center._connection.execute(
                    'UPDATE incidents SET native_polled_at=? WHERE id=? AND (native_polled_at IS NULL OR native_polled_at<=?)',
                    (now, incident_id, now-15))
                if claimed.rowcount != 1:
                    continue
            try:
                profile = dict(self.profile or _archive_profile())
                profile['harness'] = session['harness']
                request = urllib.request.Request(_session_endpoint(profile, session['session_id'], '/details?limit=5&history=auto'),
                                                 headers={'Accept': 'application/json'})
                details = _read_json_request(request, self.runner, timeout=5, response_limit=512*1024)
                projection = json.dumps(safe_projection(details), ensure_ascii=False, sort_keys=True)
            except (OSError, ValueError, TypeError, RuntimeError):
                # A read failure is no observation about whether the native
                # session is running; keep the last factual projection.
                continue
            with self.center._lock, self.center._connection:
                latest = self.center.latest_agent_session(incident_id) or {}
                if (latest.get('harness'), latest.get('session_id')) != (session['harness'], session['session_id']):
                    continue
                incident = self.center.get_incident(incident_id)
                if incident['state'] == 'resolved' or incident.get('native_projection_json') == projection:
                    continue
                self.center._connection.execute('UPDATE incidents SET native_projection_json=? WHERE id=?', (projection, incident_id))
                self.center.queue_incident_card_refresh(incident_id, now)
                count += 1
        return count
