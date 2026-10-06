"""Archive completed read-only controllers through Herder; never delete history."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from .agent_job_helper import (_load_profile, _validate_profile, default_profile_path,
    _read_json_request, _session_endpoint, _extract_health_diagnosis, _extract_health_result)
from .source_gate import source_launch_fields


class HealthSessionArchiver:
    """One small batch uses the existing agent-worker capacity and durable audit."""
    def __init__(self, center: Any, profile: dict[str, str] | None = None,
                 runner: Any = urllib.request.urlopen) -> None:
        self.center, self.profile, self.runner = center, profile, runner

    def _eligible(self, incident_id: str) -> bool:
        with self.center._lock:
            row = self.center._connection.execute(
                "SELECT state,event_type FROM incidents WHERE id=?", (incident_id,)).fetchone()
            active = self.center._connection.execute(
                "SELECT 1 FROM deliveries WHERE incident_id=? AND channel LIKE 'gptadmin.agent:%' "
                "AND status IN ('queued','claimed','sending','retry','uncertain') LIMIT 1", (incident_id,)).fetchone()
        return bool(row and row['state'] == 'resolved' and str(row['event_type']).startswith('health.')
                    and not active and not self.center.health_incident_is_synthetic(incident_id))

    def run_once(self, limit: int = 2) -> int:
        now = time.time()
        with self.center._lock:
            rows = self.center._connection.execute(
                "SELECT e.incident_id,e.event_type,e.payload_json FROM events e "
                "JOIN incidents i ON i.id=e.incident_id WHERE i.state='resolved' "
                "AND i.event_type LIKE 'health.%' "
                "AND e.event_type IN ('health.diagnosis_session_started','health.orchestrator_session_started') "
                "AND json_extract(e.payload_json,'$.harness')='codex' "
                "AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=e.incident_id "
                "AND d.channel LIKE 'gptadmin.agent:%' AND d.status IN ('queued','claimed','sending','retry','uncertain')) "
                "AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.incident_id=e.incident_id "
                "AND a.type='health_controller_archive' "
                "AND json_extract(a.payload_json,'$.session_id')=json_extract(e.payload_json,'$.session_id') "
                "AND (json_extract(a.payload_json,'$.status')='archived' OR a.created_at>?)) "
                "GROUP BY e.incident_id,json_extract(e.payload_json,'$.session_id') "
                "ORDER BY e.created_at LIMIT ?", (now - 3600, max(1, min(2, limit))),
            ).fetchall()
        count = 0
        for row in rows:
            incident_id = row['incident_id']
            if not self._eligible(incident_id):
                continue
            session = json.loads(row['payload_json'])
            native_id = session.get('session_id')
            if not isinstance(native_id, str) or not native_id or len(native_id) > 128:
                continue
            status, reason = 'deferred', 'endpoint_unavailable'
            try:
                profile = dict(self.profile or _validate_profile(_load_profile('health-diagnosis', default_profile_path())))
                profile['harness'] = 'codex'
                def read(request: Any, **kwargs: Any) -> dict[str, Any]:
                    return _read_json_request(request, self.runner, timeout=kwargs.pop('timeout', 5), **kwargs)
                with self.center._lock:
                    sources = self.center._health_source_sessions(incident_id)
                # This only reads stop context: consume=0, no prompt or manual flag.
                source_launch_fields(profile, sources, read)
                details = read(urllib.request.Request(_session_endpoint(profile, native_id, '/details?limit=1&history=auto'),
                    headers={'Accept': 'application/json'}), response_limit=512 * 1024)
                parser = _extract_health_diagnosis if row['event_type'] == 'health.diagnosis_session_started' else _extract_health_result
                if parser(details) is None:
                    reason = 'completed_readonly_result_missing'
                elif self._eligible(incident_id):
                    result = read(urllib.request.Request(_session_endpoint(profile, native_id, '/archive'),
                        data=b'{}', headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST'))
                    if result.get('ok') is True and result.get('sessionId') == native_id:
                        status, reason = 'archived', ''
                        count += 1
                    else:
                        reason = 'archive_rejected'
            except Exception as error:
                reason = str(getattr(error, 'reason', 'endpoint_unavailable'))[:128]
            with self.center._lock, self.center._connection:
                self.center._audit(incident_id, 'health_controller_archive', 'worker',
                    {'harness': 'codex', 'session_id': native_id, 'status': status, 'reason': reason})
        return count
