import sys
import unittest
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

class PublicSDKHealthTests(unittest.TestCase):
    def test_verifier_is_installed_public_sdk(self):
        from notify_center_client import SourceVerifier, VerificationPending
        self.assertTrue(callable(SourceVerifier)); self.assertTrue(issubclass(VerificationPending, Exception))
    def test_typed_emit_is_explicit_and_complete(self):
        from notify_center_client import NotificationCenterClient
        client = NotificationCenterClient('http://localhost/v1/events','test')
        captured=[]
        client._request=lambda method,url,**kw: captured.append(kw) or {'incident_id':'inc_test'}
        client.emit(project='health',severity='important',title='Edge',dedup_key='edge',
                    idempotency_key='edge:1', event_type='health.degraded', source_id='edge',
                    host_id='router',signal_type='https',problem_type='https',correlation_id='episode',observed_at=100.0)
        p=captured[0]['payload']
        self.assertEqual(p['event_type'],'health.degraded')
        self.assertEqual([p[k] for k in ('source_id','host_id','signal_type','problem_type','correlation_id')],['edge','router','https','https','episode'])
        self.assertEqual(p['observed_at'],100.0)
        with self.assertRaises(ValueError): client.emit(project='health',severity='important',title='Edge',dedup_key='edge',event_type='health.degraded',source_id='edge')

    def test_bounded_checks_remain_due_until_processed(self):
        from notify_center_client import SourceVerifier
        pending=[{'incident_id':'inc_'+str(i),'request_id':'check_'+str(i),'generation':1,
                  'group':{'project':'health','source_id':'edge','host_id':'router','signal_type':'https'}} for i in range(2)]
        posts=[]
        def transport(method,path,body=None,key=None):
            if method=='GET': return {'requests':list(pending)}
            posts.append(body['request_id'])
            pending[:]=[r for r in pending if r['request_id']!=body['request_id']]
            return {'observed_state':'healthy'}
        with tempfile.TemporaryDirectory() as d:
            verifier=SourceVerifier('http://test','test','health','edge','router',d,transport=transport)
            probe=lambda _: {'observed_state':'healthy','evidence_refs':['fresh']}
            verifier.tick(probe,max_checks=1)
            self.assertEqual(['check_0'],posts)
            self.assertEqual(1,len(pending))
            verifier.tick(probe,max_checks=1)
            self.assertEqual(['check_0','check_1'],posts)
            self.assertEqual([],pending)

    def test_group_filter_does_not_touch_other_episode(self):
        from notify_center_client import SourceVerifier
        requests=[{'incident_id':'inc_1','request_id':'q1','generation':1,
                   'group':{'project':'health','source_id':'edge','host_id':'router','correlation_id':'other'}},
                  {'incident_id':'inc_2','request_id':'q2','generation':1,
                   'group':{'project':'health','source_id':'edge','host_id':'router','correlation_id':'mine'}}]
        posts=[]
        def transport(method,path,body=None,key=None):
            if method=='GET': return {'requests':requests}
            posts.append(body['request_id']);return {'observed_state':'unknown'}
        with tempfile.TemporaryDirectory() as d:
            verifier=SourceVerifier('http://test','test','health','edge','router',d,transport=transport)
            verifier.tick(lambda _: {'observed_state':'unknown','evidence_refs':['fresh']},
                          accept_group=lambda g:g['correlation_id']=='mine')
        self.assertEqual(['q2'],posts)

if __name__ == '__main__': unittest.main()
