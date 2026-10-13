"""Fast unit: immutable old references and scoped pre-admission reconciliation.

Purpose: preserve published references and refuse ambiguous replay proof.
Expected 1 second; maximum 5 seconds. Runtime reconciliation belongs to Root.
"""
import json
import tempfile
import unittest
from pathlib import Path

from notification_center.core import AuthorizationError, NotificationCenter, ValidationError


class CanonicalReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'notice.sqlite3'
        self.tokens = {'owner': {'project': 'hermes', 'max_severity': 'critical'},
                       'foreign': {'project': 'other', 'max_severity': 'critical'}}
        self.center = NotificationCenter(self.path, self.tokens, default_quiet_hours=[])
        self.addCleanup(self.center._connection.close)
        self.request_id = '1b45fd65-6013-4925-a781-afd5367d7b1f'
        self.old_ref = '6944969d-ac29-5003-fba0-a545f0c2891c'
        self.center.create_human_request('owner', {
            'schema': 'ask_human.request.v1', 'request_id': self.request_id, 'project': 'hermes',
            'recipient': 'me', 'mode': 'question', 'message': 'Вопрос?', 'allowed_actors': ['telegram:42'],
            'native_target': {'harness': 'codex', 'session_id': 'native-session', 'cwd': '/srv/app'}})
        self.center.resolve_human_request_for_surface(self.request_id, 'telegram:42', 'detailed')
        with self.center._lock, self.center._connection:
            self.center._connection.execute('UPDATE human_request_completions SET result_ref=?,state=? WHERE request_id=?',
                                            (self.old_ref, 'unknown', self.request_id))
        self.proof = {'requestId': self.request_id, 'status': 'pending',
                      'target': {'agent': 'codex', 'sessionId': 'native-session', 'cwd': '/srv/app'}}

    def test_reopen_freezes_historical_ref_without_changing_answer_or_outbox(self):
        with self.center._lock, self.center._connection:
            self.center._connection.execute('UPDATE human_requests SET response_result_ref=NULL WHERE request_id=?', (self.request_id,))
        reopened = NotificationCenter(self.path, self.tokens)
        self.addCleanup(reopened._connection.close)
        receipt = reopened.human_request_result_receipt('owner', self.request_id)
        self.assertEqual(self.old_ref, receipt['result_ref'])
        self.assertEqual('detailed', receipt['answer'])
        self.assertEqual('unknown', reopened.human_request_completion(self.request_id)['state'])

    def test_verified_pending_reconciliation_keeps_reference_and_audits_evidence(self):
        before = self.center.get_human_request('owner', self.request_id)
        receipt = self.center.reconcile_unadmitted_human_completion(
            'owner', self.request_id, self.old_ref, self.proof, 'proof:herder-400-pending-no-attempt', 'root')
        self.assertEqual('retryable', receipt['state'])
        self.assertEqual(self.old_ref, receipt['result_ref'])
        self.assertEqual(before, self.center.get_human_request('owner', self.request_id))
        audit = self.center._connection.execute(
            "SELECT payload_json FROM audit_events WHERE type='human_completion.pre_admission_reconciled'").fetchone()
        self.assertEqual('proof:herder-400-pending-no-attempt', json.loads(audit[0])['evidence_ref'])

    def test_unscoped_stale_target_attempt_and_conflicting_reference_cannot_reset_unknown(self):
        for token, ref, record in [
            ('foreign', self.old_ref, self.proof),
            ('owner', self.old_ref, {**self.proof, 'attemptId': 'attempt-1'}),
            ('owner', self.old_ref, {**self.proof, 'status': 'resuming'}),
            ('owner', self.old_ref, {**self.proof, 'target': {**self.proof['target'], 'sessionId': 'other'}}),
            ('owner', '00000000-0000-5000-8000-000000000000', self.proof),
        ]:
            with self.subTest(record=record), self.assertRaises((ValidationError, AuthorizationError)):
                self.center.reconcile_unadmitted_human_completion(token, self.request_id, ref, record, 'proof:bad', 'root')
            self.assertEqual('unknown', self.center.human_request_completion(self.request_id)['state'])
