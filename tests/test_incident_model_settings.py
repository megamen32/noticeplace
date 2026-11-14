"""Policy settings tests for Herder authority, CAS, and offline operation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notification_center.admin import AdminConfigStore
from notification_center.core import ValidationError
from notification_center.health_workflow import selected_incident_model
from notification_center.herder_policy_client import HerderPolicyUnavailable
from notification_center.incident_model_policy import (
    DEFAULT_INCIDENT_MODEL,
    PROJECT_ROUTE_INTENT,
    incident_model_state,
    normalize_incident_model,
    read_incident_model,
    write_incident_model,
)


ROUTE = "minimax-coding-plan/MiniMax-M3.1-Flash"


class FakeHerderPolicy:
    def __init__(self, route: str = ROUTE, revision: str = '"v1"') -> None:
        self.revision = revision
        self.policy = {"incidentExecution": {"runtime": "opencode", "provider": route.split("/", 1)[0], "model": route}}
        self.writes: list[tuple[str, str]] = []

    def read(self):
        return self.policy, self.revision

    def set_incident_model(self, model: str, expected_revision: str):
        if expected_revision != self.revision:
            raise HerderPolicyUnavailable("Настройки Herder изменились; обновите страницу")
        self.writes.append((model, expected_revision))
        provider = model.split("/", 1)[0]
        self.policy["incidentExecution"] = {"runtime": "opencode", "provider": provider, "model": model}
        self.revision = '"v2"'
        return self.policy


class IncidentModelPolicyTests(unittest.TestCase):
    def test_empty_local_default_and_herder_readback(self):
        fake = FakeHerderPolicy()
        with mock.patch("notification_center.herder_policy_client.HerderPolicyClient", return_value=fake):
            self.assertEqual("", DEFAULT_INCIDENT_MODEL)
            self.assertEqual(ROUTE, read_incident_model(object()))
            state = incident_model_state(object())
        self.assertEqual(ROUTE, state["effective"])
        self.assertEqual('"v1"', state["revision"])
        self.assertEqual([], state["options"])
        self.assertEqual("", state["default"])

    def test_offline_herder_is_explicit_and_selection_fails_closed(self):
        with mock.patch(
            "notification_center.herder_policy_client.HerderPolicyClient",
            side_effect=HerderPolicyUnavailable("Настройки Herder недоступны"),
        ):
            state = incident_model_state(object())
            self.assertFalse(state["available"])
            self.assertEqual("", state["effective"])
            with self.assertRaises(ValueError):
                read_incident_model(object())
            with self.assertRaises(ValidationError):
                selected_incident_model(object())

    def test_write_uses_herder_cas_and_never_local_runtime_storage(self):
        fake = FakeHerderPolicy()
        with mock.patch("notification_center.herder_policy_client.HerderPolicyClient", return_value=fake):
            self.assertEqual(ROUTE, write_incident_model(object(), ROUTE, '"v1"'))
            with self.assertRaises(HerderPolicyUnavailable):
                write_incident_model(object(), ROUTE, '"v1"')
        self.assertEqual([(ROUTE, '"v1"')], fake.writes)

    def test_route_validation_remains_syntactic_and_rejects_malformed_input(self):
        for route in ("provider/model", "vendor/model/variant"):
            self.assertEqual(route, normalize_incident_model(route))
        for route in ("", "malformed", "provider/model\nforbidden", "x" * 257):
            with self.subTest(route=route), self.assertRaises(ValueError):
                normalize_incident_model(route)


class AdminModelSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        primary = root / "notification-center.env"
        primary.write_text(f"NOTIFY_CENTER_DB={root / 'notice.sqlite3'}\n", encoding="utf-8")
        routes = root / "routes.env"
        routes.write_text("TELEGRAM_SEVERITY_ROUTES_JSON={}\n", encoding="utf-8")
        self.store = AdminConfigStore(primary, routes, root / "state", restart=lambda: None,
                                      calls_override_path=root / "calls.conf", daemon_reload=lambda: None,
                                      health_config_path=root / "health.json")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_admin_write_requires_current_revision_and_records_only_confirmed_change(self):
        fake = FakeHerderPolicy()
        with mock.patch("notification_center.herder_policy_client.HerderPolicyClient", return_value=fake):
            with self.assertRaises(ValidationError):
                self.store.set_incident_model(ROUTE, "sso:operator")
            self.assertEqual(ROUTE, self.store.set_incident_model(ROUTE, "sso:operator", '"v1"'))
        self.assertEqual([(ROUTE, '"v1"')], fake.writes)
        record = json.loads(self.store.audit_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("incident_model_changed", record["action"])
        self.assertNotIn("secret", json.dumps(record).lower())
        center = self.store._consumer_notification_center()
        self.assertIsNone(center.get_runtime_setting("health_incident_model"))

    def test_snapshot_reports_herder_unavailable_without_inventing_a_default(self):
        unavailable = mock.Mock()
        unavailable.read.side_effect = HerderPolicyUnavailable("offline")
        with mock.patch("notification_center.herder_policy_client.HerderPolicyClient", return_value=unavailable):
            model = self.store.snapshot()["incident_model"]
        self.assertFalse(model["available"])
        self.assertEqual("", model["value"])
        self.assertEqual("", model["default"])
        self.assertEqual([], model["options"])


if __name__ == "__main__":
    unittest.main()
