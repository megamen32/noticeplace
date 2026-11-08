"""Source verifier regressions; local SQLite and in-process transports only."""
import tempfile
import time
import unittest
import io
import json
import urllib.error
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from notification_center import incident_lifecycle as lifecycle
from notification_center.consumer_lifecycle import SourceVerifier
from notification_center.core import AuthorizationError, NotificationCenter, ValidationError


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        self.posts = []
        self.pages = []

    def request(self, number, host='host'):
        return dict(incident_id=f'i{number}', request_id=f'r{number}', generation=1,
                    group=dict(project='test', source_id='source', host_id=host,
                               correlation_id=str(number)))

    def transport(self, method, path, body=None, key=None):
        if method == 'POST':
            self.posts.append(body['request_id'])
            return dict(body)
        query = parse_qs(urlsplit(path).query)
        start = int(query.get('cursor', ['0'])[0])
        limit = int(query['limit'][0])
        self.pages.append((start, limit))
        end = start + limit
        return dict(requests=self.requests[start:end],
                    next_cursor=str(end) if end < len(self.requests) else None,
                    cursor_expires_at=time.time() + 300)

    def client(self):
        return SourceVerifier('http://fixture', 'token', 'test', 'source', 'host',
                              self.temp.name, transport=self.transport)

    @staticmethod
    def probe(group):
        return dict(observed_state='healthy', evidence_refs=['fresh:fixture'])

    def test_other_host_is_skipped_before_owned_probe(self):
        self.requests = [self.request(0, 'foreign'), self.request(1)]
        probed = []
        def probe(group):
            probed.append(group['host_id'])
            return self.probe(group)
        self.client().tick(probe, accept_group=lambda g: g['host_id'] == 'host')
        self.assertEqual(['host'], probed)
        self.assertEqual(['r1'], self.posts)

    def test_max_checks_is_not_page_size(self):
        self.requests = [self.request(n) for n in range(101)]
        self.client().tick(self.probe, max_checks=1,
                           accept_group=lambda g: g['correlation_id'] == '100')
        self.assertEqual(['r100'], self.posts)
        self.assertLessEqual(len(self.pages), 6)

    def test_page_budget_resumes_across_restart(self):
        self.requests = [self.request(n) for n in range(2001)]
        accepted = lambda g: g['correlation_id'] == '2000'
        self.client().tick(self.probe, max_checks=1, accept_group=accepted)
        self.assertLessEqual(len(self.pages), 100)
        self.assertEqual([], self.posts)
        self.client().tick(self.probe, max_checks=1, accept_group=accepted)
        self.assertEqual(['r2000'], self.posts)

    def test_partial_page_restart_does_not_repeat_a_completed_check(self):
        self.requests = [self.request(n) for n in range(3)]
        for _ in range(3):
            self.client().tick(self.probe, max_checks=1)
        self.assertEqual(['r0', 'r1', 'r2'], self.posts)

    def test_expired_sweep_restarts_reads_without_replaying_results(self):
        self.requests = [self.request(n) for n in range(41)]
        accept = lambda g: g['correlation_id'] == '40'
        with patch('notification_center.consumer_lifecycle.SWEEP_MAX_PAGES', 1):
            self.client().tick(self.probe, accept_group=accept)
        checkpoint = Path(self.temp.name) / 'sweep.json'
        saved = json.loads(checkpoint.read_text())
        saved['expires_at'] = time.time() - 1
        checkpoint.write_text(json.dumps(saved))
        self.pages.clear()
        self.client().tick(self.probe, accept_group=accept)
        self.assertEqual(0, self.pages[0][0])
        self.assertEqual(['r40'], self.posts)

    def test_server_rejected_cursor_resets_only_the_sweep(self):
        self.requests = [self.request(n) for n in range(41)]
        accept = lambda g: g['correlation_id'] == '40'
        with patch('notification_center.consumer_lifecycle.SWEEP_MAX_PAGES', 1):
            self.client().tick(self.probe, accept_group=accept)
        real = self.transport
        rejected = []
        def transport(method, path, body=None, key=None):
            if method == 'GET' and 'cursor=' in path and not rejected:
                rejected.append(path)
                raise urllib.error.HTTPError(path, 400, 'invalid cursor', {},
                    io.BytesIO(b'{"error":"invalid or expired verification cursor"}'))
            return real(method, path, body, key)
        client = self.client()
        client.transport = transport
        client.tick(self.probe, accept_group=accept)
        self.assertEqual(1, len(rejected))
        self.assertEqual(['r40'], self.posts)

    def test_sweep_after_long_timer_interval_uses_supported_read_resume(self):
        self.requests = [self.request(n) for n in range(41)]
        real = self.transport
        resumes = []
        def transport(method, path, body=None, key=None):
            if 'resume%3A' in path:
                resumes.append(path)
                path = path.replace('resume%3A', '')
            response = real(method, path, body, key)
            response['cursor_resume_supported'] = True
            return response
        client = self.client()
        client.transport = transport
        accept = lambda g: g['correlation_id'] == '40'
        with patch('notification_center.consumer_lifecycle.SWEEP_MAX_PAGES', 1):
            client.tick(self.probe, accept_group=accept)
        checkpoint = Path(self.temp.name) / 'sweep.json'
        saved = json.loads(checkpoint.read_text())
        saved['expires_at'] = time.time() - 1
        checkpoint.write_text(json.dumps(saved))
        client.tick(self.probe, accept_group=accept)
        self.assertEqual(1, len(resumes))
        self.assertEqual(['r40'], self.posts)

    def test_foreign_project_and_source_fail_before_probe(self):
        for field in ('project', 'source_id'):
            self.requests = [self.request(0)]
            self.requests[0]['group'][field] = 'foreign'
            with self.assertRaises(ValueError):
                self.client().tick(lambda _: self.fail('foreign group was probed'))
        self.assertEqual([], self.posts)


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.center = NotificationCenter(Path(self.temp.name) / 'db', {
            'executor': dict(project='test', max_severity='emergency',
                             principal_id='executor', incident_role='executor')})
        self.addCleanup(self.center._connection.close)

    def consumer(self):
        return self.center.create_consumer(project='test', name='fixture', max_severity='notice',
            policy=[dict(id='root', platform='telegram', action='message',
                         target={'chat_id': -100}, retry_interval_seconds=60, max_repeats=1)])

    def incident(self, consumer, name, source='source'):
        self.center._tokens[name] = dict(project='test', max_severity='notice',
                                         principal_id='source:' + name, consumer_id=consumer['id'])
        event = dict(schema='notify.event.v1', kind='incident', severity='notice',
                     project='test', recipient='health', title='Failure', dedup_key=name,
                     event_type='health.degraded', source_id=source, host_id='host',
                     signal_type='disk', correlation_id=name)
        incident = self.center.create_event(name, name, event)['incident_id']
        request = self.center.record_work_finished('executor', incident, 'finish:' + name,
            dict(attempt_id=name, session_id='session', harness='codex'))
        return incident, request

    def test_named_source_grant_has_same_pull_and_submit_scope(self):
        owned = self.consumer()
        incident, request = self.incident(owned, 'owned')
        foreign, foreign_request = self.incident(self.consumer(), 'foreign')
        wrong_source, wrong_request = self.incident(owned, 'other-source', 'other')
        grant = dict(project='test', max_severity='notice', principal_id='verifier',
                     incident_role='verifier', verification_sources=['source'],
                     verification_consumer_ids=[owned['id']])
        self.center._tokens['verifier'] = grant
        self.assertIsNotNone(lifecycle.verifier(self.center, 'verifier', incident))
        page = self.center.source_checks_page('verifier', 'test', 'source')
        self.assertEqual([incident], [r['incident_id'] for r in page['requests']])
        for target, req in ((foreign, foreign_request), (wrong_source, wrong_request)):
            with self.assertRaises(AuthorizationError):
                self.center.record_source_check('verifier', target, 'deny:' + target,
                    dict(request_id=req['request_id'], generation=req['generation'],
                         observed_state='healthy', observed_at=time.time(), evidence_refs=['fresh']))
        self.center._tokens['forged-executor'] = dict(grant, incident_role='executor')
        self.center._tokens['same-executor'] = dict(grant, principal_id='executor')
        for token in ('forged-executor', 'same-executor'):
            with self.assertRaises(AuthorizationError):
                lifecycle.verifier(self.center, token, incident)
            try:
                page = self.center.source_checks_page(token, 'test', 'source')
            except AuthorizationError:
                continue
            self.assertEqual([], page['requests'])
        self.center.record_source_check('verifier', incident, 'accept',
            dict(request_id=request['request_id'], generation=request['generation'],
                 observed_state='healthy', observed_at=time.time(), evidence_refs=['fresh']))
        self.assertEqual('resolved', self.center.get_incident(incident)['state'])


