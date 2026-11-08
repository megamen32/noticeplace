"""Bounded question expiry and readback of old open incidents.

Age cannot establish recovery or abandonment. Incident rows are read-only here;
even a resolved binding may carry a native session, so only unbound ordinary
questions can expire. Their rows remain durable request-id replay fences.
"""
from contextlib import nullcontext
import json
import math
import time

from .retention import RetentionWorker

QUESTION_CURSOR_KEY = 'history_question_expiry_cursor'
INCIDENT_CURSOR_KEY = 'history_open_review_cursor'
REPORT_KEY = 'history_maintenance_report'

# Partial/legacy ownership is ownership too. Require absence rather than trying
# to interpret task decisions, STOP acknowledgements or native completion state.
_ABSENT_FIELDS = (
    'incident_id', 'native_harness', 'native_session_id', 'native_cwd',
    'response_value', 'response_actor', 'response_result_ref', 'resolved_at',
    'cancelled_at', 'cancelled_by', 'progress_json', 'telegram_progress_confirmed_at',
    'task_context_json', 'task_decision_ref', 'task_input_sha256', 'task_notified_at',
    'task_deadline_at', 'task_decision_source', 'task_decision_value',
    'task_decision_actor', 'task_decided_at', 'task_decision_evidence_json',
    'task_control_kind', 'task_control_id', 'task_control_requested_at',
    'task_control_lease_until', 'task_control_ack_ref', 'task_control_ack_epoch',
    'task_control_ack_at', 'task_resume_ref', 'task_resumed_at',
)
_UNOWNED = """h.state='pending' AND h.kind='user' AND h.mode IN ('question','choice')
 AND h.task_notification_state='not_applicable'
 AND h.task_control_state='clear' AND h.task_control_epoch=0
 AND h.task_control_effects_json='[]' AND h.task_late_instructions_json='[]'
 AND (h.telegram_document_state IS NULL OR h.telegram_document_state='sent')
 AND NOT EXISTS (SELECT 1 FROM human_request_completions c WHERE c.request_id=h.request_id)
 AND """ + ' AND '.join(f'h.{field} IS NULL' for field in _ABSENT_FIELDS)
_DUE = """((h.expires_at IS NOT NULL AND h.expires_at>=0 AND h.expires_at<=?)
 OR (h.expires_at IS NULL AND h.created_at>=0 AND h.created_at<?))"""
_ELIGIBLE = _UNOWNED + ' AND ' + _DUE


def _cursor(connection, key):
    row = connection.execute('SELECT value FROM runtime_settings WHERE key=?', (key,)).fetchone()
    try:
        value = int(row[0]) if row else 0
        return value if 0 <= value <= 9223372036854775807 else 0
    except (ValueError, TypeError):
        return 0


def _save(connection, key, value, now):
    connection.execute('INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
        (key, value, now))


def maintain_expiry(connection, *, now=None, cutoff=None, batch_size=100, dry_run=True):
    """Sample at most one page per table; report describes that page, not totals.

    Explicit future deadlines take precedence over the history age limit. A
    pending question without a deadline uses the existing history retention
    setting. Apply changes only state to expired: no answer, resume or recovery.
    Both cursors cycle so a protected row is revisited on later sweeps.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be 1..1000')
    now = time.time() if now is None else float(now)
    cutoff = now - RetentionWorker(connection)._effective_days()*86400 if cutoff is None else float(cutoff)
    if not math.isfinite(now) or not math.isfinite(cutoff) or cutoff > now:
        raise ValueError('finite now and cutoff <= now required')

    report = {'scope': 'bounded_page', 'now': now, 'cutoff': cutoff, 'dry_run': dry_run,
              'questions': {'sampled': 0, 'eligible': 0, 'expired': 0},
              'incidents': {'sampled': 0, 'stale_open': 0, 'protected_with_evidence': 0,
                            'requires_owner_review': 0, 'sample_ids': []}}
    # Mesh connections serialize this short transaction. The UPDATE also repeats
    # the full predicate, so sampling alone can never authorize the mutation.
    with nullcontext() if dry_run else connection:
        question_after = _cursor(connection, QUESTION_CURSOR_KEY)
        incident_after = _cursor(connection, INCIDENT_CURSOR_KEY)
        questions = connection.execute(
            'SELECT h.rowid, ('+_ELIGIBLE+') AS eligible FROM human_requests h '
            'WHERE h.rowid>? ORDER BY h.rowid LIMIT ?',
            (now, cutoff, question_after, batch_size)).fetchall()
        report['questions']['sampled'] = len(questions)
        for row in questions:
            if not row[1]:
                continue
            report['questions']['eligible'] += 1
            if not dry_run:
                changed = connection.execute(
                    "UPDATE human_requests AS h SET state='expired' WHERE h.rowid=? AND "+_ELIGIBLE,
                    (row[0], now, cutoff))
                report['questions']['expired'] += changed.rowcount

        incidents = connection.execute(
            'SELECT rowid,id,state,updated_at FROM incidents WHERE rowid>? ORDER BY rowid LIMIT ?',
            (incident_after, batch_size)).fetchall()
        report['incidents']['sampled'] = len(incidents)
        for row in incidents:
            _, incident_id, state, updated_at = tuple(row)
            if state not in ('open', 'acknowledged', 'snoozed') or updated_at is None or not updated_at < cutoff:
                continue
            report['incidents']['stale_open'] += 1
            # Presence is evidence, not a claim that an executor is still alive.
            # Each EXISTS is an indexed first-match lookup, not a history scan.
            protected = state != 'open' or connection.execute(
                'SELECT native_projection_json IS NOT NULL '
                'OR EXISTS (SELECT 1 FROM incident_lifecycle l WHERE l.incident_id=i.id) '
                'OR EXISTS (SELECT 1 FROM human_requests h WHERE h.incident_id=i.id) '
                'OR EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=i.id) '
                'FROM incidents i WHERE i.id=?', (incident_id,)).fetchone()[0]
            category = 'protected_with_evidence' if protected else 'requires_owner_review'
            report['incidents'][category] += 1
            if not protected and len(report['incidents']['sample_ids']) < 20:
                report['incidents']['sample_ids'].append(str(incident_id)[:128])
        if not dry_run:
            _save(connection, QUESTION_CURSOR_KEY, str(questions[-1][0] if questions else 0), now)
            _save(connection, INCIDENT_CURSOR_KEY, str(incidents[-1][0] if incidents else 0), now)
            _save(connection, REPORT_KEY, json.dumps(report, sort_keys=True), now)
    return report
