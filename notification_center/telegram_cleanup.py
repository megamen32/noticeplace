"""Explicit per-project opt-in; durable card receipts remain the source of truth."""
from __future__ import annotations

from typing import Any, Mapping

SETTING = 'telegram_delete_resolved:'


def enabled(center: Any, project: str) -> bool:
    return center.get_runtime_setting(SETTING + project, 'false') == 'true'


def should_delete(center: Any, payload: Mapping[str, Any]) -> bool:
    if center is None:
        return False
    incident = payload['incident']
    if (not enabled(center, str(incident.get('project') or ''))
            or incident.get('kind', 'incident') != 'incident' or incident.get('state') != 'resolved'
            or (payload.get('health_outcome') or {}).get('status') == 'phase_changed'):
        return False
    members = payload.get('group_members') or [incident]
    questions = [*(payload.get('human_questions') or []), payload.get('human_question') or {}]
    return (all(x.get('state') == 'resolved' for x in members)
            and not any(x.get('state') == 'pending' for x in questions))
