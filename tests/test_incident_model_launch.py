"""Durable incident-model snapshot and the selected-route health launch.

The operator types the route that analyses a health incident by hand. Notice
Place owns that preference and snapshots it into every new health delivery;
Agent Herder keeps admission, the workspace and the allow/deny decision. A
delivery without a snapshot keeps the legacy pinned route and is never
rerouted, and a stored route that is not usable fails the launch closed
instead of starting another model.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notification_center import agent_job_helper
from notification_center.agent_job_helper import run_profile
from notification_center.core import NotificationCenter, ValidationError
from notification_center.gptadmin_agent import _agent_job_event
from notification_center.health_workflow import (HEALTH_LAUNCH_DEFAULT, HEALTH_LAUNCH_MODELS, HEALTH_LAUNCH_PURPOSE,
                                                HealthWorkflow, health_launch_execution,
                                                normalize_incident_model, selected_incident_model)

DEFAULT_ROUTE = "omniroute/minimax-combo-m3.1"
HAND_ROUTE = "omniroute/openai/gpt-5.1"
SECOND_HAND_ROUTE = "omniroute/qwen/qwen3-max"
PAID_ROUTES = (
    "minimax-coding-plan/MiniMax-M3.1-Flash",
    "omniroute/minimax-coding-plan/MiniMax-M3.1-Flash",
    "minimax/MiniMax-M3.1-Flash",
    "omniroute/minimax/MiniMax-M3.1-Flash",
)
LEGACY_MODEL = "minimax-coding-plan/MiniMax-M3.1-Flash"


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return json.dumps(self._payload).encode()


def health_event(center: NotificationCenter, token: str, key: str, **overrides: object) -> dict[str, object]:
    """Create one health incident that admits the automatic diagnosis job."""
    event = {
        "schema": "notify.event.v1",
        "kind": "incident",
        "project": "fleet-health",
        "recipient": "operator",
        "severity": "critical",
        "title": "Диск переполнен",
        "body": "Диск заполнен на 98 процентов.",
        "dedup_key": "disk:server-100",
        "event_type": "health.degraded",
        "source_id": "disk-monitor",
        "host_id": "server-100",
        "signal_type": "disk",
        "correlation_id": "corr-disk-1",
    }
    event.update(overrides)
    return center.create_event(token, key, event)


class IncidentModelSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "notice.sqlite3"
        self.center = NotificationCenter(self.database, {"producer-token": {"project": "fleet-health", "max_severity": "critical", "agent_jobs": ["health-diagnosis"]}}, default_quiet_hours=[])

    def tearDown(self) -> None:
        self.center._connection.close()
        self.directory.cleanup()

    def _agent_delivery(self, incident_id: str) -> dict[str, object]:
        rows = self.center._connection.execute(
            "SELECT * FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis'",
            (incident_id,),
        ).fetchall()
        self.assertEqual(1, len(rows))
        return dict(rows[0])

    def test_a_hand_typed_route_is_snapshotted_into_the_new_delivery(self) -> None:
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        result = health_event(self.center, "producer-token", "evt-1")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": HAND_ROUTE}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(HAND_ROUTE, payload["health_context"]["incident_model"])

    def test_the_delivery_re_filter_accepts_the_same_routes_as_the_write_path(self) -> None:
        """A hand-picked route is not dropped by the delivery read path."""
        for route in (DEFAULT_ROUTE, HAND_ROUTE, SECOND_HAND_ROUTE):
            with self.subTest(route=route):
                self.assertEqual(route, self.center._delivery_incident_model({"incident_model": route}))

    def test_the_delivery_re_filter_fails_closed_on_an_unusable_route(self) -> None:
        for route in ("codex/gpt-6.1-sol",) + PAID_ROUTES:
            with self.subTest(route=route):
                with self.assertRaises(ValidationError):
                    self.center._delivery_incident_model({"incident_model": route})

    def test_delivery_without_a_snapshot_keeps_the_legacy_route(self) -> None:
        result = health_event(self.center, "producer-token", "evt-legacy")
        delivery = self._agent_delivery(str(result["incident_id"]))
        # Model a delivery persisted by an older version before route snapshots
        # existed. New deliveries always snapshot the current UI default.
        self.center._connection.execute("UPDATE deliveries SET target_json = '{}' WHERE id = ?", (delivery["id"],))
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertNotIn("incident_model", payload.get("health_context") or {})

    def test_a_route_saved_after_scheduling_does_not_reroute_the_old_delivery(self) -> None:
        result = health_event(self.center, "producer-token", "evt-old")
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": HEALTH_LAUNCH_DEFAULT}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(HEALTH_LAUNCH_DEFAULT, payload["health_context"]["incident_model"])

    def test_an_unusable_stored_route_is_never_snapshotted_and_blocks_the_launch(self) -> None:
        """An invalid stored value is not replaced by another model."""
        self.center.set_runtime_setting("health_incident_model", "codex/gpt-6.1-sol")
        with self.assertRaises(ValidationError):
            selected_incident_model(self.center)
        result = health_event(self.center, "producer-token", "evt-invalid")
        delivery = self._agent_delivery(str(result["incident_id"]))
        target = json.loads(delivery["target_json"])
        self.assertNotIn("incident_model", target)
        self.assertIn("incident_model_error", target)
        with self.assertRaises(ValidationError):
            self.center.delivery_payload(delivery)

    def test_a_paid_stored_route_blocks_the_launch_instead_of_another_model(self) -> None:
        self.center.set_runtime_setting("health_incident_model", "omniroute/minimax/MiniMax-M3.1-Flash")
        with self.assertRaises(ValidationError):
            selected_incident_model(self.center)
        result = health_event(self.center, "producer-token", "evt-paid")
        delivery = self._agent_delivery(str(result["incident_id"]))
        with self.assertRaises(ValidationError):
            self.center.delivery_payload(delivery)

    def test_an_unset_setting_resolves_to_the_canonical_default(self) -> None:
        self.assertEqual(DEFAULT_ROUTE, HEALTH_LAUNCH_DEFAULT)
        self.assertEqual(DEFAULT_ROUTE, selected_incident_model(_SettingCenter(None)))
        self.assertEqual(DEFAULT_ROUTE, selected_incident_model(_SettingCenter("")))
        self.assertEqual(DEFAULT_ROUTE, selected_incident_model(_SettingCenter("   ")))

        self.center.set_runtime_setting("health_incident_model", "")
        result = health_event(self.center, "producer-token", "evt-unset")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": HEALTH_LAUNCH_DEFAULT}, json.loads(delivery["target_json"]))

    def test_every_hand_typed_route_is_accepted_by_the_shared_normalizer(self) -> None:
        self.assertEqual((DEFAULT_ROUTE,), HEALTH_LAUNCH_MODELS)
        for route in (DEFAULT_ROUTE, HAND_ROUTE, SECOND_HAND_ROUTE):
            with self.subTest(route=route):
                self.assertEqual(route, normalize_incident_model(route))
                self.assertEqual(route, selected_incident_model(_SettingCenter(route)))
        for invalid in ("", "codex/gpt-6.1-sol", "omniroute/a/b/c") + PAID_ROUTES:
            with self.subTest(route=invalid), self.assertRaises(Exception):
                normalize_incident_model(invalid)

    def test_the_route_round_trips_through_its_execution_profile(self) -> None:
        for route in (DEFAULT_ROUTE, HAND_ROUTE, SECOND_HAND_ROUTE):
            with self.subTest(route=route):
                execution = health_launch_execution(route)
                self.assertEqual("opencode", execution["runtime"])
                self.assertEqual(route, agent_job_helper._health_model_route(execution))

    def test_the_snapshot_rides_only_in_the_signed_health_context(self) -> None:
        payload = {"incident": {"id": "inc-1", "project": "fleet-health"}, "health_context": {"incident_model": HAND_ROUTE}}
        event = _agent_job_event("health-diagnosis", payload)
        self.assertEqual(HAND_ROUTE, event["health"]["incident_model"])
        unsigned = {"incident": {"id": "inc-1", "project": "fleet-health"}, "health_context": {}}
        self.assertNotIn("incident_model", _agent_job_event("health-diagnosis", unsigned)["health"])

    def test_the_real_delivery_path_carries_the_snapshot_into_the_signed_event(self) -> None:
        """The whole chain: create incident -> delivery row -> payload -> event."""
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        result = health_event(self.center, "producer-token", "evt-chain")
        delivery = self._agent_delivery(str(result["incident_id"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(HAND_ROUTE, payload["health_context"]["incident_model"])
        event = _agent_job_event("health-diagnosis", payload)
        self.assertEqual(HAND_ROUTE, event["health"]["incident_model"])
        # The helper reads exactly what the signed event carries.
        self.assertEqual(HAND_ROUTE, agent_job_helper._selected_incident_model(event))

    def test_a_delivery_without_a_snapshot_carries_no_route_into_the_event(self) -> None:
        result = health_event(self.center, "producer-token", "evt-chain-legacy")
        delivery = self._agent_delivery(str(result["incident_id"]))
        # Model a delivery persisted before route snapshots existed.
        self.center._connection.execute("UPDATE deliveries SET target_json = '{}' WHERE id = ?", (delivery["id"],))
        delivery = self._agent_delivery(str(result["incident_id"]))
        payload = self.center.delivery_payload(delivery)
        self.assertNotIn("incident_model", payload.get("health_context") or {})
        event = _agent_job_event("health-diagnosis", payload)
        self.assertNotIn("incident_model", event["health"])
        self.assertEqual("", agent_job_helper._selected_incident_model(event))

    def test_a_remediation_delivery_snapshots_the_route_too(self) -> None:
        """The remediation branch is a second scheduling site, not a copy of the first."""
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        result = health_event(self.center, "producer-token", "evt-remediation")
        incident_id = str(result["incident_id"])
        workflow = HealthWorkflow(self.center, callback_secret="x" * 32)
        workflow.attach_plans(incident_id, "plans", [
            {"plan_id": "observe", "title": "Observe", "summary": "Проверить", "step": "Проверить"},
            {"plan_id": "repair", "title": "Repair", "summary": "Исправить", "step": "Исправить"},
            {"plan_id": "verify", "title": "Verify", "summary": "Проверить", "step": "Проверить"},
        ], actor="operator")
        workflow.select_plan(incident_id, "selection", "repair", "telegram:42")
        rows = self.center._connection.execute(
            "SELECT * FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-remediation'",
            (incident_id,),
        ).fetchall()
        self.assertEqual(1, len(rows))
        delivery = dict(rows[0])
        self.assertEqual({"incident_model": HAND_ROUTE}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(HAND_ROUTE, payload["health_context"]["incident_model"])
        event = _agent_job_event("health-remediation", payload)
        self.assertEqual(HAND_ROUTE, event["health"]["incident_model"])


class _SettingCenter:
    """Minimal operator-setting reader used by the route checks."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def get_runtime_setting(self, key: str, default: str | None = None) -> str | None:
        return self._value if key == "health_incident_model" else default


