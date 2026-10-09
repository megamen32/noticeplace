import unittest
from notification_center.core import ValidationError
from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE, normalize_health_execution
from notification_center import agent_job_helper as helper

PROFILE = dict(runtime='opencode', provider='minimax-coding-plan', model='MiniMax-M3.1-Flash-Preview', reasoning='default', topic='health')
MODEL = 'minimax-coding-plan/MiniMax-M3.1-Flash-Preview'

class IncidentRouteTests(unittest.TestCase):
    def test_new_plan_pins_subscription(self):
        self.assertEqual(PROFILE, HEALTH_EXECUTION_PROFILE)
        self.assertEqual(PROFILE, normalize_health_execution(PROFILE))
        self.assertEqual(MODEL, helper._health_model_route(PROFILE))

    def test_old_routes_are_not_accepted_for_new_incidents(self):
        for runtime, provider, model in [('codex','openai-codex','gpt-5.6-sol'), ('opencode','auto','minimax'), ('opencode','omniroute','minimax')]:
            with self.subTest(provider=provider), self.assertRaises(ValidationError):
                normalize_health_execution(dict(PROFILE, runtime=runtime, provider=provider, model=model, reasoning="high" if runtime=="codex" else "default"))

class IncidentPreflightTests(unittest.TestCase):
    def select(self, policy, catalog):
        calls = []
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args):
                import json
                return json.dumps(self.body).encode()
        def runner(request, **kwargs):
            calls.append(request.full_url)
            return Response(catalog if '/api/models' in request.full_url else policy)
        selected = helper._incident_profile({'url':'http://127.0.0.1:18787/api/sessions/new-or-resume','harness':'codex','model':'omniroute/subagent'}, runner)
        return selected, calls

    def test_incident_uses_saved_subscription_ignoring_global_codex(self):
        selected, calls = self.select({'incidentExecution':PROFILE, 'preferredHarness':'codex','models':{'codex':'gpt-6.1-sol'}}, {'models':[MODEL], 'stale':False})
        self.assertEqual('opencode', selected['harness'])
        self.assertEqual(MODEL, selected['model'])
        self.assertEqual(MODEL, selected['orchestrator_model'])
        self.assertEqual(2, len(calls))

    def test_missing_model_or_stale_catalog_refuses_without_fallback(self):
        for catalog in [{'models':['omniroute/auto/minimax']},{'models':[MODEL], 'stale':True}]:
            with self.subTest(catalog=catalog), self.assertRaisesRegex(RuntimeError, 'subscription model unavailable.*no provider fallback'):
                self.select({'incidentExecution':PROFILE}, catalog)

    def test_old_or_missing_incident_choice_refuses_before_model_or_creation(self):
        for policy in [{}, {'incidentExecution':dict(PROFILE, provider='omniroute')}, {'incidentExecution':dict(PROFILE, runtime='codex')}]:
            with self.subTest(policy=policy), self.assertRaisesRegex(RuntimeError, 'no runtime/provider fallback'):
                self.select(policy, {'models':[MODEL]})
