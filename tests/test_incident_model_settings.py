"""Focused tests for the operator-controlled health incident analysis route.

The operator types the route by hand: any OmniRoute gateway route of the form
``omniroute/<model>`` or ``omniroute/<provider>/<model>``. The paid MiniMax
subscription route stays forbidden, and an invalid stored value stays visible
instead of being silently replaced.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.admin import AdminConfigStore, MODEL_LOOKUP_INCIDENT_LIMIT
from notification_center.admin_http import build_admin_handler
from notification_center.core import NotificationCenter, ValidationError
from notification_center.health_workflow import HealthWorkflow, selected_incident_model
from notification_center.incident_model_policy import (
    DEFAULT_INCIDENT_MODEL,
    INCIDENT_MODEL_SETTING_KEY,
    MAX_INCIDENT_MODEL_LENGTH,
    MAX_LAUNCH_ROUTE_LENGTH,
    default_incident_model,
    incident_model_label,
    incident_model_options,
    is_incident_model,
    normalize_incident_model,
    read_incident_model,
)

DEFAULT_ROUTE = "omniroute/minimax-combo-m3.1"
HAND_ROUTE = "omniroute/openai/gpt-5.1"
SECOND_HAND_ROUTE = "omniroute/qwen/qwen3-max"
PAID_ROUTES = (
    "minimax-coding-plan/MiniMax-M3.1-Flash",
    "omniroute/minimax-coding-plan/MiniMax-M3.1-Flash",
    "minimax/MiniMax-M3.1-Flash",
    "omniroute/minimax/MiniMax-M3.1-Flash",
)
HEALTH_JOB_NAMES = ("health-diagnosis", "health-remediation")


class IncidentModelSettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.primary = root / "notification-center.env"
        self.routes = root / "routes.env"
        self.database = root / "notify-center.sqlite3"
        self.primary.write_text(
            f"NOTIFY_CENTER_DB={self.database}\n"
            'NOTIFY_CENTER_TOKENS_JSON={"old-token":{"project":"existing","max_severity":"notice"}}\n'
            "OTHER=unchanged\n",
            encoding="utf-8",
        )
        self.routes.write_text("TELEGRAM_SEVERITY_ROUTES_JSON={}\n", encoding="utf-8")
        self.health_config = root / "health-incident-fleet.json"
        self.health_config.write_text(json.dumps({"targets": []}), encoding="utf-8")
        self.restarts = 0
        self.store = AdminConfigStore(
            self.primary,
            self.routes,
            root / "state",
            restart=self._restart,
            calls_override_path=root / "calls.conf",
            daemon_reload=lambda: None,
            health_config_path=self.health_config,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_admin_handler(self.store, "test-csrf-secret"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tempdir.cleanup()

    def _restart(self) -> None:
        self.restarts += 1

    def _request(self, method: str, path: str, form: dict[str, str] | None = None, *, admin: bool = True) -> tuple[int, bytes]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
        if admin:
            headers["X-Notify-Admin"] = "1"
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def _request_no_redirect(self, method: str, path: str, form: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        """POST without following the redirect, so the operator flow is observable."""
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"Content-Type": "application/x-www-form-urlencoded", "X-Notify-Admin": "1"}
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):  # type: ignore[override]
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=2) as response:
                return response.status, response.headers.get("Location") or "", response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or "", error.read()

    def _csrf(self) -> str:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        token = re.search(rb'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(token)
        return token.group(1).decode()

    def _center(self) -> NotificationCenter:
        return self.store._consumer_notification_center()

    def _stored_value(self) -> str | None:
        row = self._center()._connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (INCIDENT_MODEL_SETTING_KEY,)
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def _force_stored_value(self, value: str) -> None:
        """Write the setting behind the console, as an operator or a migration would."""
        self._center().set_runtime_setting(INCIDENT_MODEL_SETTING_KEY, value)

    def _record_job_receipt(self, model: str, job: str = "health-diagnosis", status: str = "completed") -> str:
        """Persist an accepted health agent session that names the model route."""
        center = self._center()
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal("old-token", f"model-receipt-{job}", {
            "project": "existing", "recipient": "me", "severity": "notice",
            "title": "Model receipt", "body": "Model receipt",
            "dedup_key": f"health:model-receipt:{job}",
            "source_id": "host:roomhacker-server-100", "host_id": "roomhacker-server-100", "signal_type": "disk",
        })
        incident_id = str(created["incident_id"])
        center.record_health_agent_session(
            incident_id, f"model-session-{job}", "", "opencode", f"ses-{job}",
            "https://agent.bezrabotnyi.com", actor="worker", stage="health-diagnosis", model=model,
        )
        return incident_id

    def _record_unrelated_job_receipt(self, model: str) -> str:
        """Persist an agent-job receipt that is not a health session event."""
        center = self._center()
        created = center.create_event("old-token", "unrelated-job-receipt", {
            "project": "existing", "recipient": "me", "kind": "notification", "severity": "notice",
            "title": "Unrelated", "body": "Unrelated", "dedup_key": "unrelated:job:receipt",
        })
        incident_id = str(created["incident_id"])
        center.record_agent_job_result(incident_id, "dlv-test", "some-other-job", {
            "job_id": "hub-2", "status": "completed", "elapsed_ms": 5,
            "agent_receipt": {"session_id": "ses_other", "harness": "opencode", "model": model},
        })
        return incident_id

    # --- the route contract ---------------------------------------------

    def test_the_canonical_free_route_is_the_default(self) -> None:
        self.assertEqual(DEFAULT_ROUTE, default_incident_model())
        self.assertEqual(DEFAULT_ROUTE, DEFAULT_INCIDENT_MODEL)
        options = incident_model_options()
        self.assertEqual([DEFAULT_ROUTE], [option["value"] for option in options])
        self.assertTrue(options[0]["default"])
        self.assertFalse(options[0]["manual_only"])
        self.assertEqual("OpenCode · OmniRoute · MiniMax M3.1 (combo)", options[0]["label"])

    def test_any_hand_typed_omniroute_route_is_valid(self) -> None:
        for route in (DEFAULT_ROUTE, HAND_ROUTE, SECOND_HAND_ROUTE, "omniroute/some-model"):
            with self.subTest(route=route):
                self.assertTrue(is_incident_model(route))
                self.assertEqual(route, normalize_incident_model(route))
        self.assertEqual(HAND_ROUTE, incident_model_label(HAND_ROUTE))

    def test_a_saved_route_is_trimmed_but_not_rewritten(self) -> None:
        self.assertEqual(HAND_ROUTE, normalize_incident_model(f"  {HAND_ROUTE}  "))

    def test_a_route_of_the_maximum_length_is_accepted(self) -> None:
        """The signed launch context bounds the route, so the bound is exact."""
        route = "omniroute/" + "x" * (MAX_LAUNCH_ROUTE_LENGTH - len("omniroute/"))
        self.assertEqual(MAX_LAUNCH_ROUTE_LENGTH, len(route))
        self.assertTrue(is_incident_model(route))

    def test_the_paid_minimax_subscription_route_stays_rejected(self) -> None:
        for route in PAID_ROUTES:
            with self.subTest(route=route):
                self.assertFalse(is_incident_model(route))
                with self.assertRaises(ValueError):
                    normalize_incident_model(route)

    def test_malformed_and_oversized_routes_are_rejected(self) -> None:
        invalid = (
            "",
            "   ",
            "codex/gpt-6.1-sol",
            "omniroute/too/many/segments",
            "omniroute/",
            "omniroute/a\nb",
            "omniroute/\x00null",
            "omniroute/" + "x" * (MAX_LAUNCH_ROUTE_LENGTH - len("omniroute/") + 1),
            "omniroute/" + "x" * (MAX_INCIDENT_MODEL_LENGTH + 1),
        )
        for route in invalid:
            with self.subTest(route=route):
                self.assertFalse(is_incident_model(route))
                with self.assertRaises(ValueError):
                    normalize_incident_model(route)

    # --- persistence ------------------------------------------------------

    def test_a_hand_typed_route_is_stored_and_read_back(self) -> None:
        for route in (HAND_ROUTE, SECOND_HAND_ROUTE, DEFAULT_ROUTE):
            with self.subTest(route=route):
                self.assertEqual(route, self.store.set_incident_model(route, "sso:operator"))
                self.assertEqual(route, self._stored_value())
                self.assertEqual(route, read_incident_model(self._center()))
                self.assertEqual(route, selected_incident_model(self._center()))
                self.assertEqual(route, self.store.incident_model_setting()["value"])
        self.assertEqual(0, self.restarts)

    def test_the_default_is_used_until_the_operator_saves_something(self) -> None:
        state = self.store.incident_model_setting()
        self.assertEqual(DEFAULT_ROUTE, state["value"])
        self.assertEqual(DEFAULT_ROUTE, state["default"])
        self.assertIsNone(state["stored"])
        self.assertIsNone(state["stored_error"])
        self.assertIsNone(self._stored_value())

    def test_an_invalid_route_is_rejected_without_mutation(self) -> None:
        invalid = ("", "   ", "codex/gpt-6.1-sol", "omniroute/a/b/c", "omniroute/\x00null") + PAID_ROUTES
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.store.set_incident_model(value, "sso:operator")
                self.assertIsNone(self._stored_value())
                self.assertFalse(self.store.audit_path.exists())

    def test_an_oversized_route_is_rejected_without_mutation(self) -> None:
        value = "omniroute/" + "x" * (MAX_LAUNCH_ROUTE_LENGTH - len("omniroute/") + 1)
        with self.assertRaises(ValidationError):
            self.store.set_incident_model(value, "sso:operator")
        self.assertIsNone(self._stored_value())
        self.assertFalse(self.store.audit_path.exists())

    def test_an_invalid_stored_value_is_visible_and_never_replaced(self) -> None:
        """A stored value that is not usable keeps its reason instead of a fallback."""
        self._force_stored_value("codex/gpt-6.1-sol")
        state = self.store.incident_model_setting()
        self.assertEqual("codex/gpt-6.1-sol", state["stored"])
        self.assertFalse(state["stored_recognized"])
        self.assertIsNotNone(state["stored_error"])
        self.assertEqual("", state["effective"])
        # The policy module stays dependency-free and rejects with a plain
        # ValueError; the domain read turns it into the ValidationError that
        # the launch path fails closed on.
        with self.assertRaises(ValueError):
            read_incident_model(self._center())
        with self.assertRaises(ValidationError):
            selected_incident_model(self._center())
        # The stored value itself is not rewritten.
        self.assertEqual("codex/gpt-6.1-sol", self._stored_value())

    def test_a_paid_stored_route_is_rejected_at_the_read_boundary(self) -> None:
        self._force_stored_value("omniroute/minimax/MiniMax-M3.1-Flash")
        state = self.store.incident_model_setting()
        self.assertIsNotNone(state["stored_error"])
        with self.assertRaises(ValidationError):
            selected_incident_model(self._center())

    def test_the_audit_records_actor_action_and_model_without_secrets(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        records = [json.loads(line) for line in self.store.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual({"timestamp", "actor", "action", "model", "label"}, set(record))
        self.assertEqual("sso:operator", record["actor"])
        self.assertEqual("incident_model_changed", record["action"])
        self.assertEqual(HAND_ROUTE, record["model"])
        self.assertEqual(HAND_ROUTE, record["label"])
        self.assertNotIn("secret", json.dumps(record).lower())
        self.assertNotIn("password", json.dumps(record).lower())

    # --- HTTP surface -----------------------------------------------------

    def test_csrf_failure_does_not_mutate_the_saved_model(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        status, page = self._request("POST", "/admin/health-model", {"csrf": "wrong", "model": SECOND_HAND_ROUTE})
        self.assertEqual(400, status)
        self.assertIn(b"Configuration rejected", page)
        self.assertEqual(HAND_ROUTE, self._stored_value())

    def test_unauthorized_post_is_denied(self) -> None:
        status, page = self._request("POST", "/admin/health-model", {"csrf": "x", "model": HAND_ROUTE}, admin=False)
        self.assertEqual(403, status)
        self.assertIn(b"Access denied", page)
        self.assertIsNone(self._stored_value())

    def test_http_save_accepts_a_hand_typed_route(self) -> None:
        csrf = self._csrf()
        status, location, _ = self._request_no_redirect("POST", "/admin/health-model", {"csrf": csrf, "model": HAND_ROUTE})
        self.assertEqual(303, status)
        self.assertEqual("/admin/#health-model", location)
        self.assertEqual(HAND_ROUTE, self._stored_value())
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(f'value="{HAND_ROUTE}"'.encode(), page)

    def test_http_save_rejects_a_paid_route_without_mutation(self) -> None:
        csrf = self._csrf()
        for route in PAID_ROUTES:
            with self.subTest(route=route):
                status, page = self._request("POST", "/admin/health-model", {"csrf": csrf, "model": route})
                self.assertEqual(400, status)
                self.assertIn(b"Configuration rejected", page)
                self.assertIn("Платный маршрут MiniMax по подписке".encode(), page)
                self.assertIsNone(self._stored_value())

    def test_http_save_rejects_a_malformed_route_without_mutation(self) -> None:
        csrf = self._csrf()
        for value in ("", "codex/gpt-6.1-sol", "omniroute/a/b/c", "omniroute/\x00null"):
            with self.subTest(value=value):
                status, page = self._request("POST", "/admin/health-model", {"csrf": csrf, "model": value})
                self.assertEqual(400, status)
                self.assertIn(b"Configuration rejected", page)
                self.assertIsNone(self._stored_value())

    def test_http_save_rejects_an_oversized_route_without_mutation(self) -> None:
        csrf = self._csrf()
        value = "omniroute/" + "x" * (MAX_LAUNCH_ROUTE_LENGTH - len("omniroute/") + 1)
        status, page = self._request("POST", "/admin/health-model", {"csrf": csrf, "model": value})
        self.assertEqual(400, status)
        self.assertIn(b"Configuration rejected", page)
        self.assertIsNone(self._stored_value())

    # --- rendering --------------------------------------------------------

    def _model_cells(self, page: bytes) -> tuple[str, str, str]:
        """Split the rendered model block into saved, effective and used cells."""
        text = page.decode("utf-8")
        saved_at = text.index("Сохранённый выбор — для новых разборов")
        effective_at = text.index("Фактически используемый маршрут — следующий запуск")
        used_at = text.index("Фактически использована в последнем запуске")
        return (
            text[saved_at:effective_at],
            text[effective_at:used_at],
            text[used_at:text.index("</div></div>", used_at)],
        )

    def test_dashboard_shows_the_saved_value_and_the_effective_route(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b'id="health-model"', page)
        self.assertIn("Сохранённый выбор — для новых разборов".encode(), page)
        self.assertIn("Фактически используемый маршрут — следующий запуск".encode(), page)
        self.assertIn("Фактически использована в последнем запуске".encode(), page)
        saved_cell, effective_cell, used_cell = self._model_cells(page)
        self.assertIn(HAND_ROUTE, saved_cell)
        self.assertIn(HAND_ROUTE, effective_cell)
        self.assertIn("Ещё не подтверждено запуском", used_cell)
        self.assertNotIn(HAND_ROUTE, used_cell)

    def test_dashboard_offers_a_free_form_field_with_known_route_suggestions(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b'<input type="text" name="model"', page)
        self.assertIn(b'list="incident-model-routes"', page)
        self.assertIn(b'<datalist id="incident-model-routes">', page)
        self.assertIn(f'<option value="{DEFAULT_ROUTE}">'.encode(), page)
        self.assertNotIn(b'<select name="model">', page)

    def test_dashboard_shows_a_visible_error_for_an_invalid_stored_value(self) -> None:
        self._force_stored_value("codex/gpt-6.1-sol")
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b'class="model-error"', page)
        self.assertIn("Сохранённый маршрут недопустим".encode(), page)
        self.assertIn("codex/gpt-6.1-sol".encode(), page)
        saved_cell, effective_cell, _used_cell = self._model_cells(page)
        self.assertIn("codex/gpt-6.1-sol", saved_cell)
        self.assertIn("заблокирован", effective_cell)

    def test_dashboard_never_claims_an_unproven_choice_was_used(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        _saved_cell, _effective_cell, used_cell = self._model_cells(page)
        self.assertIn("Ещё не подтверждено запуском", used_cell)
        self.assertNotIn(HAND_ROUTE, used_cell)

    def test_dashboard_shows_the_last_receipt_model_next_to_the_saved_choice(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        self._record_job_receipt(SECOND_HAND_ROUTE)
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertNotIn("Ещё не подтверждено запуском".encode(), page)
        _saved_cell, _effective_cell, used_cell = self._model_cells(page)
        self.assertIn(SECOND_HAND_ROUTE, used_cell)
        self.assertNotIn(HAND_ROUTE, used_cell)

    def test_dashboard_ignores_receipts_of_unrelated_agent_jobs(self) -> None:
        self._record_unrelated_job_receipt("codex/gpt-6.1-sol")
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn("Ещё не подтверждено запуском".encode(), page)
        self.assertNotIn(b"codex/gpt-6.1-sol", page)

    def test_dashboard_copy_states_that_existing_jobs_are_not_replayed(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn("не переигрываются".encode(), page)
        self.assertIn("только к новым ".encode(), page)


class IncidentModelStoreUnitTests(unittest.TestCase):
    """Store-level checks that need no HTTP server."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.primary = root / "notification-center.env"
        self.routes = root / "routes.env"
        self.database = root / "notify-center.sqlite3"
        self.primary.write_text(f"NOTIFY_CENTER_DB={self.database}\n", encoding="utf-8")
        self.routes.write_text("TELEGRAM_SEVERITY_ROUTES_JSON={}\n", encoding="utf-8")
        self.store = AdminConfigStore(
            self.primary, self.routes, root / "state",
            restart=lambda: None, calls_override_path=root / "calls.conf",
            daemon_reload=lambda: None, health_config_path=root / "health.json",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_snapshot_exposes_the_bounded_model_state(self) -> None:
        snapshot = self.store.snapshot()
        model = snapshot["incident_model"]
        self.assertEqual(DEFAULT_ROUTE, model["value"])
        self.assertEqual(DEFAULT_ROUTE, model["default"])
        self.assertEqual([DEFAULT_ROUTE], [option["value"] for option in model["options"]])
        self.assertIsNone(model["last_used"])
        self.assertEqual(DEFAULT_ROUTE, snapshot["health"]["incident_model"]["value"])

    def test_runtime_settings_setter_does_not_touch_the_model_key(self) -> None:
        self.store.set_runtime_settings({}, "sso:operator")
        self.assertIsNone(self.store._consumer_notification_center().get_runtime_setting(INCIDENT_MODEL_SETTING_KEY))
        self.assertEqual(DEFAULT_ROUTE, self.store.incident_model_setting()["value"])

    def test_saved_value_is_stored_in_the_existing_runtime_settings_table(self) -> None:
        self.store.set_incident_model(HAND_ROUTE, "sso:operator")
        center = self.store._consumer_notification_center()
        self.assertEqual(HAND_ROUTE, center.get_runtime_setting(INCIDENT_MODEL_SETTING_KEY))
        self.assertEqual(HAND_ROUTE, read_incident_model(center))
        self.assertIsInstance(center, NotificationCenter)

    def test_config_card_stays_useful_without_any_incident(self) -> None:
        """The saved choice and the model form work on an empty store."""
        snapshot = self.store.snapshot()
        model = snapshot["incident_model"]
        self.assertEqual(DEFAULT_ROUTE, model["value"])
        self.assertIsNone(model["last_used"])
        self.assertEqual([], snapshot["health"]["incidents"])
        self.assertEqual(DEFAULT_ROUTE, snapshot["health"]["incident_model"]["value"])
        self.assertIsNone(snapshot["health"]["incident_model"]["last_used"])

    def test_effective_model_lookup_is_bounded_and_needs_no_audit_scan(self) -> None:
        """The effective model comes from per-incident session events only."""
        center = self.store._consumer_notification_center()
        self.assertEqual([], center.list_incidents())
        self.assertIsNone(self.store.last_used_incident_model(center.list_incidents()))
        self.assertEqual(200, MODEL_LOOKUP_INCIDENT_LIMIT)


if __name__ == "__main__":
    unittest.main()