class FreshnessTests(GrantTests):
    # Reuse the SQLite fixture, not the parent test case.
    test_named_source_grant_has_same_pull_and_submit_scope = None

    def prepare(self):
        consumer = self.consumer()
        self.center._tokens['verifier'] = dict(project='test', max_severity='notice',
            principal_id='verifier', incident_role='verifier', verification_sources=['source'],
            verification_consumer_ids=[consumer['id']])
        return self.incident(consumer, 'freshness')

    def submit(self, incident, request, observed_at, key='measurement'):
        return self.center.record_source_check('verifier', incident, key,
            dict(request_id=request['request_id'], generation=request['generation'],
                 observed_state='healthy', observed_at=observed_at, evidence_refs=['fresh']))

    def test_delayed_healthy_rearms_unknown_durably_instead_of_closing(self):
        with patch.object(lifecycle.time, 'time', return_value=1000):
            incident, request = self.prepare()
        with patch.object(lifecycle.time, 'time', return_value=1000 + 7 * 86400):
            with self.assertRaisesRegex(ValidationError, 'stale verification request or generation'):
                self.submit(incident, request, 1001)
            current = self.center.get_incident(incident)
            self.assertEqual('open', current['state'])
            self.assertEqual('unknown', current['lifecycle']['verification_state'])
            self.assertGreater(current['lifecycle']['generation'], request['generation'])
            self.assertNotEqual(current['lifecycle']['request_id'], request['request_id'])
            # The rejection must not roll back the new request on restart.
            reopened = NotificationCenter(Path(self.temp.name) / 'db', self.center._tokens)
            self.addCleanup(reopened._connection.close)
            self.assertEqual(current['lifecycle']['request_id'],
                             reopened.get_incident(incident)['lifecycle']['request_id'])
            self.submit(incident, current['lifecycle'], lifecycle.time.time(), key='new-measurement')
            self.assertEqual('resolved', self.center.get_incident(incident)['state'])

    def test_provided_null_old_future_nonfinite_measurements_never_close(self):
        for measured in (None, 900, 1100, True, float('nan'), float('inf')):
            with self.subTest(observed_at=measured), patch.object(lifecycle.time, 'time', return_value=1000):
                incident, request = self.incident(self.consumer(), 'bad-' + str(measured))
                self.center._tokens['verifier'] = dict(project='test', max_severity='notice',
                    principal_id='source:bad-' + str(measured), incident_role='verifier',
                    verification_sources=['source'])
                with self.assertRaises(ValidationError):
                    self.submit(incident, request, measured, key='bad-' + str(measured))
                self.assertEqual('open', self.center.get_incident(incident)['state'])

    def test_huge_json_timestamp_is_domain_rejection_not_overflow(self):
        incident, request = self.prepare()
        # HTTP maps ValidationError to 400; OverflowError would escape as 500.
        with self.assertRaises(ValidationError):
            self.submit(incident, request, 10 ** 400)
        self.assertEqual('open', self.center.get_incident(incident)['state'])

    def test_pull_rotates_expired_request_once_without_fabricated_health(self):
        with patch.object(lifecycle.time, 'time', return_value=1000):
            incident, request = self.prepare()
        with patch.object(lifecycle.time, 'time', return_value=2000):
            first = self.center.source_checks_page('verifier', 'test', 'source')['requests'][0]
            second = self.center.source_checks_page('verifier', 'test', 'source')['requests'][0]
            self.assertEqual('unknown', first['verification_state'])
            self.assertGreater(first['generation'], request['generation'])
            self.assertEqual(first['request_id'], second['request_id'])
            self.assertGreater(first['verification_deadline'], 2000)
            self.assertEqual(0, self.center._connection.execute(
                'SELECT COUNT(*) FROM incident_check_receipts').fetchone()[0])

    def test_accepted_receipt_replay_survives_deadline_and_restart(self):
        with patch.object(lifecycle.time, 'time', return_value=1000):
            incident, request = self.prepare()
            receipt = self.submit(incident, request, 1000)
        with patch.object(lifecycle.time, 'time', return_value=100000):
            self.assertEqual(receipt, self.submit(incident, request, 1000))
            self.assertEqual('resolved', self.center.get_incident(incident)['state'])

    def test_expired_read_cursor_resume_rechecks_scope_and_reaches_next_page(self):
        with patch.object(lifecycle.time, 'time', return_value=1000):
            consumer = self.consumer()
            self.center._tokens['verifier'] = dict(project='test', max_severity='notice',
                principal_id='verifier', incident_role='verifier', verification_sources=['source'],
                verification_consumer_ids=[consumer['id']])
            for n in range(3):
                self.incident(consumer, 'resume-' + str(n))
            first = self.center.source_checks_page('verifier', 'test', 'source', limit=1)
        with patch.object(lifecycle.time, 'time', return_value=2000):
            with self.assertRaises(ValidationError):
                self.center.source_checks_page('verifier', 'test', 'source', limit=1,
                                               cursor=first['next_cursor'])
            resumed = self.center.source_checks_page('verifier', 'test', 'source', limit=1,
                                                     cursor='resume:' + first['next_cursor'])
            self.assertNotEqual(first['requests'][0]['incident_id'], resumed['requests'][0]['incident_id'])
            self.assertGreater(resumed['cursor_expires_at'], 2000)
            with self.assertRaises((AuthorizationError, ValidationError)):
                self.center.source_checks_page('executor', 'test', 'source', limit=1,
                                               cursor='resume:' + first['next_cursor'])
            self.center._tokens['verifier']['verification_consumer_ids'] = []
            denied = self.center.source_checks_page('verifier', 'test', 'source', limit=1,
                                                    cursor='resume:' + first['next_cursor'])
            self.assertEqual([], denied['requests'])

    def test_legacy_sdk_without_observed_at_works_only_within_request_ttl(self):
        with patch.object(lifecycle.time, 'time', return_value=1000):
            incident, request = self.prepare()
        body = dict(request_id=request['request_id'], generation=request['generation'],
                    observed_state='healthy', evidence_refs=['legacy fresh-probe assertion'])
        with patch.object(lifecycle.time, 'time', return_value=1400):
            with self.assertRaisesRegex(ValidationError, 'stale verification request or generation'):
                self.center.record_source_check('verifier', incident, 'legacy-expired', body)
            current = self.center.get_incident(incident)
            self.assertEqual('open', current['state'])
            self.assertEqual('unknown', current['lifecycle']['verification_state'])
            fresh = dict(body, request_id=current['lifecycle']['request_id'],
                         generation=current['lifecycle']['generation'])
            accepted = self.center.record_source_check('verifier', incident, 'legacy-new', fresh)
            self.assertEqual('resolved', self.center.get_incident(incident)['state'])
        with patch.object(lifecycle.time, 'time', return_value=9999):
            self.assertEqual(accepted, self.center.record_source_check('verifier', incident, 'legacy-new', fresh))

    def test_legacy_accepted_payload_without_timestamp_stays_reconcilable(self):
        incident, request = self.prepare()
        body = dict(request_id=request['request_id'], generation=request['generation'],
                    observed_state='unknown', evidence_refs=['legacy observation unavailable'])
        receipt = self.center.record_source_check('verifier', incident, 'legacy', body)
        reopened = NotificationCenter(Path(self.temp.name) / 'db', self.center._tokens)
        self.addCleanup(reopened._connection.close)
        self.assertEqual(receipt, reopened.record_source_check('verifier', incident, 'legacy', body))
        self.assertNotIn('observed_at', receipt)

    def test_sdk_preserves_measurement_time_then_recovers_rejected_stale_result(self):
        incident, _ = self.prepare()
        submitted = []
        calls = []
        def transport(method, path, body=None, key=None):
            calls.append((method, path))
            if method == 'POST':
                submitted.append(dict(body))
                try:
                    return self.center.record_source_check('verifier', incident, key, body)
                except ValidationError as error:
                    raise urllib.error.HTTPError(path, 400, 'rejected', {},
                        io.BytesIO(json.dumps({'error': str(error)}).encode())) from error
            if path.startswith('/v1/incidents/'):
                return self.center.get_incident(incident)
            return self.center.source_checks_page('verifier', 'test', 'source')
        client = SourceVerifier('http://fixture', 'verifier', 'test', 'source', 'host',
                                Path(self.temp.name) / 'sdk', transport=transport)
        stale = time.time() - 10000
        with self.assertRaises(urllib.error.HTTPError):
            client.tick(lambda _: dict(observed_state='healthy', observed_at=stale, evidence_refs=['old']))
        self.assertEqual(stale, submitted[0]['observed_at'])
        self.assertEqual('open', self.center.get_incident(incident)['state'])
        intent = json.loads((Path(self.temp.name) / 'sdk' / 'verification.json').read_text())
        self.assertEqual('rejected_stale', intent['status'])
        calls.clear()
        client = SourceVerifier('http://fixture', 'verifier', 'test', 'source', 'host',
                                Path(self.temp.name) / 'sdk', transport=transport)
        client.tick(lambda _: dict(observed_state='healthy', evidence_refs=['fresh probe']))
        self.assertEqual(('GET', '/v1/incidents/' + incident), calls[0])
        self.assertEqual(2, len(submitted))
        self.assertNotEqual(submitted[0]['request_id'], submitted[1]['request_id'])
        self.assertEqual('resolved', self.center.get_incident(incident)['state'])

    def test_sdk_expired_post_400_survives_restart_and_rechecks_before_new_probe(self):
        now = [1000]
        with patch.object(lifecycle.time, 'time', side_effect=lambda: now[0]):
            incident, original = self.prepare()
            calls, posts = [], []
            def transport(method, path, body=None, key=None):
                calls.append((method, path))
                if method == 'POST':
                    posts.append((key, dict(body)))
                    if len(posts) == 1:
                        now[0] = 1400  # A fresh measurement was delayed in transport.
                    try:
                        return self.center.record_source_check('verifier', incident, key, body)
                    except ValidationError as error:
                        raise urllib.error.HTTPError(path, 400, 'expired', {},
                            io.BytesIO(json.dumps({'error': str(error)}).encode())) from error
                if path.startswith('/v1/incidents/'):
                    return self.center.get_incident(incident)
                return self.center.source_checks_page('verifier', 'test', 'source')
            directory = Path(self.temp.name) / 'expired-sdk'
            client = SourceVerifier('http://fixture', 'verifier', 'test', 'source', 'host',
                                    directory, transport=transport)
            def probe(group):
                calls.append(('probe', group['host_id']))
                return dict(observed_state='healthy', evidence_refs=['fresh source measurement'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                client.tick(probe)
            self.assertEqual(400, error.exception.code)
            intent = json.loads((directory / 'verification.json').read_text())
            self.assertEqual('rejected_stale', intent['status'])
            current = self.center.get_incident(incident)
            self.assertEqual('open', current['state'])
            self.assertEqual('unknown', current['lifecycle']['verification_state'])
            self.assertNotIn('last_check_receipt', current['lifecycle'])
            calls.clear()
            restarted = SourceVerifier('http://fixture', 'verifier', 'test', 'source', 'host',
                                       directory, transport=transport)
            restarted.tick(probe)
            self.assertEqual(('GET', '/v1/incidents/' + incident), calls[0])
            self.assertEqual('GET', calls[1][0])
            self.assertEqual('probe', calls[2][0])
            self.assertEqual('POST', calls[3][0])
            self.assertEqual(2, len(posts))
            self.assertNotEqual(posts[0][0], posts[1][0])
            self.assertEqual(original['request_id'], posts[0][1]['request_id'])
            self.assertNotEqual(posts[0][1]['request_id'], posts[1][1]['request_id'])
            self.assertEqual('resolved', self.center.get_incident(incident)['state'])
