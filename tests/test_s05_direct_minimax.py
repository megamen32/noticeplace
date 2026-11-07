import unittest
from notification_center.incident_model_policy import default_incident_model, normalize_incident_model, read_incident_model
from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE, health_launch_execution

ROUTE = 'minimax-coding-plan/MiniMax-M3.1-Flash'
OLD = 'omniroute/glm-5.3-flash-minimax-m3.1'

class Settings:
    def __init__(self, value): self.value = value
    def get_runtime_setting(self, key): return self.value

class DirectMiniMaxTest(unittest.TestCase):
    def test_direct_default_and_profile(self):
        self.assertEqual(default_incident_model(), ROUTE)
        self.assertEqual(normalize_incident_model(ROUTE), ROUTE)
        self.assertEqual(health_launch_execution(ROUTE), HEALTH_EXECUTION_PROFILE)
    def test_only_future_setting_migrates_and_snapshots_fail_closed(self):
        stored = Settings(OLD)
        self.assertEqual(read_incident_model(stored), ROUTE)
        self.assertEqual(stored.value, OLD)
        for route in (OLD, 'auto/minimax', 'minimax-coding-plan/other'):
            with self.subTest(route=route), self.assertRaises(ValueError): normalize_incident_model(route)
