"""NoticePlace model intent and Agent Herder policy ownership.

Notice Place snapshots delegated project-route intent; Herder owns effective
model selection and launch admission.
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
                                                HealthWorkflow, normalize_incident_model, selected_incident_model)
from notification_center.incident_model_policy import PROJECT_ROUTE_INTENT, incident_model_state, write_incident_model

DEFAULT_ROUTE = "minimax-coding-plan/MiniMax-M3.1-Flash"
HAND_ROUTE = DEFAULT_ROUTE
SECOND_HAND_ROUTE = "omniroute/qwen/qwen3-max"
PAID_ROUTES = (
    "omniroute/minimax-combo-m3.1",
    "omniroute/minimax-combo.m3.1",
    "omniroute/minimax-coding-plan/MiniMax-M3.1-Flash",
    "minimax/MiniMax-M3.1-Flash",
    "omniroute/minimax/MiniMax-M3.1-Flash",
    "omniroute/openai/gpt-5.1",
    "omniroute/qwen/qwen3-max",
)
LEGACY_MODEL = DEFAULT_ROUTE


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

    def test_new_delivery_snapshots_herder_project_route_intent(self) -> None:
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        result = health_event(self.center, "producer-token", "evt-1")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": "herder/project-route"}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual("herder/project-route", payload["health_context"]["incident_model"])

    def test_the_delivery_re_filter_accepts_the_same_routes_as_the_write_path(self) -> None:
        """The sole approved route is not dropped by the delivery read path."""
        for route in ("herder/project-route", "provider/arbitrary-model"):
            with self.subTest(route=route):
                self.assertEqual(route, self.center._delivery_incident_model({"incident_model": route}))

    def test_delivery_rejects_malformed_route_syntax(self) -> None:
        for route in ("malformed", "provider/model\nforbidden"):
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
        self.assertEqual({"incident_model": "herder/project-route"}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual("herder/project-route", payload["health_context"]["incident_model"])

    def test_an_unusable_stored_route_is_never_snapshotted_and_blocks_the_launch(self) -> None:
        """An invalid stored value is not replaced by another model."""
        self.center.set_runtime_setting("health_incident_model", "codex/gpt-6.1-sol")
        self.assertEqual("codex/gpt-6.1-sol", self.center.get_runtime_setting("health_incident_model"))
        result = health_event(self.center, "producer-token", "evt-invalid")
        delivery = self._agent_delivery(str(result["incident_id"]))
        target = json.loads(delivery["target_json"])
        self.assertEqual({"incident_model": "herder/project-route"}, target)
        self.assertEqual("herder/project-route", self.center.delivery_payload(delivery)["health_context"]["incident_model"])

    def test_a_paid_stored_route_blocks_the_launch_instead_of_another_model(self) -> None:
        self.center.set_runtime_setting("health_incident_model", "omniroute/minimax/MiniMax-M3.1-Flash")
        self.assertEqual("omniroute/minimax/MiniMax-M3.1-Flash", self.center.get_runtime_setting("health_incident_model"))
        result = health_event(self.center, "producer-token", "evt-paid")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual("herder/project-route", json.loads(delivery["target_json"])["incident_model"])

    def test_an_unset_setting_resolves_to_the_canonical_default(self) -> None:
        self.assertEqual("", HEALTH_LAUNCH_DEFAULT)

        self.center.set_runtime_setting("health_incident_model", "")
        result = health_event(self.center, "producer-token", "evt-unset")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": "herder/project-route"}, json.loads(delivery["target_json"]))

    def test_legacy_saved_default_does_not_migrate_into_new_delivery(self) -> None:
        self.center.set_runtime_setting("health_incident_model", "omniroute/minimax-combo.m3.1")
        result = health_event(self.center, "producer-token", "evt-setting-migration")
        delivery = self._agent_delivery(str(result["incident_id"]))
        self.assertEqual({"incident_model": "herder/project-route"}, json.loads(delivery["target_json"]))
        self.assertEqual("herder/project-route", self.center.delivery_payload(delivery)["health_context"]["incident_model"])
        # A migration read does not mutate persisted settings or any old delivery.
        self.assertEqual("omniroute/minimax-combo.m3.1", self.center.get_runtime_setting("health_incident_model"))
        self.assertEqual("omniroute/minimax-combo.m3.1", self.center.get_runtime_setting("health_incident_model"))

    def test_local_route_validation_checks_syntax_without_whitelist(self) -> None:
        self.assertEqual((), HEALTH_LAUNCH_MODELS)
        for route in ("provider/model", "vendor/model/variant"):
            with self.subTest(route=route):
                self.assertEqual(route, normalize_incident_model(route))
        for invalid in ("", "malformed", "provider/model\nforbidden", "x" * 257):
            with self.subTest(route=invalid), self.assertRaises(ValueError):
                normalize_incident_model(invalid)

    def test_herder_policy_fake_read_and_write_use_revision(self) -> None:
        class FakeHerderPolicyClient:
            policy = {"incidentExecution": {"runtime": "opencode", "provider": "p", "model": "p/old"}}
            revision = '"v1"'
            writes: list[tuple[str, str]] = []
            def read(self):
                return self.policy, self.revision
            def set_incident_model(self, model: str, expected_revision: str):
                if expected_revision != self.revision:
                    raise RuntimeError("stale Herder revision")
                self.writes.append((model, expected_revision))
                provider = model.partition("/")[0]
                self.policy["incidentExecution"] = {"runtime": "opencode", "provider": provider, "model": model}
                self.revision = '"v2"'
                return self.policy
        fake = FakeHerderPolicyClient()
        with mock.patch("notification_center.herder_policy_client.HerderPolicyClient", return_value=fake):
            self.assertEqual("p/old", selected_incident_model(self.center))
            self.assertEqual("p/new", write_incident_model(self.center, "p/new", '"v1"'))
        self.assertEqual([("p/new", '"v1"')], fake.writes)

    def test_the_snapshot_rides_only_in_the_signed_health_context(self) -> None:
        payload = {"incident": {"id": "inc-1", "project": "fleet-health"}, "health_context": {"incident_model": PROJECT_ROUTE_INTENT}}
        event = _agent_job_event("health-diagnosis", payload)
        self.assertEqual(PROJECT_ROUTE_INTENT, event["health"]["incident_model"])
        unsigned = {"incident": {"id": "inc-1", "project": "fleet-health"}, "health_context": {}}
        self.assertNotIn("incident_model", _agent_job_event("health-diagnosis", unsigned)["health"])

    def test_the_real_delivery_path_carries_the_snapshot_into_the_signed_event(self) -> None:
        """The whole chain: create incident -> delivery row -> payload -> event."""
        self.center.set_runtime_setting("health_incident_model", HAND_ROUTE)
        result = health_event(self.center, "producer-token", "evt-chain")
        delivery = self._agent_delivery(str(result["incident_id"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(PROJECT_ROUTE_INTENT, payload["health_context"]["incident_model"])
        event = _agent_job_event("health-diagnosis", payload)
        self.assertEqual(PROJECT_ROUTE_INTENT, event["health"]["incident_model"])
        # The helper reads exactly what the signed event carries.
        self.assertEqual(PROJECT_ROUTE_INTENT, agent_job_helper._selected_incident_model(event))

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
        self.assertEqual({"incident_model": PROJECT_ROUTE_INTENT}, json.loads(delivery["target_json"]))
        payload = self.center.delivery_payload(delivery)
        self.assertEqual(PROJECT_ROUTE_INTENT, payload["health_context"]["incident_model"])
        event = _agent_job_event("health-remediation", payload)
        self.assertEqual(PROJECT_ROUTE_INTENT, event["health"]["incident_model"])


class _SettingCenter:
    """Minimal operator-setting reader used by the route checks."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def get_runtime_setting(self, key: str, default: str | None = None) -> str | None:
        return self._value if key == "health_incident_model" else default