class SelectedRouteLaunchTests(unittest.TestCase):
    """The helper launches the selected route and fails closed without it."""

    def _profile(self, **overrides: str) -> dict[str, str]:
        profile = {
            "url": "http://127.0.0.1:18787/api/sessions/new-or-resume",
            "harness": "opencode",
            "name": "health-diagnosis",
            "cwd": "/home/roomhacker/ServersAdministartion",
            "mode": "queue",
            "instruction": "Repair the original incident in its native session.",
            "model": LEGACY_MODEL,
            "reasoning": "default",
            "topic": "health",
            "health_launch_token": "test-health-launch-token",
        }
        profile.update(overrides)
        return profile

    def _policy(self, **overrides: object) -> dict[str, object]:
        policy: dict[str, object] = {
            "version": 1,
            "allowedHarnesses": ["opencode", "codex"],
            "preferredHarness": "opencode",
            "models": {"opencode": "omniroute/minimax/other", "codex": "gpt-6.1-sol"},
            "incidentExecution": {"runtime": "opencode", "provider": "minimax-coding-plan", "model": "MiniMax-M3.1-Flash", "reasoning": "default", "topic": "health"},
        }
        policy.update(overrides)
        return policy

    def _event(self, model: str = "") -> dict[str, object]:
        event: dict[str, object] = {
            "schema": "notify.agent-job.v1",
            "job_id": "health-diagnosis",
            "incident": {"id": "inc-selected", "project": "fleet-health", "severity": "critical", "title": "Диск", "body": "b", "dedup_key": "k", "occurrences": "1"},
        }
        if model:
            event["health"] = {"incident_model": model}
        return event

    def _runner(self, policy: dict[str, object], calls: list) -> object:
        def runner(request, **_options):
            calls.append(request)
            if request.full_url.endswith("/api/automation/launch-policy"):
                return _Response(policy)
            body = json.loads(request.data) if request.data else {}
            return _Response({"outcome": "accepted", "operation_id": "op-1", "project": "fleet-health",
                              "selected_harness": body.get("harness") or "opencode",
                              "model": body.get("model") or LEGACY_MODEL, "mode": "queue",
                              "session_id": "ses-1", "delivery": "accepted", "routing_reason": "declared route",
                              "attempts": []})
        return runner

    def test_the_selected_route_launches_with_purpose_and_dedicated_bearer(self) -> None:
        calls: list = []
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")), \
             mock.patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}):
            receipt = run_profile("health-diagnosis", self._event(HAND_ROUTE), Path("unused"),
                                  profile_override=self._profile(), runner=self._runner(self._policy(), calls))
        launch = [call for call in calls if call.full_url.endswith("/api/automation/project-launch")]
        self.assertEqual(1, len(launch))
        body = json.loads(launch[0].data)
        self.assertEqual(HEALTH_LAUNCH_PURPOSE, body["purpose"])
        self.assertEqual("opencode", body["harness"])
        self.assertEqual(HAND_ROUTE, body["model"])
        self.assertTrue(body["operation_id"])
        self.assertEqual("Bearer test-health-launch-token", launch[0].get_header("Authorization"))
        self.assertNotIn("test-health-launch-token", json.dumps(body))
        self.assertEqual(HAND_ROUTE, receipt["model"])

    def test_the_stored_route_reaches_the_herder_launch_payload_unchanged(self) -> None:
        """Console -> runtime_settings -> delivery -> signed event -> launch."""
        with tempfile.TemporaryDirectory() as tempdir:
            center = NotificationCenter(Path(tempdir) / "notice.sqlite3", {"producer-token": {"project": "fleet-health", "max_severity": "critical", "agent_jobs": ["health-diagnosis"]}}, default_quiet_hours=[])
            try:
                center.set_runtime_setting("health_incident_model", HAND_ROUTE)
                result = health_event(center, "producer-token", "evt-round-trip")
                rows = center._connection.execute(
                    "SELECT * FROM deliveries WHERE incident_id = ? AND channel = 'gptadmin.agent:health-diagnosis'",
                    (str(result["incident_id"]),),
                ).fetchall()
                self.assertEqual(1, len(rows))
                payload = center.delivery_payload(dict(rows[0]))
                self.assertEqual(HAND_ROUTE, payload["health_context"]["incident_model"])
                event = _agent_job_event("health-diagnosis", payload)
            finally:
                center._connection.close()
        calls: list = []
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")), \
             mock.patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}):
            receipt = run_profile("health-diagnosis", event, Path("unused"),
                                  profile_override=self._profile(), runner=self._runner(self._policy(), calls))
        launch = [call for call in calls if call.full_url.endswith("/api/automation/project-launch")]
        self.assertEqual(1, len(launch))
        body = json.loads(launch[0].data)
        self.assertEqual(HAND_ROUTE, body["model"])
        self.assertEqual(HAND_ROUTE, receipt["model"])

    def test_a_delivery_without_a_snapshot_keeps_the_legacy_launch(self) -> None:
        calls: list = []
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")), \
             mock.patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}):
            receipt = run_profile("health-diagnosis", self._event(), Path("unused"),
                                  profile_override=self._profile(), runner=self._runner(self._policy(), calls))
        launch = [call for call in calls if call.full_url.endswith("/api/automation/project-launch")]
        self.assertEqual(1, len(launch))
        body = json.loads(launch[0].data)
        self.assertNotIn("purpose", body)
        self.assertIsNone(launch[0].get_header("Authorization"))
        self.assertEqual(LEGACY_MODEL, body["model"])
        self.assertEqual(LEGACY_MODEL, receipt["model"])

    def test_a_missing_token_fails_closed_before_any_launch(self) -> None:
        calls: list = []
        profile = self._profile()
        profile.pop("health_launch_token")
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")):
            with self.assertRaisesRegex(RuntimeError, "no provider fallback"):
                run_profile("health-diagnosis", self._event(HAND_ROUTE), Path("unused"),
                            profile_override=profile, runner=self._runner(self._policy(), calls))
        self.assertEqual([], [call for call in calls if call.full_url.endswith("/api/automation/project-launch")])

    def test_an_unapproved_snapshot_route_fails_closed_before_any_launch(self) -> None:
        calls: list = []
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")):
            with self.assertRaisesRegex(RuntimeError, "unapproved incident model"):
                run_profile("health-diagnosis", self._event("codex/gpt-6.1-sol"), Path("unused"),
                            profile_override=self._profile(), runner=self._runner(self._policy(), calls))
        self.assertEqual([], [call for call in calls if call.full_url.endswith("/api/automation/project-launch")])

    def test_a_paid_snapshot_route_fails_closed_before_any_launch(self) -> None:
        for route in PAID_ROUTES:
            with self.subTest(route=route):
                calls: list = []
                with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")):
                    with self.assertRaisesRegex(RuntimeError, "unapproved incident model"):
                        run_profile("health-diagnosis", self._event(route), Path("unused"),
                                    profile_override=self._profile(), runner=self._runner(self._policy(), calls))
                self.assertEqual([], [call for call in calls if call.full_url.endswith("/api/automation/project-launch")])

    def test_a_project_without_opencode_fails_closed_before_any_launch(self) -> None:
        calls: list = []
        policy = self._policy(projectRoutes={"fleet-health": {"harnesses": ["codex"]}})
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")):
            with self.assertRaisesRegex(RuntimeError, "no harness fallback"):
                run_profile("health-diagnosis", self._event(HAND_ROUTE), Path("unused"),
                            profile_override=self._profile(), runner=self._runner(policy, calls))
        self.assertEqual([], [call for call in calls if call.full_url.endswith("/api/automation/project-launch")])

    def test_a_policy_without_opencode_fails_closed_before_any_launch(self) -> None:
        calls: list = []
        policy = self._policy(allowedHarnesses=["codex"])
        with mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://127.0.0.1:8091", "t")):
            with self.assertRaisesRegex(RuntimeError, "no harness fallback"):
                run_profile("health-diagnosis", self._event(HAND_ROUTE), Path("unused"),
                            profile_override=self._profile(), runner=self._runner(policy, calls))
        self.assertEqual([], [call for call in calls if call.full_url.endswith("/api/automation/project-launch")])

    def test_the_protected_profile_field_is_read_only_from_a_0600_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = Path(tempdir) / "agent-jobs.json"
            config.write_text(json.dumps({"profiles": {"health-diagnosis": {"health_launch_token": "secret-token"}}}),
                              encoding="utf-8")
            config.chmod(0o600)
            self.assertEqual("secret-token", agent_job_helper.protected_launch_token("health-diagnosis", config))
            config.chmod(0o644)
            self.assertEqual("", agent_job_helper.protected_launch_token("health-diagnosis", config))


if __name__ == "__main__":
    unittest.main()
