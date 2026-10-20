"""Durable, opt-in question calls using the existing Notice Place executor.

The transport must enforce consent and the deadline at the actual dial boundary,
and emit a call-bound confirmed answer. Ordinary transcripts are never answers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ValidationError

PROTOCOL = 'noticeplace.question.v1'
PREFERENCE_PREFIX = 'question-phone:'


def call_window(preferences, when):
    """Return admission and the exclusive UTC deadline; never infer a timezone."""
    try:
        if (preferences.get('enabled') is not True or preferences.get('muted') is True
                or not preferences.get('approval_ref') or when.tzinfo is None):
            return False, None
        zone = ZoneInfo(preferences['timezone'])
        days = preferences['weekdays']
        if (not isinstance(days, list) or not days
                or any(type(day) is not int or day not in range(7) for day in days)):
            return False, None
        start, end = [datetime.strptime(preferences[key], '%H:%M').time() for key in ('start', 'end')]
        if start >= end:
            return False, None
        local = when.astimezone(zone)
        if local.weekday() not in days or not start <= local.time().replace(tzinfo=None) < end:
            return False, None
        # Walking UTC minutes handles both occurrences of a folded local hour.
        cursor = when.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(26 * 60):
            candidate = cursor.astimezone(zone)
            if candidate.weekday() not in days or not start <= candidate.time().replace(tzinfo=None) < end:
                return True, cursor.timestamp()
            cursor += timedelta(minutes=1)
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        pass
    return False, None


def _read_herder(path, body=None):
    """Read the already deployed loopback authority, with bounded JSON/SSE frames."""
    from .agent_job_helper import _herder_loopback_profile
    parsed = urllib.parse.urlsplit(_herder_loopback_profile()['url'])
    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    token = os.environ.get('NOTIFY_HERDER_MCP_TOKEN', '')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise ValueError('authority response too large')
    if raw.startswith(b'event:') or raw.startswith(b'data:'):
        frames = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith(b'data:')]
        if len(frames) != 1:
            raise ValueError('ambiguous authority response')
        return frames[0]
    return json.loads(raw)


def verified_source(request):
    """Verify the exact existing binding and a live, non-stopped native source."""
    try:
        envelope = _read_herder('/mcp', {'jsonrpc': '2.0', 'id': 'question-source', 'method': 'tools/call',
            'params': {'name': 'human_request_get', 'arguments': {'requestId': request['request_id']}}})
        binding = envelope['result']['structuredContent']['request']
        target = binding['target']
        if (binding['kind'] != 'user' or binding['status'] != 'pending'
                or target['harness'] != request['native_harness']
                or target['sessionId'] != request['native_session_id']
                or target['cwd'] != request['native_cwd']):
            return False
        harness, session = [urllib.parse.quote(request[key], safe='')
                            for key in ('native_harness', 'native_session_id')]
        native = _read_herder(f'/api/sessions/{harness}/{session}')['session']
        if (native['id'] != request['native_session_id'] or native['harness'] != request['native_harness']
                or native['cwd'] != request['native_cwd'] or native['status'] not in {'running', 'idle', 'needs_input'}):
            return False
        query = urllib.parse.urlencode({'harness': request['native_harness'], 'sessionId': request['native_session_id'],
                                       'cwd': request['native_cwd'], 'consume': '0'})
        return _read_herder('/api/coordination/context?' + query).get('humanStopHeld') is False
    except (OSError, ValueError, KeyError, TypeError):
        return False


class QuestionCalls:
    """One call attempt per request. Unknown or accepted attempts never redial."""

    def __init__(self, center, phone, *, guard=verified_source, complete=None, clock=None):
        self.center, self.phone, self.guard = center, phone, guard
        self.complete = complete
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def set_preferences(self, recipient, preferences):
        """Operator-only helper; never exposed through producer-controlled input."""
        if not recipient or len(recipient) > 80:
            raise ValidationError('question recipient is invalid')
        self.center.set_runtime_setting(PREFERENCE_PREFIX + recipient, json.dumps(preferences, ensure_ascii=False))

    def preferences(self, recipient):
        try:
            value = json.loads(self.center.get_runtime_setting(PREFERENCE_PREFIX + recipient, '{}'))
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def receipt(self, request_id):
        with self.center._lock:
            row = self.center._connection.execute('SELECT * FROM question_phone_calls WHERE request_id=?', (request_id,)).fetchone()
        return dict(row) if row else {}

    def has_due(self):
        with self.center._lock:
            return self.center._connection.execute("SELECT 1 FROM question_phone_calls WHERE state='queued' "
                'AND next_attempt_at<=? LIMIT 1', (self.clock().timestamp(),)).fetchone() is not None

    def _eligible(self, request, preferences):
        if (request['state'] != 'pending' or request.get('kind') != 'user'
                or request['mode'] not in {'question', 'choice'} or not request.get('native_session_id')
                or preferences.get('locale') != 'ru-RU'
                or preferences.get('target_ref') != 'agentcall.configured_owner'
                or preferences.get('actor') not in request['allowed_actors']):
            return False
        if request.get('incident_id'):
            incident = self.center.get_incident(request['incident_id'])
            if not incident or incident['state'] == 'resolved' or incident.get('notifications_muted'):
                return False
        return preferences.get('enabled') is True and preferences.get('muted') is not True

    def _update(self, request_id, state, reason='', *, from_state=None, **fields):
        allowed = {'call_id', 'deadline', 'actor', 'next_attempt_at'}
        assert set(fields) <= allowed
        with self.center._lock, self.center._connection:
            values = {'state': state, 'reason': reason, **fields}
            self.center._connection.execute('UPDATE question_phone_calls SET ' + ','.join(key + '=?' for key in values)
                + ' WHERE request_id=?' + (' AND state=?' if from_state else ''),
                (*values.values(), request_id, *((from_state,) if from_state else ())))

    def run_once(self):
        """Run in the existing call executor; no timer, watcher or new process."""
        now = self.clock()
        with self.center._lock:
            row = self.center._connection.execute("SELECT request_id FROM question_phone_calls WHERE state='queued' "
                'AND next_attempt_at<=? ORDER BY created_at LIMIT 1', (now.timestamp(),)).fetchone()
        if row is None:
            return False
        request_id = row['request_id']
        request = self.center.get_human_request_from_telegram(request_id)
        preferences = self.preferences(request['recipient'])
        if not self._eligible(request, preferences):
            self._update(request_id, 'fallback', 'not_authorized_or_pending', from_state='queued')
            return False
        allowed, deadline = call_window(preferences, now)
        if not allowed:
            self._update(request_id, 'queued', 'outside_approved_window', from_state='queued', next_attempt_at=now.timestamp() + 60)
            return False
        if not self.guard(request):
            self._update(request_id, 'fallback', 'source_not_verified', from_state='queued')
            return False
        try:
            readiness = self.phone.readiness()
        except Exception:
            readiness = {}
        if (not isinstance(readiness, dict) or readiness.get('ready') is not True
                or readiness.get('question_ready') is not True):
            self._update(request_id, 'fallback', 'interactive_gateway_unavailable', from_state='queued')
            return False
        # Acquire the SQL writer fence before reserving; independent API replicas
        # cannot both win, and a crashed sender is never reclaimed automatically.
        with self.center._lock, self.center._connection:
            claimed = self.center._connection.execute("UPDATE question_phone_calls SET state='sending',deadline=?,actor=? "
                "WHERE request_id=? AND state='queued'", (deadline, preferences['actor'], request_id)).rowcount
        if claimed != 1:
            return False
        try:
            current = self.center.get_human_request_from_telegram(request_id)
            preferences = self.preferences(current['recipient'])
            allowed, deadline = call_window(preferences, self.clock())
            if not allowed or not self._eligible(current, preferences) or not self.guard(current):
                self._update(request_id, 'fallback', 'final_admission_denied')
                return False
            attempt = self.receipt(request_id)['attempt_id']
            result = self.phone.question_call({
                'protocol': PROTOCOL, 'request_id': request_id, 'attempt_id': attempt,
                'not_after': deadline, 'locale': preferences['locale'], 'actor': preferences['actor'],
                'target_ref': preferences['target_ref'], 'question': current['message'], 'choices': current['choices'],
                'message': 'Здравствуйте, это служба уведомлений. ' + current['message'],
                'require_readback': True, 'require_confirmation': True,
            })
            if (not isinstance(result, dict) or result.get('ok') is not True
                    or result.get('protocol') != PROTOCOL or not result.get('call_id')):
                self._update(request_id, 'unknown', 'invalid_native_receipt')
                return False
            self._update(request_id, 'active', call_id=str(result['call_id'])[:128], deadline=deadline)
            return True
        except Exception:
            # Do not copy provider errors: they may contain a number or transcript.
            self._update(request_id, 'unknown', 'transport_unavailable_or_unknown')
            return False

    def handle_event(self, event):
        """Only the existing authenticated gateway stream may call this method.

        A future question-capable transport must emit structured receipts after
        playing the exact readback and hearing an explicit confirmation. Its
        ordinary STT text cannot call this path or become a canonical answer.
        """
        call_id = str(event.get('callId') or '')
        if not call_id:
            return False
        with self.center._lock:
            row = self.center._connection.execute("SELECT * FROM question_phone_calls WHERE call_id=? AND state='active'", (call_id,)).fetchone()
        if row is None:
            return False
        attempt = dict(row)
        request_id = attempt['request_id']
        if event.get('event') in {'ended', 'error', 'no_answer', 'voicemail'}:
            self._update(request_id, 'fallback', 'call_ended_without_answer', from_state='active')
            return False
        if (event.get('event') != 'question_answer' or event.get('confirmed') is not True
                or event.get('readback') is not True or event.get('protocol') != PROTOCOL
                or event.get('request_id') != request_id or event.get('attempt_id') != attempt['attempt_id']
                or event.get('actor') != attempt['actor']
                or event.get('input_method') not in {'speech', 'dtmf'}):
            return False
        request = self.center.get_human_request_from_telegram(request_id)
        preferences = self.preferences(request['recipient'])
        if request['state'] != 'pending':
            reason = 'canonical_answer_already_resolved' if request['state'] == 'resolved' else 'request_withdrawn'
            self._update(request_id, 'fallback', reason, from_state='active')
            return False
        if (not self._eligible(request, preferences) or preferences.get('actor') != attempt['actor']
                or not self.guard(request)):
            return False
        value = event.get('value')
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            return False
        if request['mode'] == 'choice' and value not in {choice['value'] for choice in request['choices']}:
            return False
        with self.center._lock, self.center._connection:
            claimed = self.center._connection.execute("UPDATE question_phone_calls SET state='confirming' WHERE request_id=? AND state='active'",
                                                       (request_id,)).rowcount
        if claimed != 1:
            return False
        try:
            if self.complete:
                result = self.complete(self.center, request, value, attempt['actor'], 'phone')
            else:
                from .http_api import complete_human_request
                result = complete_human_request(self.center, request, value, attempt['actor'], 'phone')
            accepted = result.get('state') == 'resolved' and result.get('value') == value
            self._update(request_id, 'completed' if accepted else 'fallback',
                         '' if accepted else 'canonical_answer_conflict', from_state='confirming')
            return accepted
        except Exception:
            self._update(request_id, 'unknown', 'completion_outcome_unknown')
            return False
