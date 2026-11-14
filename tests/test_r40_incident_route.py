"""Herder policy is the admission boundary for health incident launches."""

import json
import unittest

from notification_center import agent_job_helper as helper
from notification_center.core import ValidationError
from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE, normalize_health_execution


MODEL = "minimax-coding-plan/MiniMax-M3.1-Flash"
PROFILE = dict(HEALTH_EXECUTION_PROFILE)


class IncidentRouteTests(unittest.TestCase):
    def test_local_profile_contains_no_model_authority(self):
        self.assertEqual({"runtime": "opencode", "provider": "", "model": "", "reasoning": "default", "topic": "health"}, PROFILE)
        self.assertEqual(PROFILE, normalize_health_execution(PROFILE))

    def test_malformed_local_profile_stays_fail_closed(self):
        with self.assertRaises(ValidationError):
            normalize_health_execution(dict(PROFILE, runtime="codex", provider="", model=""))


class IncidentPreflightTests(unittest.TestCase):
    def select(self, policy, project=""):
        calls = []

        class Response:
            status = 200
            def __init__(self, body):
                self.body = body
                self.headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps(self.body).encode()

        def runner(request, **kwargs):
            calls.append(request.full_url)
            return Response(policy)

        selected = helper._incident_profile(
            {"url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "harness": "codex", "model": "local/guess"},
            runner, project=project,
        )
        return selected, calls

    def test_incident_reads_one_herder_policy_and_uses_its_project_route(self):
        selected, calls = self.select({
            "version": 1,
            "projectRoutes": {"noticeplace": {"harness": "opencode", "model": MODEL}},
        }, project="noticeplace")
        self.assertEqual("opencode", selected["harness"])
        self.assertEqual(MODEL, selected["model"])
        self.assertEqual(MODEL, selected["orchestrator_model"])
        self.assertEqual(1, len(calls))
        self.assertIn("/api/automation/launch-policy", calls[0])

    def test_missing_or_invalid_herder_route_fails_closed_before_any_catalog_probe(self):
        for policy in ({"version": 1}, {"incidentExecution": {"runtime": "opencode", "provider": "", "model": ""}}):
            with self.subTest(policy=policy):
                with self.assertRaises(ValidationError):
                    self.select(policy)


if __name__ == "__main__":
    unittest.main()
