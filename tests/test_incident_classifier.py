import io
import json
import shlex
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from notification_center.incident_classifier import classify, normalize_decision


class ClassifierTests(unittest.TestCase):
    def event(self, **extra):
        return {'incident': {'project':'health-monitor','severity':'important','title':'Проверка маршрута не проходит','body':'Три последовательных отказа, доступность сайта снижена.',**extra},
                'health':{'source_id':'probe','host_id':'router','signal_type':'https','correlation_id':'episode'}}

    def test_emergency_floor_never_waits_for_model(self):
        result = classify(self.event(severity='emergency'), lambda _: self.fail('emergency must not wait'))
        self.assertEqual('emergency', result['class'])

    def test_incomplete_never_disappears_into_log(self):
        event=self.event(); event['health']={}
        result=classify(event,lambda _: {'class':'log','confidence':.99,'reason':'Штатное событие'})
        self.assertEqual('repair',result['class'])
        self.assertEqual('insufficient_context',result['status'])

    def test_bad_json_or_timeout_is_repair_without_fallback_model(self):
        calls=[]
        def unavailable(payload):
            calls.append(payload); raise TimeoutError('fixture')
        result=classify(self.event(),unavailable)
        self.assertEqual('repair',result['class'])
        self.assertEqual('unavailable',result['status'])
        self.assertEqual(1,len(calls))
        self.assertNotIn('tools',calls[0])
        self.assertFalse(calls[0]['stream'])

    def test_rejects_extra_authority_and_low_confidence(self):
        for decision in [{'class':'log','confidence':.4,'reason':'Неясно'},
                         {'class':'resolved','confidence':1,'reason':'Готово'},
                         {'class':'log','confidence':.9,'reason':'x','run':'reboot'}]:
            self.assertEqual('repair',normalize_decision(self.event(),decision)['class'])

    def test_valid_decision_remains_advisory_without_human_delivery_authority(self):
        result=classify(self.event(),lambda _: {'class':'important','confidence':.95,'reason':'Требуется ремонт маршрута'})
        self.assertEqual('important',result['class'])
        self.assertNotIn('notify_user',result)
        self.assertNotIn('resolved',result)

    def test_large_integer_confidence_fails_closed(self):
        result = classify(self.event(), lambda _: {
            'class': 'log', 'confidence': 10**400, 'reason': 'fixture'})
        self.assertEqual(('repair', 'invalid_response'), (result['class'], result['status']))

    def test_incomplete_and_stale_source_context_skip_model(self):
        for context in ({}, None, {'service': 'fixture'}, {
            'service': 'fixture', 'impact': 'fixture', 'symptoms': ['fixture'],
            'observed_at': 1, 'freshness_seconds': 60, 'verifier_ref': 'fixture',
            'context_status': 'complete', 'missing_fields': [],
        }):
            event = self.event()
            event['health']['source_context'] = context
            calls = []
            result = classify(event, lambda payload: calls.append(payload) or {
                'class': 'log', 'confidence': .99, 'reason': 'fixture'})
            self.assertEqual('repair', result['class'])
            self.assertEqual([], calls)

    def test_emergency_floor_survives_incomplete_source_context(self):
        event = self.event(severity='emergency')
        event['health']['source_context'] = {}
        result = classify(event, lambda _: self.fail('must not call model'))
        self.assertEqual('emergency', result['class'])

    def test_large_valid_unicode_request_reaches_remote_as_complete_json(self):
        from notification_center.incident_classifier import DIGEST, MODEL
        event = self.event(body='я'*3000, title='я'*500)
        event['health']['source_context'] = {
            'service': 'fixture', 'impact': 'я'*1000, 'symptoms': ['я'*500]*8,
            'observed_at': time.time(), 'freshness_seconds': 60, 'verifier_ref': 'fixture',
        }
        received = []

        def urlopen(request, **kwargs):
            if isinstance(request, str):
                return io.BytesIO(json.dumps({'models': [{'name': MODEL, 'digest': DIGEST}]}).encode())
            received.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'message': {'content': json.dumps({
                'class': 'repair', 'confidence': .9, 'reason': 'fixture'})}}).encode())

        def local_remote_program(args, **kwargs):
            raw = kwargs['input']
            raw = raw.encode('utf-8') if isinstance(raw, str) else raw
            self.assertGreater(len(raw), 16000)
            out = io.StringIO()
            with patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(raw))), patch('sys.stdout', out), \
                    patch('urllib.request.urlopen', urlopen):
                exec(compile(shlex.split(args[-1])[2], '<isolated-transport>', 'exec'), {})
            self.assertEqual(json.loads(raw), received[-1])
            return SimpleNamespace(returncode=0, stdout=out.getvalue())

        with patch('notification_center.incident_classifier.subprocess.run', local_remote_program):
            result = classify(event)
        self.assertEqual('classified', result['status'])
        self.assertEqual(1, len(received))

    def test_oversized_transport_request_is_rejected_before_ssh(self):
        from notification_center.incident_classifier import _queue_transport
        with patch('notification_center.incident_classifier.subprocess.run') as run:
            with self.assertRaises(ValueError):
                _queue_transport({'unbounded': 'x'*100000})
            run.assert_not_called()
