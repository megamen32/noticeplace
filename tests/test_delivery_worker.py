"""Delivery adapter tests, including the MatrixRTC ACK boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, MatrixCallSender, TelegramSender


class DeliveryWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "emergency"}}, default_quiet_hours=[])
        self.event = {
            "schema": "notify.event.v1", "project": "hermes", "recipient": "me", "kind": "incident",
            "severity": "critical", "title": "Gateway unavailable", "body": "three checks failed", "dedup_key": "gateway:100",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_inactive_standard_mode_is_cancelled_without_telegram_send(self) -> None:
        created = self.center.create_event("producer", "inactive-critical", {**self.event, "severity": "debug"})

        class Telegram:
            active_modes = {"important"}

            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("inactive mode must not reach Telegram")

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        status = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"]
        self.assertEqual("cancelled", status)

    def test_health_delivery_waits_for_a_topic_instead_of_being_cancelled(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-topic-missing",
            {**self.event, "event_type": "health.degraded"},
        )
        self.assertIsNone(created["initial_delivery_id"])
        legacy_delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        telegram = TelegramSender("bot-token", "-100123", active_modes={"log"}, center=self.center)
        worker = DeliveryWorker(self.center, telegram)

        self.assertEqual(1, worker.run_once())
        row = self.center._connection.execute(
            "SELECT status, last_error, due_at FROM deliveries WHERE id = ?",
            (legacy_delivery_id,),
        ).fetchone()
        self.assertEqual("queued", row["status"])
        self.assertEqual("Telegram health topic route is not active", row["last_error"])
        self.assertGreater(row["due_at"], 0)

    def test_health_delivery_waits_for_exactly_three_plans_before_sending(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-plans-missing",
            {**self.event, "event_type": "health.degraded"},
        )
        self.assertIsNone(created["initial_delivery_id"])
        legacy_delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram())

        self.assertEqual(1, worker.run_once())
        row = self.center._connection.execute(
            "SELECT status, last_error FROM deliveries WHERE id = ?",
            (legacy_delivery_id,),
        ).fetchone()
        self.assertEqual("queued", row["status"])
        self.assertEqual("Health plans are not attached", row["last_error"])
        self.assertEqual([], sent)

    def test_health_degraded_remediation_outcome_sends_without_plan_card(self) -> None:
        created = self.center.create_event(
            "producer", "health-remediation-outcome",
            {**self.event, "event_type": "health.degraded"},
        )
        delivery_id = self.center._schedule_delivery(
            created["incident_id"], "telegram.main", "health.remediation_degraded:repair:fp-1", 0,
            {"health_outcome": {"plan_id": "repair", "observed_state": "degraded", "step": "validate"}},
        )
        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}
            def send(self, payload: dict[str, object]) -> dict[str, object]:
                sent.append(payload)
                return {"message_id": 22, "chat_id": "-1001"}

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        row = self.center._connection.execute("SELECT status FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        self.assertEqual("sent", row["status"])
        self.assertEqual("degraded", sent[0]["health_outcome"]["observed_state"])

    def test_attaching_plans_releases_one_plan_card_from_the_existing_slot(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-plans-attached",
            {**self.event, "event_type": "health.degraded"},
        )
        self.assertIsNone(created["initial_delivery_id"])
        legacy_delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        plans = [
            {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the selected fix"},
            {"plan_id": "verify", "title": "Verify", "summary": "Check the original signal"},
        ]
        self.center.record_health_update(
            created["incident_id"],
            "health-plans-attached-1",
            "health.plans_attached",
            {"plans": plans},
            actor="omniroute",
        )
        initial = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (legacy_delivery_id,)
        ).fetchone()
        self.assertEqual("queued", initial["status"])

        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram(), critical_repeat_seconds=600)
        self.assertEqual(1, worker.run_once())
        self.assertEqual(1, len(sent))
        self.assertEqual(3, len(sent[0]["health_plans"]))
        remaining = self.center._connection.execute(
            "SELECT delivery_key, status FROM deliveries WHERE incident_id = ? AND status = 'queued'",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], remaining)

    def test_attaching_plans_revives_cancelled_plan_slot_after_mode_activation(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-plans-revive-cancelled-slot",
            {**self.event, "event_type": "health.degraded"},
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "health.plans", 0)
        self.center._connection.execute(
            "UPDATE deliveries SET status = 'cancelled', last_error = 'Telegram mode was inactive' WHERE id = ?",
            (delivery_id,),
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-plans-revive-cancelled-slot-1",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the selected fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the original signal"},
            ]},
            actor="omniroute",
        )
        row = self.center._connection.execute(
            "SELECT status, last_error FROM deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        self.assertEqual("queued", row["status"])
        self.assertIsNone(row["last_error"])

    def test_claimed_initial_health_delivery_is_not_sent_after_plans_supersede_it(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-claimed-race",
            {**self.event, "event_type": "health.degraded"},
        )
        self.assertIsNone(created["initial_delivery_id"])
        legacy_delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([legacy_delivery_id], [item["id"] for item in claimed])
        plans = [
            {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the selected fix"},
            {"plan_id": "verify", "title": "Verify", "summary": "Check the original signal"},
        ]
        self.center.record_health_update(
            created["incident_id"],
            "health-claimed-race-plans",
            "health.plans_attached",
            {"plans": plans},
            actor="omniroute",
        )
        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram())
        worker.deliver(claimed[0])
        for delivery in self.center.claim_due_deliveries(now_epoch=10**12):
            worker.deliver(delivery)
        self.assertEqual(1, len(sent))

    def test_synthetic_health_canary_does_not_create_a_user_facing_card(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-synthetic-canary",
            {**self.event, "event_type": "health.degraded", "correlation_id": "corr:live-health-canary:test"},
        )
        plans = [
            {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the selected fix"},
            {"plan_id": "verify", "title": "Verify", "summary": "Check the original signal"},
        ]
        self.center.record_health_update(
            created["incident_id"],
            "health-synthetic-canary-plans",
            "health.plans_attached",
            {"plans": plans, "correlation_id": "health:real-looking-callback"},
            actor="omniroute",
        )
        rows = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'telegram.main'",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], rows)

    def test_health_plan_gate_also_covers_custom_telegram_consumers(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-consumer-gate",
            {**self.event, "event_type": "health.degraded"},
        )
        delivery_id = self.center._schedule_delivery(
            created["incident_id"],
            "telegram.consumer:custom",
            "initial",
            0,
        )
        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        row = self.center._connection.execute(
            "SELECT status, last_error FROM deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        self.assertEqual("queued", row["status"])
        self.assertEqual("Health plans are not attached", row["last_error"])
        self.assertEqual([], sent)

    def test_sent_legacy_initial_does_not_schedule_a_second_health_card(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-sent-legacy",
            {**self.event, "event_type": "health.degraded"},
        )
        legacy_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([legacy_id], [item["id"] for item in claimed])
        self.center.complete_delivery(legacy_id, "sent")
        self.center.record_health_update(
            created["incident_id"],
            "health-sent-legacy-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        rows = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'telegram.main' AND delivery_key LIKE ?",
            (created["incident_id"], f"{created['incident_id']}:%:health.plans"),
        ).fetchall()
        self.assertEqual([], rows)
        self.assertIsNotNone(self.center._connection.execute(
            "SELECT 1 FROM audit_events WHERE incident_id = ? AND type = 'health.legacy_card_migration_required'",
            (created["incident_id"],),
        ).fetchone())
        self.assertEqual("degraded", self.center.health()["status"])

    def test_compliant_sent_health_card_is_retained_without_migration_gate(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-sent-compliant",
            {**self.event, "event_type": "health.degraded"},
        )
        legacy_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([legacy_id], [item["id"] for item in claimed])
        self.center.complete_delivery(
            legacy_id,
            "sent",
            result={
                "message_id": 77,
                "chat_id": "-100123",
                "health_plan_ids": ["observe", "repair", "verify"],
                "health_button_count": 3,
                "health_signed_callback_count": 3,
            },
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-sent-compliant-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        self.assertIsNone(self.center._connection.execute(
            "SELECT 1 FROM audit_events WHERE incident_id = ? AND type = 'health.legacy_card_migration_required'",
            (created["incident_id"],),
        ).fetchone())

    def test_legacy_sent_card_with_message_identity_is_edited_in_place(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-legacy-edit",
            {**self.event, "event_type": "health.degraded"},
        )
        source_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.complete_delivery(source_id, "sent", result={"message_id": 88, "chat_id": "-100123"})
        self.center.record_health_update(
            created["incident_id"],
            "health-legacy-edit-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        edit = self.center._connection.execute(
            "SELECT id, target_json, status FROM deliveries WHERE incident_id = ? AND channel = 'telegram.edit'",
            (created["incident_id"],),
        ).fetchone()
        self.assertIsNotNone(edit)
        self.assertEqual("queued", edit["status"])
        self.assertIn('"source_delivery_id": "' + source_id + '"', edit["target_json"])
        self.assertEqual([source_id], [item["id"] for item in claimed])

        edited: list[tuple[int, str]] = []

        class Telegram:
            def edit_health_card(self, _payload: dict[str, object], message_id: int, chat_id: str) -> dict[str, object]:
                edited.append((message_id, chat_id))
                return {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "health_plan_ids": ["observe", "repair", "verify"],
                    "health_button_count": 3,
                    "health_signed_callback_count": 3,
                    "edited_in_place": True,
                }

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        self.assertEqual([(88, "-100123")], edited)
        source_result = self.center._connection.execute(
            "SELECT result_json FROM deliveries WHERE id = ?", (source_id,)
        ).fetchone()["result_json"]
        self.assertIn('"edited_in_place": true', source_result)
        self.assertEqual("superseded", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (edit["id"],)
        ).fetchone()["status"])

    def test_repeated_plan_attachment_reuses_queued_legacy_edit(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-legacy-edit-idempotent",
            {**self.event, "event_type": "health.degraded"},
        )
        source_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.complete_delivery(source_id, "sent", result={"message_id": 89, "chat_id": "-100123"})
        plans = {"plans": [
            {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
            {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
            {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
        ]}
        self.center.record_health_update(created["incident_id"], "health-legacy-edit-idempotent-1", "health.plans_attached", plans, actor="omniroute")
        first_edit = self.center._connection.execute(
            "SELECT id, status FROM deliveries WHERE incident_id = ? AND channel = 'telegram.edit'",
            (created["incident_id"],),
        ).fetchone()
        self.center.record_health_update(created["incident_id"], "health-legacy-edit-idempotent-2", "health.plans_attached", plans, actor="omniroute")
        second_edit = self.center._connection.execute(
            "SELECT id, status FROM deliveries WHERE incident_id = ? AND channel = 'telegram.edit'",
            (created["incident_id"],),
        ).fetchone()
        self.assertEqual((first_edit["id"], "queued"), (second_edit["id"], second_edit["status"]))

    def test_successor_persistence_failure_does_not_requeue_sent_delivery(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Successor persistence failure",
            policy=[
                {"id": "matrix-root-failure", "platform": "matrix", "action": "call", "target": {"room_id": "!ops:example.org"}, "retry_interval_seconds": 30, "max_repeats": 1},
                {"id": "phone-successor-failure", "platform": "phone", "action": "call", "target": {"device": "operator"}, "retry_interval_seconds": 30, "max_repeats": 1, "previous_step_id": "matrix-root-failure"},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "successor-persistence-failure", self.event)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        original_schedule = self.center._schedule_delivery

        def fail_schedule(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("successor persistence failed")

        self.center._schedule_delivery = fail_schedule
        try:
            self.center.complete_delivery(claimed[0]["id"], "sent")
        finally:
            self.center._schedule_delivery = original_schedule
        self.assertEqual("sent", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (claimed[0]["id"],)
        ).fetchone()["status"])

    def test_sent_legacy_row_quarantines_an_existing_queued_plan_row(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-sent-legacy-with-plan-row",
            {**self.event, "event_type": "health.degraded"},
        )
        legacy_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([legacy_id], [item["id"] for item in claimed])
        self.center.complete_delivery(legacy_id, "sent")
        queued_plan_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "health.plans", 0)
        self.center.record_health_update(
            created["incident_id"],
            "health-sent-legacy-with-plan-row-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        status = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (queued_plan_id,)
        ).fetchone()["status"]
        self.assertEqual("cancelled", status)

    def test_claimed_initial_coalesces_with_an_existing_queued_plan_row(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-claimed-with-plan-row",
            {**self.event, "event_type": "health.degraded"},
        )
        initial_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([initial_id], [item["id"] for item in claimed])
        plan_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "health.plans", 0)
        self.center.record_health_update(
            created["incident_id"],
            "health-claimed-with-plan-row-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        self.assertEqual("cancelled", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (initial_id,)
        ).fetchone()["status"])
        self.assertEqual("queued", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (plan_id,)
        ).fetchone()["status"])

    def test_sent_main_coalesces_with_a_claimed_custom_telegram_row(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-sent-with-custom-row",
            {**self.event, "event_type": "health.degraded"},
        )
        main_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        main_claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([main_id], [item["id"] for item in main_claimed])
        self.center.complete_delivery(main_id, "sent")
        custom_id = self.center._schedule_delivery(created["incident_id"], "telegram.consumer:custom", "initial", 0)
        custom_claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([custom_id], [item["id"] for item in custom_claimed])
        self.center.record_health_update(
            created["incident_id"],
            "health-sent-with-custom-row-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        self.assertEqual("cancelled", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (custom_id,)
        ).fetchone()["status"])

    def test_generic_telegram_root_coalesces_with_health_plan_delivery(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-generic-telegram-root",
            {**self.event, "event_type": "health.degraded"},
        )
        generic_id = self.center._schedule_delivery(created["incident_id"], "telegram.message", "step:custom:repeat:1", 0)
        self.center.record_health_update(
            created["incident_id"],
            "health-generic-telegram-root-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        self.assertEqual("queued", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (generic_id,)
        ).fetchone()["status"])
        self.assertEqual([], self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'telegram.main'",
            (created["incident_id"],),
        ).fetchall())

    def test_stale_lease_generation_cannot_send_after_reclaim(self) -> None:
        created = self.center.create_event("producer", "stale-lease-generation", self.event)
        first = self.center.claim_due_deliveries(now_epoch=10**12, lease_seconds=30)
        reclaimed = self.center.claim_due_deliveries(now_epoch=10**12 + 30, lease_seconds=30)
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in first])
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in reclaimed])
        sent: list[dict[str, object]] = []

        class Telegram:
            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram())
        worker.deliver(first[0])
        self.assertEqual([], sent)
        self.assertEqual("claimed", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"])
        worker.deliver(reclaimed[0])
        self.assertEqual(1, len(sent))
        self.assertEqual("sent", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"])

    def test_stale_lease_exception_cannot_requeue_reclaimed_delivery(self) -> None:
        created = self.center.create_event("producer", "stale-lease-exception", self.event)
        first = self.center.claim_due_deliveries(now_epoch=10**12, lease_seconds=180)
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in first])
        center = self.center

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                center.claim_due_deliveries(now_epoch=10**12 + 180, lease_seconds=180)
                raise RuntimeError("transport failed after lease reclaim")

        worker = DeliveryWorker(self.center, Telegram())
        worker.deliver(first[0])
        row = self.center._connection.execute(
            "SELECT status, attempt FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()
        self.assertEqual("uncertain", row["status"])
        self.assertEqual(1, row["attempt"])

    def test_send_reservation_is_not_reclaimed_after_worker_crash(self) -> None:
        created = self.center.create_event("producer", "send-reservation", self.event)
        first = self.center.claim_due_deliveries(now_epoch=10**12, lease_seconds=30)
        self.assertEqual([created["initial_delivery_id"]], [item["id"] for item in first])
        self.assertTrue(self.center.reserve_delivery_send(
            first[0]["id"], claimed_at=first[0]["claimed_at"], attempt=first[0]["attempt"]
        ))
        reopened = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "hermes", "max_severity": "emergency"}}, default_quiet_hours=[])
        self.assertEqual([], reopened.claim_due_deliveries(now_epoch=10**12 + 30, lease_seconds=30))
        self.assertEqual("sending", reopened._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"])
        reopened._connection.close()

    def test_post_send_failure_cannot_requeue_a_sent_delivery(self) -> None:
        created = self.center.create_event("producer", "post-send-failure", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> dict[str, object]:
                return {"message_id": 91, "chat_id": "-100123"}

        worker = DeliveryWorker(self.center, Telegram())

        def fail_after_send(_delivery: dict[str, object], _incident: dict[str, object]) -> None:
            raise RuntimeError("follow-up scheduling failed")

        worker._after_telegram_delivery = fail_after_send
        worker.run_once()
        self.assertEqual("sent", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (created["initial_delivery_id"],)
        ).fetchone()["status"])

    def test_health_plans_do_not_cancel_an_inflight_send_or_schedule_a_second_card(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-inflight-send",
            {**self.event, "event_type": "health.degraded"},
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual([delivery_id], [item["id"] for item in claimed])
        self.assertTrue(self.center.reserve_delivery_send(
            delivery_id, claimed_at=claimed[0]["claimed_at"], attempt=claimed[0]["attempt"]
        ))
        self.center.record_health_update(
            created["incident_id"],
            "health-inflight-send-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        rows = self.center._connection.execute(
            "SELECT status, delivery_key FROM deliveries WHERE incident_id = ? ORDER BY created_at",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([("sending", f"{created['incident_id']}:telegram.main:initial")], [(row["status"], row["delivery_key"]) for row in rows])

    def test_uncertain_health_send_blocks_plan_card_until_reconciled(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-uncertain-send",
            {**self.event, "event_type": "health.degraded"},
        )
        delivery_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.center.reserve_delivery_send(delivery_id, claimed[0]["claimed_at"], claimed[0]["attempt"])
        self.center.complete_delivery(
            delivery_id,
            "uncertain",
            "external outcome unknown",
            claimed_at=claimed[0]["claimed_at"],
            attempt=claimed[0]["attempt"],
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-uncertain-send-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        self.assertEqual([], self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND delivery_key LIKE ?",
            (created["incident_id"], f"{created['incident_id']}:%:health.plans"),
        ).fetchall())
        self.assertIsNotNone(self.center._connection.execute(
            "SELECT 1 FROM audit_events WHERE incident_id = ? AND type = 'health.uncertain_delivery_reconciliation_required'",
            (created["incident_id"],),
        ).fetchone())

    def test_multiple_sent_health_cards_block_new_plan_delivery(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-multiple-sent",
            {**self.event, "event_type": "health.degraded"},
        )
        first_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        second_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "health.plans", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual({first_id, second_id}, {item["id"] for item in claimed})
        result = {
            "message_id": 101,
            "chat_id": "-100123",
            "health_plan_ids": ["observe", "repair", "verify"],
            "health_button_count": 3,
            "health_signed_callback_count": 3,
        }
        for delivery in claimed:
            self.center.complete_delivery(delivery["id"], "sent", result=result)
        self.center.record_health_update(
            created["incident_id"],
            "health-multiple-sent-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        plan_rows = self.center._connection.execute(
            "SELECT id, status FROM deliveries WHERE incident_id = ? AND delivery_key LIKE ?",
            (created["incident_id"], f"{created['incident_id']}:%:health.plans"),
        ).fetchall()
        self.assertEqual([(second_id, "sent")], [(row["id"], row["status"]) for row in plan_rows])
        self.assertIsNotNone(self.center._connection.execute(
            "SELECT 1 FROM audit_events WHERE incident_id = ? AND type = 'health.multiple_sent_delivery_reconciliation_required'",
            (created["incident_id"],),
        ).fetchone())
        self.assertEqual("degraded", self.center.health()["status"])

    def test_sent_and_sending_health_cards_block_new_plan_delivery(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-sent-and-sending",
            {**self.event, "event_type": "health.degraded"},
        )
        sent_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        sending_id = self.center._schedule_delivery(created["incident_id"], "telegram.consumer:custom", "initial", 0)
        claimed = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual({sent_id, sending_id}, {item["id"] for item in claimed})
        sending_claim = next(item for item in claimed if item["id"] == sending_id)
        self.center.complete_delivery(sent_id, "sent", result={
            "message_id": 102,
            "chat_id": "-100123",
            "health_plan_ids": ["observe", "repair", "verify"],
            "health_button_count": 3,
            "health_signed_callback_count": 3,
        })
        self.center.reserve_delivery_send(sending_id, sending_claim["claimed_at"], sending_claim["attempt"])
        self.center.record_health_update(
            created["incident_id"],
            "health-sent-and-sending-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        statuses = self.center._connection.execute(
            "SELECT id, status FROM deliveries WHERE incident_id = ? ORDER BY id", (created["incident_id"],)
        ).fetchall()
        self.assertEqual({sent_id: "sent", sending_id: "sending"}, {row["id"]: row["status"] for row in statuses})
        self.assertIsNotNone(self.center._connection.execute(
            "SELECT 1 FROM audit_events WHERE incident_id = ? AND type = 'health.sent_and_sending_reconciliation_required'",
            (created["incident_id"],),
        ).fetchone())

    def test_health_generic_telegram_policy_does_not_schedule_repeats(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Health generic Telegram",
            policy=[
                {
                    "id": "telegram-health-root",
                    "platform": "telegram",
                    "action": "message",
                    "target": {"chat_id": -100123},
                    "retry_interval_seconds": 60,
                    "max_repeats": 2,
                },
            ],
        )
        created = self.center.create_event(
            consumer["intake_token"],
            "health-generic-repeat",
            {**self.event, "event_type": "health.degraded"},
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-generic-repeat-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        sent: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        worker = DeliveryWorker(self.center, Telegram())
        self.assertEqual(1, worker.run_once())
        self.assertEqual(1, len(sent))
        queued = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND status IN ('queued', 'claimed')",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], queued)

    def test_health_skips_telegram_repeat_but_keeps_matrix_successor(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Health Telegram then Matrix",
            policy=[
                {
                    "id": "telegram-health-root-successor",
                    "platform": "telegram",
                    "action": "message",
                    "target": {"chat_id": -100123},
                    "retry_interval_seconds": 60,
                    "max_repeats": 2,
                },
                {
                    "id": "telegram-health-middle",
                    "platform": "telegram",
                    "action": "message",
                    "target": {"chat_id": -100123},
                    "retry_interval_seconds": 30,
                    "max_repeats": 1,
                    "previous_step_id": "telegram-health-root-successor",
                },
                {
                    "id": "matrix-health-successor",
                    "platform": "matrix",
                    "action": "call",
                    "target": {"room_id": "!ops:example.org"},
                    "retry_interval_seconds": 30,
                    "max_repeats": 1,
                    "previous_step_id": "telegram-health-middle",
                },
            ],
        )
        created = self.center.create_event(
            consumer["intake_token"],
            "health-telegram-matrix-successor",
            {**self.event, "event_type": "health.degraded"},
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-telegram-matrix-successor-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )

        class Telegram:
            active_modes = {"health"}

            def send(self, _payload: dict[str, object]) -> None:
                return None

        self.assertEqual(1, DeliveryWorker(self.center, Telegram()).run_once())
        rows = self.center._connection.execute(
            "SELECT channel, status FROM deliveries WHERE incident_id = ? ORDER BY created_at",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([("telegram.message", "sent"), ("matrix.call", "queued")], [(row["channel"], row["status"]) for row in rows])

    def test_health_phone_pre_call_does_not_send_a_second_telegram_card(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-phone-precall",
            {**self.event, "event_type": "health.degraded"},
        )
        initial_id = self.center._schedule_delivery(created["incident_id"], "telegram.main", "initial", 0)
        self.center.record_health_update(
            created["incident_id"],
            "health-phone-precall-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        phone_id = self.center._schedule_delivery(created["incident_id"], "android.phone.call", "escalation", 0)
        sent: list[dict[str, object]] = []
        calls: list[dict[str, object]] = []

        class Telegram:
            active_modes = {"health"}

            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        class Android:
            can_phone_call = True

            def phone_call(self, payload: dict[str, object]) -> None:
                calls.append(payload)

        worker = DeliveryWorker(self.center, Telegram(), android_phone=Android())
        self.assertEqual(2, worker.run_once())
        self.assertEqual(1, len(sent))
        self.assertEqual(1, len(calls))
        self.assertEqual("sent", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (initial_id,)
        ).fetchone()["status"])
        self.assertEqual("sent", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (phone_id,)
        ).fetchone()["status"])

    def test_custom_health_initial_coalesces_with_plan_card(self) -> None:
        created = self.center.create_event(
            "producer",
            "health-custom-coalesce",
            {**self.event, "event_type": "health.degraded"},
        )
        custom_id = self.center._schedule_delivery(
            created["incident_id"], "telegram.consumer:custom", "initial", 0,
        )
        self.center.record_health_update(
            created["incident_id"],
            "health-custom-coalesce-plans",
            "health.plans_attached",
            {"plans": [
                {"plan_id": "observe", "title": "Observe", "summary": "Collect evidence"},
                {"plan_id": "repair", "title": "Repair", "summary": "Apply the fix"},
                {"plan_id": "verify", "title": "Verify", "summary": "Check the source"},
            ]},
            actor="omniroute",
        )
        main_rows = self.center._connection.execute(
            "SELECT id FROM deliveries WHERE incident_id = ? AND channel = 'telegram.main'",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], main_rows)
        self.assertEqual("queued", self.center._connection.execute(
            "SELECT status FROM deliveries WHERE id = ?", (custom_id,)
        ).fetchone()["status"])

    def test_confirmed_matrix_answer_acknowledges_only_that_incident(self) -> None:
        created = self.center.create_event("producer", "create", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)
        calls: list[dict[str, object]] = []

        class Matrix:
            def send(self, payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {"answered": True, "actor": "matrix:@bezrabotnyi:chat.example"}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix escalation adapter")

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix()).run_once())
        self.assertEqual("acknowledged", self.center.get_incident(created["incident_id"])["state"])
        self.assertEqual(1, len(calls))

    def test_initial_critical_telegram_delivery_schedules_one_matrix_call(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=object(), call_escalation_seconds=60)
        self.assertEqual(1, worker.run_once())
        queued = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual(["matrix.call"], [item["channel"] for item in queued])
        self.assertEqual(created["incident_id"], queued[0]["incident_id"])

    def test_automatic_calls_can_be_disabled_without_restarting_worker(self) -> None:
        created = self.center.create_event("producer", "runtime-disable", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "android.phone.call", due_epoch=0)
        self.center.set_runtime_setting("automatic_calls_enabled", "false")
        calls: list[dict[str, object]] = []

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            def phone_call(self, payload: dict[str, object]) -> None:
                calls.append(payload)

        worker = DeliveryWorker(self.center, Telegram(), android_phone=Android())
        self.assertEqual(1, worker.run_once())
        self.assertEqual([], calls)
        status = self.center._connection.execute(
            "SELECT status, last_error FROM deliveries WHERE channel = 'android.phone.call' AND incident_id = ?",
            (created["incident_id"],),
        ).fetchone()
        self.assertEqual("cancelled", status["status"])
        self.assertEqual("automatic calls disabled by operator", status["last_error"])

    def test_runtime_phone_delay_is_used_for_new_escalations(self) -> None:
        created = self.center.create_event("producer", "runtime-delay", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            can_phone_call = True

        self.center.set_runtime_setting("android_phone_call_escalation_seconds", "17")
        worker = DeliveryWorker(self.center, Telegram(), android_phone=Android(), android_phone_call_escalation_seconds=600)
        with mock.patch("notification_center.http_api.time.time", return_value=10**12):
            self.assertEqual(1, worker.run_once())
        row = self.center._connection.execute(
            "SELECT due_at FROM deliveries WHERE incident_id = ? AND channel = 'android.phone.call'",
            (created["incident_id"],),
        ).fetchone()
        self.assertAlmostEqual(10**12 + 17, row["due_at"])

    def test_critical_repeats_while_open_and_calls_matrix_after_its_deadline(self) -> None:
        created = self.center.create_event("producer", "critical-repeat", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(
            self.center,
            Telegram(),
            matrix_call=object(),
            critical_repeat_seconds=600,
            critical_call_escalation_seconds=3600,
        )
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel, delivery_key FROM deliveries WHERE incident_id = ? AND status = 'queued' ORDER BY delivery_key",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(
            [("matrix.call", f"{created['incident_id']}:matrix.call:escalation"), ("telegram.main", f"{created['incident_id']}:telegram.main:repeat:1")],
            [(row["channel"], row["delivery_key"]) for row in rows],
        )

    def test_emergency_schedules_matrix_without_repeat(self) -> None:
        emergency = {**self.event, "severity": "emergency", "dedup_key": "gateway:emergency"}
        created = self.center.create_event("producer", "emergency", emergency)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=object(), emergency_call_escalation_seconds=600)
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id = ? AND status = 'queued' ORDER BY channel",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual(["matrix.call"], [row["channel"] for row in rows])

    def test_important_is_single_shot_without_repeat_or_call(self) -> None:
        important = {**self.event, "severity": "important", "dedup_key": "gateway:important"}
        created = self.center.create_event("producer", "important", important)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        worker = DeliveryWorker(
            self.center,
            Telegram(),
            matrix_call=object(),
            critical_repeat_seconds=600,
            critical_call_escalation_seconds=3600,
            emergency_call_escalation_seconds=600,
        )
        self.assertEqual(1, worker.run_once())
        rows = self.center._connection.execute(
            "SELECT channel FROM deliveries WHERE incident_id = ? AND status = 'queued'",
            (created["incident_id"],),
        ).fetchall()
        self.assertEqual([], rows)

    def test_unanswered_matrix_call_falls_back_to_s21(self) -> None:
        created = self.center.create_event("producer", "matrix-fallback", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)

        class Matrix:
            def send(self, _payload: dict[str, object]) -> dict[str, object]:
                return {"answered": False, "actor": None}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix fallback adapter")

        class Android:
            can_phone_call = True

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix(), android_phone=Android()).run_once())
        queued = self.center.claim_due_deliveries(now_epoch=10**12)
        self.assertEqual(["android.phone.call"], [item["channel"] for item in queued])

    def test_initial_critical_delivery_schedules_one_phone_call_after_configured_delay(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            can_phone_call = True

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def phone_call(self, payload: dict[str, object]) -> None:
                self.calls.append(payload)

        android = Android()
        worker = DeliveryWorker(
            self.center,
            Telegram(),
            android_phone=android,
            android_phone_call_escalation_seconds=600,
        )
        with mock.patch("notification_center.http_api.time.time", return_value=10**12):
            self.assertEqual(1, worker.run_once())

        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=(10**12) + 599.0))
        due = self.center.claim_due_deliveries(now_epoch=(10**12) + 600.0)
        self.assertEqual(["android.phone.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(android.calls))
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=10**12))

    def test_critical_phone_call_sends_context_before_call(self) -> None:
        created = self.center.create_event("producer", "pre-call-context", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "android.phone.call", due_epoch=0)
        order: list[str] = []

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                order.append("telegram")

        class Android:
            def phone_call(self, _payload: dict[str, object]) -> None:
                order.append("phone")

        due = self.center.claim_due_deliveries(now_epoch=0)
        DeliveryWorker(self.center, Telegram(), android_phone=Android()).deliver(due[0])
        self.assertEqual(["telegram", "phone"], order)

    def test_consumer_telegram_uses_policy_target_and_keeps_existing_phone_deadline(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Gateway producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123, "topic_id": 42},
                {"kind": "phone", "delay_seconds": 600},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "consumer-dispatch", self.event)
        sent: list[dict[str, object]] = []

        class Telegram:
            def send(self, payload: dict[str, object]) -> None:
                sent.append(payload)

        class Android:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def phone_call(self, payload: dict[str, object]) -> None:
                self.calls.append(payload)

        android = Android()
        worker = DeliveryWorker(self.center, Telegram(), android_phone=android)
        deadlines = self.center._connection.execute(
            "SELECT channel, due_at FROM deliveries WHERE incident_id = ? ORDER BY due_at", (created["incident_id"],)
        ).fetchall()
        initial = self.center.claim_due_deliveries(now_epoch=deadlines[0]["due_at"])
        self.assertEqual(1, len(initial))
        worker.deliver(initial[0])
        self.assertEqual({"chat_id": -100123, "topic_id": 42}, sent[0]["target"])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=deadlines[1]["due_at"] - 0.1))
        due = self.center.claim_due_deliveries(now_epoch=deadlines[1]["due_at"])
        self.assertEqual(["android.phone.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(android.calls))

    def test_consumer_matrix_is_not_delivered_before_its_policy_deadline(self) -> None:
        consumer = self.center.create_consumer(
            project="hermes",
            name="Matrix producer",
            policy=[
                {"kind": "telegram", "chat_id": -100123},
                {"kind": "matrix", "delay_seconds": 120},
                {"kind": "phone", "delay_seconds": 600},
            ],
        )
        created = self.center.create_event(consumer["intake_token"], "consumer-matrix", self.event)
        calls: list[dict[str, object]] = []

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Matrix:
            def send(self, payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {"answered": False, "actor": None}

        worker = DeliveryWorker(self.center, Telegram(), matrix_call=Matrix())
        rows = self.center._connection.execute(
            "SELECT channel, due_at FROM deliveries WHERE incident_id = ? ORDER BY due_at",
            (created["incident_id"],),
        ).fetchall()
        initial = self.center.claim_due_deliveries(now_epoch=rows[0]["due_at"])
        worker.deliver(initial[0])
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"] - 0.01))
        due = self.center.claim_due_deliveries(now_epoch=rows[1]["due_at"])
        self.assertEqual(["matrix.call"], [item["channel"] for item in due])
        worker.deliver(due[0])
        self.assertEqual(1, len(calls))

    def test_resolved_critical_incident_cancels_phone_call_before_deadline(self) -> None:
        created = self.center.create_event("producer", "create", self.event)

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                return None

        class Android:
            can_phone_call = True

            def phone_call(self, _payload: dict[str, object]) -> None:
                raise AssertionError("resolved incident must not call the phone")

        worker = DeliveryWorker(self.center, Telegram(), android_phone=Android(), android_phone_call_escalation_seconds=600)
        with mock.patch("notification_center.http_api.time.time", return_value=10**12):
            self.assertEqual(1, worker.run_once())
        self.center.resolve(created["incident_id"], "test:reply")
        self.assertEqual([], self.center.claim_due_deliveries(now_epoch=(10**12) + 600.0))

    def test_matrix_answer_after_resolution_does_not_resurrect_the_incident(self) -> None:
        created = self.center.create_event("producer", "create", self.event)
        self.center.complete_delivery(created["initial_delivery_id"], "sent")
        self.center.schedule_escalation(created["incident_id"], "matrix.call", due_epoch=0)

        class Matrix:
            def send(_self, _payload: dict[str, object]) -> dict[str, object]:
                self.center.resolve(created["incident_id"], "producer")
                return {"answered": True, "actor": "matrix:@bezrabotnyi:chat.example"}

        class Telegram:
            def send(self, _payload: dict[str, object]) -> None:
                raise AssertionError("Telegram is not the matrix escalation adapter")

        self.assertEqual(1, DeliveryWorker(self.center, Telegram(), matrix_call=Matrix()).run_once())
        self.assertEqual("resolved", self.center.get_incident(created["incident_id"])["state"])
        status = self.center._connection.execute(
            "SELECT status FROM deliveries WHERE incident_id = ? AND channel = 'matrix.call'", (created["incident_id"],)
        ).fetchone()["status"]
        self.assertEqual("cancelled", status)

    def test_matrix_sender_posts_only_safe_incident_fields_and_requires_answer(self) -> None:
        received: dict[str, object] = {}

        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok":true,"answered":true,"target":"@bezrabotnyi:chat.example"}'

        def opener(request: object, timeout: float) -> Response:
            received["url"] = request.full_url
            received["body"] = request.data.decode()
            received["authorization"] = request.get_header("Authorization")
            received["timeout"] = timeout
            return Response()

        sender = MatrixCallSender("http://matrix-bridge.test/v1/calls", "bridge-token", runner=opener)
        result = sender.send({"incident": {"id": "inc_123", "title": "Outage", "body": "details", "project": "hermes", "severity": "critical"}})
        self.assertTrue(result["answered"])
        self.assertEqual("matrix:@bezrabotnyi:chat.example", result["actor"])
        self.assertEqual("http://matrix-bridge.test/v1/calls", received["url"])
        self.assertEqual("Bearer bridge-token", received["authorization"])
        self.assertIn('"incident_id": "inc_123"', str(received["body"]))
        self.assertNotIn("access_token", str(received["body"]))

        class Unanswered(Response):
            def read(self) -> bytes:
                return b'{"ok":true,"answered":false}'

        self.assertFalse(MatrixCallSender("http://bridge.test/v1/calls", "bridge-token", runner=lambda *_args, **_kwargs: Unanswered()).send({"incident": {"id": "inc_123", "title": "Outage", "body": "details", "project": "hermes", "severity": "critical"}})["answered"])


if __name__ == "__main__":
    unittest.main()
