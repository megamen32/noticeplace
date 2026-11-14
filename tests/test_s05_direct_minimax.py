"""The health model is a Herder-owned intent, not a NoticePlace default."""

import unittest

from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE, health_launch_execution
from notification_center.incident_model_policy import DEFAULT_INCIDENT_MODEL, PROJECT_ROUTE_INTENT, normalize_incident_model


class HerderOwnedModelTest(unittest.TestCase):
    def test_noticeplace_has_no_local_default_and_launches_project_route_intent(self):
        self.assertEqual("", DEFAULT_INCIDENT_MODEL)
        self.assertEqual("", HEALTH_EXECUTION_PROFILE["provider"])
        self.assertEqual("", HEALTH_EXECUTION_PROFILE["model"])
        self.assertEqual(
            {"runtime": "opencode", "provider": "herder", "model": "project-route", "reasoning": "default", "topic": "health"},
            health_launch_execution(PROJECT_ROUTE_INTENT),
        )

    def test_route_validation_is_syntactic_and_never_selects_a_fallback(self):
        for route in ("provider/model", "vendor/model/variant"):
            with self.subTest(route=route):
                self.assertEqual(route, normalize_incident_model(route))
        for route in ("", "malformed", "provider/model\nforbidden", "x" * 257):
            with self.subTest(route=route), self.assertRaises(ValueError):
                normalize_incident_model(route)


if __name__ == "__main__":
    unittest.main()