class SelectedRouteLaunchTests(unittest.TestCase):
    """Fast offline contract tests at the Herder admission boundary."""

    def _profile(self, with_token: bool = True) -> dict[str, str]:
        profile = {"url": "http://127.0.0.1:18787/api/sessions/new-or-resume", "harness": "opencode",
                  "name": "health-diagnosis", "cwd": "/tmp", "mode": "queue",
                  "instruction": "Repair the incident.", "model": "legacy/local-route",
                  "reasoning": "default", "topic": "health"}
        if with_token:
            profile["health_launch_token"] = "fixture-token"
        return profile

    def _event(self, intent: str = "herder/project-route") -> dict[str, object]:
        return {"schema": "notify.agent-job.v1", "job_id": "health-diagnosis",
                "incident": {"id": "inc-1", "project": "fleet-health", "severity": "critical",
                             "title": "Disk", "body": "Full", "dedup_key": "disk"},
                "health": {"incident_model": intent}}

    def _run(self, launch_result: dict[str, object], intent: str = "herder/project-route",
             with_token: bool = True):
        launch = mock.Mock(return_value=launch_result)
        with mock.patch.object(agent_job_helper, "require_herder_owner_running"), \
             mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://fixture", "fixture")), \
             mock.patch.object(agent_job_helper, "source_launch_fields", return_value={}), \
             mock.patch.object(agent_job_helper, "project_launch", launch), \
             mock.patch.object(agent_job_helper, "arm_session_autopilot", return_value=True), \
             mock.patch.object(agent_job_helper, "_run_health_diagnosis", return_value={"status": "completed"}):
            result = run_profile("health-diagnosis", self._event(intent), Path("/unused"),
                                 profile_override=self._profile(with_token))
        return result, launch

    def test_new_project_intent_omits_model_and_uses_herder_receipt(self) -> None:
        receipt, launch = self._run({"outcome": "accepted", "session_id": "s1", "selected_harness": "opencode",
                                     "model": "provider/chosen", "operation_id": "op-1"})
        self.assertEqual("", launch.call_args.kwargs["model"])
        self.assertEqual("provider/chosen", receipt["model"])

    def test_concrete_old_snapshot_is_forwarded_exactly(self) -> None:
        _, launch = self._run({"outcome": "accepted", "session_id": "s1", "selected_harness": "opencode",
                               "model": "legacy/provider-model", "operation_id": "op-1"},
                              intent="legacy/provider-model")
        self.assertEqual("legacy/provider-model", launch.call_args.kwargs["model"])

    def test_dedicated_launch_bearer_is_header_only(self) -> None:
        calls = []
        def runner(request, **_kwargs):
            calls.append(request)
            return _Response({"outcome": "accepted", "session_id": "s1", "selected_harness": "opencode",
                              "model": "provider/chosen", "operation_id": "op-1"})
        with mock.patch.object(agent_job_helper, "require_herder_owner_running"):
            agent_job_helper.project_launch(self._profile(), "fleet-health", "/tmp", "repair", runner,
                operation_id="op-1", harness="opencode", purpose=HEALTH_LAUNCH_PURPOSE,
                health_launch_token="fixture-token")
        self.assertEqual(1, len(calls))
        self.assertEqual("Bearer fixture-token", calls[0].get_header("Authorization"))
        self.assertNotIn("fixture-token", calls[0].data.decode())

    def test_herder_structured_no_harness_receipt_is_not_replayed(self) -> None:
        launch = mock.Mock(side_effect=lambda *args, **kwargs: {
            "outcome": "no_harness", "operation_id": kwargs["operation_id"], "project": "fleet-health",
            "routing_reason_code": "model_not_permitted_for_project", "routing_reason": "explicitly forbidden",
            "attempts": [], "created": False, "accepted": False, "admitted": False,
            "pending": False, "uncertain": False, "session_id": "", "delivery": "not_attempted",
        })
        with mock.patch.object(agent_job_helper, "require_herder_owner_running"), \
             mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://fixture", "fixture")), \
             mock.patch.object(agent_job_helper, "source_launch_fields", return_value={}), \
             mock.patch.object(agent_job_helper, "project_launch", launch):
            receipt = run_profile("health-diagnosis", self._event("provider/blocked"), Path("/unused"),
                                  profile_override=self._profile())
        self.assertEqual("not_attempted", receipt["admission"])
        self.assertEqual("herder_rejected", receipt["reason"])
        self.assertFalse(receipt["retryable"])
        self.assertEqual(1, launch.call_count)

    def test_unknown_admission_is_not_replayed(self) -> None:
        launches = []
        def unknown(*_args, **_kwargs):
            launches.append(True)
            raise RuntimeError("Agent Herder launch stopped as admission_unknown for operation op-1")
        with mock.patch.object(agent_job_helper, "require_herder_owner_running"), \
             mock.patch.object(agent_job_helper, "_load_health_callback", return_value=("http://fixture", "fixture")), \
             mock.patch.object(agent_job_helper, "source_launch_fields", return_value={}), \
             mock.patch.object(agent_job_helper, "project_launch", side_effect=unknown):
            with self.assertRaisesRegex(RuntimeError, "admission_unknown"):
                run_profile("health-diagnosis", self._event(), Path("/unused"), profile_override=self._profile())
        self.assertEqual(1, len(launches))

    def test_missing_dedicated_token_fails_closed_before_launch(self) -> None:
        calls = []
        def runner(request, **kwargs):
            calls.append(request)
            return _Response({})
        with mock.patch.object(agent_job_helper, "require_herder_owner_running"):
            with self.assertRaisesRegex(RuntimeError, "dedicated launch token"):
                agent_job_helper.project_launch(self._profile(with_token=False), "fleet-health", "/tmp", "repair",
                    runner, purpose=HEALTH_LAUNCH_PURPOSE, health_launch_token="")
        self.assertEqual([], calls)

if __name__ == "__main__":
    unittest.main()
