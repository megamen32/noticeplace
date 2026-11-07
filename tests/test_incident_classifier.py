import unittest
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
