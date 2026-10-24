"""Contract tests for the SSO-gated NoticePlace operator console."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

from notification_center.admin import AdminConfigStore
from notification_center.admin_http import build_admin_handler
from notification_center.core import NotificationCenter, ValidationError
from notification_center.health_workflow import HealthWorkflow


class AdminConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.primary = root / "notification-center.env"
        self.routes = root / "routes.env"
        self.database = root / "notify-center.sqlite3"
        self.primary.write_text(f'NOTIFY_CENTER_DB={self.database}\nNOTIFY_CENTER_TOKENS_JSON={{"old-token":{{"project":"existing","max_severity":"notice"}}}}\nOTHER=unchanged\n', encoding="utf-8")
        self.routes.write_text("TELEGRAM_SEVERITY_ROUTES_JSON={}\n", encoding="utf-8")
        self.restarts = 0
        self.calls_override = root / "notification-center-calls.conf"
        self.health_config = root / "health-incident-fleet.json"
        self.health_config.write_text(json.dumps({"targets": [{
            "target": "local", "host_id": "roomhacker-server-100", "source_id": "host:roomhacker-server-100",
            "cpu_warn_percent": 85, "ram_warn_percent": 86, "disk_warn_percent": 90,
            "log_rules": [{"backend": "journalctl", "keywords": ["ERROR", "timeout"], "max_lines": 200}],
        }]}), encoding="utf-8")
        self.monitor_starts = 0
        self.store = AdminConfigStore(
            self.primary, self.routes, root / "state", restart=self._restart,
            calls_override_path=self.calls_override, daemon_reload=lambda: None,
            health_config_path=self.health_config, start_health_monitor=self._start_health_monitor,
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

    def _start_health_monitor(self) -> None:
        self.monitor_starts += 1

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

    def test_sso_header_gate_csrf_and_one_time_token_display(self) -> None:
        status, _ = self._request("GET", "/admin/", admin=False)
        self.assertEqual(403, status)
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b"adapter-steps", page)
        self.assertIn(b"add-adapter-step", page)
        self.assertNotIn(b"textarea name=\"policy_json\"", page)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

        status, rejected = self._request("POST", "/admin/projects", {"project": "service-a", "max_severity": "important", "csrf": "wrong"})
        self.assertEqual(400, status)
        self.assertIn(b"Configuration rejected", rejected)

        status, token_page = self._request("POST", "/admin/projects", {"project": "service-a", "max_severity": "important", "csrf": csrf})
        self.assertEqual(200, status)
        token = re.search(rb'<code>([^<]+)</code>', token_page).group(1).decode()
        self.assertGreater(len(token), 30)
        self.assertIn(b"curl", token_page)
        self.assertIn(b"EnvironmentFile", token_page)
        self.assertIn(b"notify_center_client", token_page)
        self.assertEqual(1, self.restarts)

        snapshot = self.store.snapshot()
        created = next(item for item in snapshot["projects"] if item["project"] == "service-a")
        self.assertEqual("important", created["max_severity"])
        self.assertNotIn(token.encode(), json.dumps(snapshot).encode())
        self.assertNotIn(token, self.store.audit_path.read_text(encoding="utf-8"))

    def test_project_edit_preserves_allowlisted_agent_jobs(self) -> None:
        self.primary.write_text(
            f'NOTIFY_CENTER_DB={self.database}\n'
            'NOTIFY_CENTER_TOKENS_JSON={"old-token":{"project":"existing","max_severity":"notice","agent_jobs":["health-diagnosis"]}}\n'
            'OTHER=unchanged\n',
            encoding="utf-8",
        )
        self.store.set_project_severity("existing", "critical", "test")
        env_line = next(
            line for line in self.primary.read_text(encoding="utf-8").splitlines()
            if line.startswith("NOTIFY_CENTER_TOKENS_JSON=")
        )
        scopes = json.loads(env_line.split("=", 1)[1])
        self.assertEqual(["health-diagnosis"], scopes["old-token"]["agent_jobs"])
        self.assertEqual("critical", scopes["old-token"]["max_severity"])

    def test_health_dashboard_is_first_and_projects_live_workflow_state(self) -> None:
        center = self.store._consumer_notification_center()
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal("old-token", "dashboard-health", {
            "project": "existing", "recipient": "me", "severity": "notice",
            "title": "Disk pressure", "body": "Disk is high", "dedup_key": "health:dashboard:disk",
            "source_id": "host:roomhacker-server-100", "host_id": "roomhacker-server-100", "signal_type": "disk",
        })
        workflow.attach_plans(created["incident_id"], "dashboard-plans", [
            {"plan_id": "observe", "title": "Observe", "summary": "Observe", "step": "observe"},
            {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
            {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
        ])
        workflow.select_plan(created["incident_id"], "dashboard-select", "repair", "operator")
        workflow.record_progress(created["incident_id"], "dashboard-progress", plan_id="repair", step="restart worker", evidence_refs=["unit:worker"], progress_fingerprint="progress-1", actor="worker")
        workflow.record_verification(created["incident_id"], "dashboard-verification", source_id="host:independent", verification_id="verify-1", observed_state="healthy", evidence_refs=["probe:https"], actor="verifier")

        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertLess(page.index(b'id="health-dashboard"'), page.index(b"Add producer"))
        for expected in (b"roomhacker-server-100", b"Disk pressure", b"Repair", b"restart worker", b"healthy", b"ERROR", b"Monitor config loaded"):
            self.assertIn(expected, page)

    def test_health_dashboard_offers_the_bounded_incident_model_choice(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b'id="health-model"', page)
        self.assertIn(b'<input type="text" name="model"', page)
        self.assertIn(b'<datalist id="incident-model-routes">', page)
        self.assertIn(b"omniroute/minimax-combo-m3.1", page)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

        status, page = self._request("POST", "/admin/health-model", {"csrf": csrf, "model": "minimax-coding-plan/MiniMax-M3.1-Flash"})
        self.assertEqual(400, status)
        self.assertIn(b"Configuration rejected", page)
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b"omniroute/minimax-combo-m3.1", page)

    def test_health_settings_require_csrf_save_atomically_and_optionally_start_monitor(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
        original_inode = self.health_config.stat().st_ino
        update = json.dumps({"targets": [{
            "target": "local", "host_id": "roomhacker-server-100", "source_id": "host:roomhacker-server-100",
            "cpu_warn_percent": 75, "ram_warn_percent": 76, "disk_warn_percent": 80,
            "log_rules": [{"backend": "journalctl", "keywords": ["PANIC", "timeout"], "max_lines": 150}],
        }]})
        status, _ = self._request("POST", "/admin/health-settings", {"csrf": "wrong", "config_json": update})
        self.assertEqual(400, status)
        self.assertEqual(85, json.loads(self.health_config.read_text())["targets"][0]["cpu_warn_percent"])

        status, _ = self._request("POST", "/admin/health-settings", {"csrf": csrf, "config_json": update, "run_monitor": "true"})
        self.assertEqual(200, status)
        saved = json.loads(self.health_config.read_text())
        self.assertEqual(75.0, saved["targets"][0]["cpu_warn_percent"])
        self.assertEqual(["PANIC", "timeout"], saved["targets"][0]["log_rules"][0]["keywords"])
        self.assertNotEqual(original_inode, self.health_config.stat().st_ino)
        self.assertEqual(1, self.monitor_starts)

    def test_health_settings_reject_unbounded_schema_without_mutation(self) -> None:
        before = self.health_config.read_bytes()
        with self.assertRaisesRegex(Exception, "threshold"):
            self.store.save_health_config({"targets": [{"target": "local", "cpu_warn_percent": 101, "log_rules": []}]}, "sso:operator")
        self.assertEqual(before, self.health_config.read_bytes())
        with self.assertRaisesRegex(Exception, "keywords"):
            self.store.save_health_config({"targets": [{"target": "local", "log_rules": [{"backend": "journalctl", "keywords": ["x"] * 33}]}]}, "sso:operator")
        self.assertEqual(before, self.health_config.read_bytes())

    def test_routes_are_validated_and_persisted_with_a_restart(self) -> None:
        self.store.set_routes({"critical": {"chat_id": "-100123", "message_thread_id": 42}}, "sso:operator")
        self.assertEqual(1, self.restarts)
        self.assertEqual({"chat_id": "-100123", "message_thread_id": 42}, self.store.snapshot()["routes"]["critical"])

    def test_operator_can_toggle_automatic_call_escalation(self) -> None:
        self.assertTrue(self.store.snapshot()["automatic_calls_enabled"])
        self.store.set_automatic_calls(False, "sso:operator")
        self.assertFalse(self.store.snapshot()["automatic_calls_enabled"])
        self.assertFalse(self.calls_override.exists())
        self.store.set_automatic_calls(True, "sso:operator")
        self.assertTrue(self.store.snapshot()["automatic_calls_enabled"])
        self.assertFalse(self.calls_override.exists())
        self.assertEqual(0, self.restarts)

    def test_operator_can_run_central_adapter_tests(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
        with mock.patch.object(self.store, "test_adapter", return_value={"ok": True, "channel": "phone"}) as test_adapter:
            status, result = self._request("POST", "/admin/test-adapter", {
                "csrf": csrf, "adapter": "phone", "message": "test call",
            })
        self.assertEqual(200, status)
        self.assertIn(b"Adapter test accepted", result)
        test_adapter.assert_called_once_with("phone", "test call", "sso:operator")

    def test_resolved_telegram_cleanup_is_explicit_project_opt_in_without_restart(self) -> None:
        status, page = self._request('GET', '/admin/')
        self.assertEqual(200, status)
        self.assertIn('Удаление закрытых Telegram-карточек'.encode(), page)
        self.assertFalse(self.store.snapshot()['projects'][0]['delete_resolved_telegram'])
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
        status, _ = self._request('POST', '/admin/telegram-cleanup',
                                  {'project':'existing', 'enabled':'true', 'csrf':'wrong'})
        self.assertEqual(400, status)
        for project, enabled in [('unknown','true'), ('existing','invalid')]:
            self.assertEqual(400, self._request('POST', '/admin/telegram-cleanup',
                {'project':project,'enabled':enabled,'csrf':csrf})[0])
        for enabled in ('true', 'false'):
            status, _ = self._request('POST', '/admin/telegram-cleanup',
                {'project':'existing','enabled':enabled,'csrf':csrf})
            self.assertEqual(200, status)
            self.assertEqual(enabled == 'true', self.store.snapshot()['projects'][0]['delete_resolved_telegram'])
        self.assertEqual(0, self.restarts)

    def test_call_problem_interval_defaults_to_hour_and_rejects_nonfinite(self) -> None:
        values = self.store.runtime_settings()
        self.assertEqual('3600', values['call_repeat_min_interval_seconds'])
        for value in ('nan', 'inf', '-1'):
            with self.assertRaises(ValidationError):
                self.store.set_runtime_settings({**values, 'call_repeat_min_interval_seconds': value}, 'sso:operator')
        self.assertEqual('3600', self.store.runtime_settings()['call_repeat_min_interval_seconds'])

    def test_cleanup_includes_live_projects_outside_legacy_token_file(self) -> None:
        center=NotificationCenter(self.database, {'dns-token':{'project':'dns-live','max_severity':'notice'}},
                                  default_quiet_hours=[])
        try:
            center.create_event('dns-token','dns-live-source',{'schema':'notify.event.v1','kind':'incident',
                'severity':'notice','project':'dns-live','recipient':'me','dedup_key':'dns',
                'title':'DNS','body':'Проверка'})
            status,page=self._request('GET','/admin/')
            self.assertEqual(200,status)
            self.assertIn(b'name="project" value="dns-live"',page)
            csrf=re.search(rb'name="csrf" value="([^"]+)"',page).group(1).decode()
            self.assertEqual(200,self._request('POST','/admin/telegram-cleanup',
                {'project':'dns-live','enabled':'true','csrf':csrf})[0])
            choices={p['project']:p['delete_resolved_telegram'] for p in self.store.telegram_cleanup_projects()}
            self.assertTrue(choices['dns-live']);self.assertFalse(choices['existing'])
        finally:center._connection.close()

    def test_settings_save_preserves_effective_dropin_defaults_and_database_overrides(self) -> None:
        center = self.store._consumer_notification_center()
        center.set_runtime_setting('android_phone_quiet_end_hour', '12')
        with mock.patch.object(self.store, '_live_delivery_defaults', return_value={
            'MATRIX_CALL_CRITICAL_ESCALATION_SECONDS': '3600',
            'MATRIX_CALL_EMERGENCY_ESCALATION_SECONDS': '600',
            'TELEGRAM_CRITICAL_REPEAT_SECONDS': '600',
            'ANDROID_PHONE_QUIET_END_HOUR': '0',
        }):
            values = self.store.runtime_settings()
            self.assertEqual('3600', values['matrix_call_critical_escalation_seconds'])
            self.assertEqual('600', values['matrix_call_emergency_escalation_seconds'])
            self.assertEqual('600', values['telegram_critical_repeat_seconds'])
            self.assertEqual('12', values['android_phone_quiet_end_hour'])
            self.store.set_runtime_settings(values, 'sso:operator')
        self.assertEqual(values, self.store.runtime_settings())
        self.assertEqual(0, self.restarts)

    def test_operator_can_change_live_delivery_timers_without_restart(self) -> None:
        values = {
            "call_repeat_min_interval_seconds": "3600",
            "matrix_call_critical_escalation_seconds": "91",
            "matrix_call_emergency_escalation_seconds": "31",
            "android_phone_call_escalation_seconds": "601",
            "android_phone_emergency_call_escalation_seconds": "0",
            "android_phone_quiet_start_hour": "0",
            "android_phone_quiet_end_hour": "12",
            "android_telegram_call_escalation_seconds": "41",
            "telegram_critical_repeat_seconds": "121",
        }
        self.store.set_runtime_settings(values, "sso:operator")
        actual=self.store.snapshot()['runtime_settings']
        self.assertEqual(values,{key:actual[key] for key in values})
        self.assertEqual(0, self.restarts)

    def test_card_policy_defaults_are_visible_live_and_validate_edit_floor(self) -> None:
        """Focused integration: operator form and shared DB, <1s / maximum3s."""
        values=self.store.runtime_settings()
        self.assertEqual('15',values['telegram_card_edit_interval_seconds'])
        self.assertEqual('1',values['telegram_critical_sound_enabled'])
        self.assertEqual('0',values['telegram_routine_sound_enabled'])
        code,body=self._request('GET','/')
        self.assertEqual(200,code)
        self.assertIn('Объединять похожие уведомления',body.decode())
        self.assertIn('name="telegram_native_progress_enabled"',body.decode())
        with self.assertRaises(ValidationError):
            self.store.set_runtime_settings({'telegram_card_edit_interval_seconds':'14'},'operator')
        with self.assertRaises(ValidationError):
            self.store.set_runtime_settings({'telegram_routine_sound_enabled':'2'},'operator')
        self.store.set_runtime_settings({'telegram_card_edit_interval_seconds':'30','telegram_routine_sound_enabled':'1'},'operator')
        self.assertEqual('30',self.store.runtime_settings()['telegram_card_edit_interval_seconds'])
        self.assertEqual(0,self.restarts)

    def test_preset_and_custom_topics_share_the_same_live_editor(self) -> None:
        self.store._consumer_notification_center().set_runtime_setting("telegram_topics_json", json.dumps({
            "emergency": {"name": "Emergency", "chat_id": "-1001", "message_thread_id": 7, "enabled": True},
        }))
        updated = self.store.save_topic("emergency", "Critical emergency", "-1001", "8", True, "sso:operator")
        created = self.store.save_topic("new-topic", "Deployments", "-1001", "9", True, "sso:operator")
        self.assertEqual("Critical emergency", next(item for item in self.store.topics() if item["id"] == "emergency")["name"])
        self.assertEqual("deployments", created["id"])
        self.store.delete_topic("emergency", "sso:operator")
        self.assertNotIn("emergency", {item["id"] for item in self.store.topics()})
        self.assertEqual(0, self.restarts)

    def test_consumer_form_reveals_intake_url_and_token_once(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()

        status, token_page = self._request("POST", "/admin/consumers", {
            "csrf": csrf,
            "project": "hermes",
            "name": "Gateway producer",
            "max_severity": "critical",
            "chat_id": "-100123",
            "topic_id": "42",
            "matrix_delay_seconds": "120",
            "phone_delay_seconds": "600",
        })
        self.assertEqual(200, status)
        token = re.search(rb'<code>(nct_[^<]+)</code>', token_page).group(1).decode()
        self.assertIn(b"https://notify.bezrabotnyi.com/v1/events", token_page)
        self.assertIn(token.encode(), token_page)

        snapshot = self.store.snapshot()
        consumer = snapshot["consumers"][0]
        self.assertEqual("Gateway producer", consumer["name"])
        self.assertEqual(["telegram", "matrix", "phone"], [stage["kind"] for stage in consumer["policy"] if stage["enabled"]])
        self.assertIn(b"Matrix", page)
        self.assertNotIn(token, json.dumps(snapshot))
        reopened = NotificationCenter(self.database, {"old-token": {"project": "existing", "max_severity": "notice"}})
        self.assertNotIn("intake_token", reopened.get_consumer(consumer["id"]))

    def test_history_page_shows_provenance_children_notifications_and_outcome(self) -> None:
        center = self.store._consumer_notification_center()
        parent = center.create_event(
            "old-token",
            "admin-history-parent",
            {
                "schema": "notify.event.v1", "project": "existing", "recipient": "me",
                "kind": "incident", "severity": "notice", "title": "Site down",
                "dedup_key": "site:down", "event_type": "site.down", "producer": "monitor",
                "plugin": "lol-nginx", "correlation_id": "admin-history-1",
            },
            request_meta={"peer_ip": "127.0.0.1", "source_ip": "192.0.2.44", "proxy_ip": "127.0.0.1"},
        )
        center.complete_delivery(parent["initial_delivery_id"], "sent")
        child = center.create_event(
            "old-token",
            "admin-history-child",
            {
                "schema": "notify.event.v1", "project": "existing", "recipient": "me",
                "kind": "incident", "severity": "notice", "title": "Service stopped",
                "dedup_key": "site:service-stopped", "event_type": "service.stopped",
                "producer": "monitor", "plugin": "systemd", "correlation_id": "admin-history-1",
                "parent_incident_id": parent["incident_id"], "parent_event_id": parent["event_id"],
            },
        )
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        self.assertIn(b"Event history", page)
        self.assertIn(b"site.down", page)
        self.assertIn(b"192.0.2.44", page)
        self.assertIn(b"lol-nginx", page)
        self.assertIn(b"service.stopped", page)
        self.assertIn(f'/admin/?history={child["incident_id"]}#event-history'.encode(), page)
        self.assertIn(b"sent", page)
        self.assertNotIn(b"old-token", page)

    def test_health_history_shows_all_three_plan_names(self) -> None:
        center = self.store._consumer_notification_center()
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "old-token",
            "admin-health-intake",
            {
                "project": "existing",
                "recipient": "me",
                "severity": "notice",
                "title": "Health degradation",
                "body": "Disk pressure",
                "dedup_key": "health:admin:disk",
                "source_id": "node-admin",
                "host_id": "node-admin",
                "signal_type": "disk",
            },
        )
        workflow.attach_plans(
            created["incident_id"],
            "admin-health-plans",
            [
                {"plan_id": "observe", "title": "Observe", "summary": "Observe", "step": "observe"},
                {"plan_id": "repair", "title": "Repair", "summary": "Repair", "step": "repair"},
                {"plan_id": "verify", "title": "Verify", "summary": "Verify", "step": "verify"},
            ],
            actor="omniroute",
            correlation_id="corr:admin-health",
        )
        workflow.attach_plans(
            created["incident_id"],
            "admin-health-plans-latest",
            [
                {"plan_id": "observe", "title": "Observe latest", "summary": "Observe", "step": "observe"},
                {"plan_id": "repair", "title": "Repair latest", "summary": "Repair", "step": "repair"},
                {"plan_id": "verify", "title": "Verify latest", "summary": "Verify", "step": "verify"},
            ],
            actor="omniroute",
            correlation_id="corr:admin-health-latest",
        )
        status, page = self._request("GET", f"/admin/?history={created['incident_id']}")
        self.assertEqual(200, status)
        self.assertIn(b"Health plans (3)", page)
        for plan in (b"observe: Observe latest", b"repair: Repair latest", b"verify: Verify latest"):
            self.assertIn(plan, page)

    def test_health_history_does_not_claim_three_plans_for_incomplete_audit(self) -> None:
        center = self.store._consumer_notification_center()
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "old-token",
            "admin-health-incomplete",
            {
                "project": "existing",
                "recipient": "me",
                "severity": "notice",
                "title": "Health degradation",
                "body": "Disk pressure",
                "dedup_key": "health:admin:incomplete",
                "source_id": "node-admin",
                "host_id": "node-admin",
                "signal_type": "disk",
            },
        )
        center.record_health_update(
            created["incident_id"],
            "admin-health-incomplete-plans",
            "health.plans_attached",
            {"plans": [{"plan_id": "only", "title": "Only"}]},
            actor="legacy",
        )

        status, page = self._request("GET", f"/admin/?history={created['incident_id']}")
        self.assertEqual(200, status)
        self.assertNotIn(b"Health plans (3)", page)
        self.assertNotIn(b"only: Only", page)

    def test_health_history_rejects_duplicate_and_extra_plan_entries(self) -> None:
        center = self.store._consumer_notification_center()
        workflow = HealthWorkflow(center, callback_secret="x" * 32)
        created = workflow.intake_signal(
            "old-token",
            "admin-health-malformed",
            {
                "project": "existing",
                "recipient": "me",
                "severity": "notice",
                "title": "Health degradation",
                "body": "Disk pressure",
                "dedup_key": "health:admin:malformed",
                "source_id": "node-admin",
                "host_id": "node-admin",
                "signal_type": "disk",
            },
        )
        cases = (
            (
                "duplicate",
                [
                    {"plan_id": "observe", "title": "Observe"},
                    {"plan_id": "observe", "title": "Observe again"},
                    {"plan_id": "verify", "title": "Verify"},
                ],
            ),
            (
                "extra",
                [
                    {"plan_id": "observe", "title": "Observe"},
                    {"plan_id": "repair", "title": "Repair"},
                    {"plan_id": "verify", "title": "Verify"},
                    {"plan_id": "extra", "title": "Extra"},
                ],
            ),
        )
        for suffix, plans in cases:
            center.record_health_update(
                created["incident_id"],
                f"admin-health-malformed-{suffix}",
                "health.plans_attached",
                {"plans": plans},
                actor="legacy",
            )
            status, page = self._request("GET", f"/admin/?history={created['incident_id']}")
            self.assertEqual(200, status)
            self.assertNotIn(b"Health plans (3)", page)

    def test_generic_consumer_builder_does_not_require_legacy_fields(self) -> None:
        status, page = self._request("GET", "/admin/")
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
        policy = json.dumps([{
            "id": "step-1", "platform": "telegram", "action": "message",
            "target": {"chat_id": -100123}, "retry_interval_seconds": 60,
            "max_repeats": 1, "previous_step_id": None,
        }])
        status, token_page = self._request("POST", "/admin/consumers", {
            "csrf": csrf, "project": "existing", "name": "Generic builder",
            "max_severity": "notice", "policy_json": policy,
            "operator_note": "browser QA",
        })
        self.assertEqual(200, status)
        self.assertIn(b"Copy this intake URL", token_page)


    def test_consumer_policy_update_keeps_identity_and_replaces_stages(self) -> None:
        """The update keeps the consumer identity and only replaces its stages."""
        created = self.store.create_consumer(
            project="hermes", name="Scoped route", chat_id="", topic_id="",
            matrix_delay_seconds="", phone_delay_seconds="45",
            max_severity="notice", actor="sso:operator",
            policy_json=json.dumps([{
                "id": "step-1", "platform": "matrix", "action": "message",
                "target": {"room_id": "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"},
                "retry_interval_seconds": 60, "max_repeats": 1,
            }]),
        )
        consumer_id = created["id"]
        intake_token = created["intake_token"]

        updated = self.store.update_consumer_policy(
            consumer_id,
            json.dumps([{
                "id": "step-1", "platform": "matrix", "action": "message",
                "target": {
                    "room_id": "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com",
                    "senders": ["@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"],
                    "target": "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com",
                },
                "retry_interval_seconds": 60, "max_repeats": 1,
            }]),
            "sso:operator",
        )
        self.assertEqual(consumer_id, updated["id"])
        self.assertEqual(created["token_fingerprint"], updated["token_fingerprint"])
        stages = updated["policy"]
        self.assertEqual(1, len(stages))
        self.assertEqual("matrix", stages[0]["platform"])
        self.assertEqual("message", stages[0]["action"])
        self.assertIn(
            "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com",
            stages[0]["senders"],
        )
        self.assertEqual("!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com", stages[0]["room_id"])

        # The intake token of the consumer still works: no new grant, no new consumer.
        center = NotificationCenter(self.database, {"old-token": {"project": "hermes", "max_severity": "notice"}})
        incident = center.create_event(intake_token, "policy-update", {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "operator",
            "kind": "incident", "severity": "notice", "title": "t", "dedup_key": "policy-update",
        })
        self.assertEqual("open", center.get_incident(incident["incident_id"])["state"])
        self.assertEqual(
            1,
            len([row for row in self.store.snapshot().get("consumers", []) if row.get("id") == consumer_id]),
        )

    def test_consumer_policy_update_refuses_builtin_profiles_and_invalid_policy(self) -> None:
        """A builtin profile and an invalid policy are both refused."""
        with self.assertRaises(ValidationError):
            self.store.update_consumer_policy("profile_critical", "[]", "sso:operator")
        with self.assertRaises(ValidationError):
            self.store.update_consumer_policy("consumer_missing", "[]", "sso:operator")
        created = self.store.create_consumer(
            project="hermes", name="Scoped route", chat_id="", topic_id="",
            matrix_delay_seconds="", phone_delay_seconds="45",
            max_severity="notice", actor="sso:operator",
            policy_json=json.dumps([{
                "id": "step-1", "platform": "matrix", "action": "message",
                "target": {"room_id": "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"},
                "retry_interval_seconds": 60, "max_repeats": 1,
            }]),
        )
        with self.assertRaises(ValidationError):
            self.store.update_consumer_policy(created["id"], "{not json", "sso:operator")
        with self.assertRaises(ValidationError):
            self.store.update_consumer_policy(created["id"], json.dumps([{"platform": "phone", "action": "call"}]), "sso:operator")

if __name__ == "__main__":
    unittest.main()
