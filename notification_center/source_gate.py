"""Bounded, fail-closed admission checks against Herder's human-stop authority."""
from __future__ import annotations

from time import monotonic
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

MAX_SOURCE_SESSIONS = 32
_SOURCE_HARNESSES = {'codex', 'zcode', 'opencode'}


class HealthSourceGateBlocked(RuntimeError):
    """A related background launch needs human handling instead of another retry."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f'Notice automatic source gate blocked: {reason}')


def source_chain(*groups: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        if group is None:
            continue
        if not isinstance(group, list):
            raise HealthSourceGateBlocked('source_receipt_invalid')
        if len(group) > MAX_SOURCE_SESSIONS:
            raise HealthSourceGateBlocked('source_chain_exceeds_32_manual_handling')
        for item in group:
            if not isinstance(item, Mapping):
                raise HealthSourceGateBlocked('source_receipt_invalid')
            harness, native_id = item.get('harness'), item.get('sessionId')
            if (not isinstance(harness, str) or harness not in _SOURCE_HARNESSES or not isinstance(native_id, str)
                    or not native_id or native_id != native_id.strip() or len(native_id) > 512
                    or any(ord(c) < 32 or ord(c) == 127 for c in native_id)):
                raise HealthSourceGateBlocked('source_receipt_invalid')
            key = (harness, native_id)
            if key not in seen:
                seen.add(key)
                result.append({'harness': harness, 'sessionId': native_id})
                if len(result) > MAX_SOURCE_SESSIONS:
                    raise HealthSourceGateBlocked('source_chain_exceeds_32_manual_handling')
    return result


def event_sources(event: Mapping[str, Any]) -> list[dict[str, str]]:
    health = event.get('health')
    return source_chain(health.get('source_sessions') if isinstance(health, Mapping) else None)


def source_launch_fields(profile: Mapping[str, str], sources: list[dict[str, str]], read_json: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Check without consuming/touching inbox; the server checks the same list again."""
    sources = source_chain(sources)
    parsed = urllib.parse.urlsplit(profile['url'])
    deadline = monotonic() + 15
    for source in sources:
        query = urllib.parse.urlencode({**source, 'cwd': profile['cwd'], 'consume': '0'})
        url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/api/coordination/context', query, ''))
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise HealthSourceGateBlocked('source_context_timeout')
        try:
            state = read_json(urllib.request.Request(url, headers={'Accept':'application/json'}, method='GET'), timeout=min(5, remaining))
        except Exception as error:
            raise HealthSourceGateBlocked('source_context_unavailable') from error
        if not isinstance(state, Mapping) or type(state.get('humanStopHeld')) is not bool:
            raise HealthSourceGateBlocked('source_context_unavailable')
        if state['humanStopHeld']:
            raise HealthSourceGateBlocked('human_stop_held')
    fields: dict[str, Any] = {'sourceSessions': sources}
    if sources:
        fields.update(sourceHarness=sources[0]['harness'], sourceSessionId=sources[0]['sessionId'])
    return fields
