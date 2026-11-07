"""«Отключить такие» silences the source's varying dedup keys, not one key.

Producer 2026-11-06 order: the Telegram mute button on a fail2ban card kept
letting later bans through, because each ban embeds its own IP in the dedup
key and the stored mute matched one exact key. The button now stores the
stable key prefix; every current and future key of that source stays quiet
until «Включить обратно». A different issue family of the same project stays
audible, and the HTTP API keeps its documented exact-scope contract.
"""
from __future__ import annotations

from unittest import mock
import tempfile
import unittest
from pathlib import Path

from notification_center.core import NotificationCenter


def ban_event(center: NotificationCenter, jail: str, ip: str, idempotency: str) -> dict[str, object]:
    key = f"fail2ban:fail2ban.server-100:{jail}:{ip}"
    return center.create_event("producer", idempotency, {
        "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
        "kind": "notification", "severity": "notice", "title": f"Защита сработала: IP {ip} заблокирован",
        "body": "Сервер: перебор паролей, адрес заблокирован. Штатная защита.",
        "dedup_key": key,
    })


class SourceScopeMuteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.center = NotificationCenter(Path(self.tempdir.name) / "notify.sqlite3", {"producer": {"project": "fail2ban.server-100", "max_severity": "critical"}})
        self.env = mock.patch.dict("os.environ", {"TELEGRAM_ACTIVE_MODES_JSON": '["decisions","notify","log"]'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.first = ban_event(self.center, "sshd", "203.0.113.10", "ban-1")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_mute_button_silences_future_dedup_keys_of_the_source(self) -> None:
        result = self.center.apply_telegram_action(self.first["incident_id"], "mute", "telegram:42")
        self.assertTrue(result["notifications_muted"])
        self.assertEqual("prefix", result["scope"])
        self.assertEqual("fail2ban:fail2ban.server-100:sshd:", result["dedup_key"])

        # The owner's exact bug: the next ban carries a different IP, so the
        # dedup key differs and the old exact mute never matched it again.
        second = ban_event(self.center, "sshd", "203.0.113.11", "ban-2")
        self.assertTrue(second["notifications_muted"])
        self.assertIsNone(second["initial_delivery_id"])
        suppressed = self.center._connection.execute(
            "SELECT COUNT(*) c FROM audit_events WHERE incident_id = ? AND type = 'notification_suppressed'",
            (second["incident_id"],),
        ).fetchone()
        self.assertGreaterEqual(suppressed["c"], 1)

    def test_unmute_restores_the_source_for_new_keys(self) -> None:
        self.center.apply_telegram_action(self.first["incident_id"], "mute", "telegram:42")
        self.center.apply_telegram_action(self.first["incident_id"], "unmute", "telegram:42")

        third = ban_event(self.center, "sshd", "203.0.113.12", "ban-3")
        self.assertFalse(third["notifications_muted"])
        self.assertIsNotNone(third["initial_delivery_id"])
        self.assertEqual([], self.center.list_notification_mutes())

    def test_mute_cancels_queued_cards_of_the_covered_keys(self) -> None:
        other = ban_event(self.center, "sshd", "203.0.113.20", "ban-4")
        self.center.apply_telegram_action(self.first["incident_id"], "mute", "telegram:42")
        statuses = self.center._connection.execute(
            "SELECT DISTINCT status FROM deliveries WHERE incident_id IN (?, ?) AND channel LIKE 'telegram%'",
            (self.first["incident_id"], other["incident_id"]),
        ).fetchall()
        self.assertEqual(["cancelled"], [row["status"] for row in statuses])

    def test_prefix_rule_is_listed_and_never_silences_other_sources(self) -> None:
        self.center.apply_telegram_action(self.first["incident_id"], "mute", "telegram:42")
        listed = self.center.list_notification_mutes()
        self.assertEqual(["prefix"], [row["match_mode"] for row in listed])
        self.assertEqual("fail2ban:fail2ban.server-100:sshd:", listed[0]["dedup_key"])

        # Another jail of the same fail2ban host keeps notifying.
        nginx = ban_event(self.center, "nginx-limit-req", "198.51.100.9", "ban-5")
        self.assertFalse(nginx["notifications_muted"])
        self.assertIsNotNone(nginx["initial_delivery_id"])

    def test_legacy_exact_mutes_still_hold_for_their_incident(self) -> None:
        legacy = ban_event(self.center, "nginx", "198.51.100.9", "ban-6")
        legacy_key = f"fail2ban:fail2ban.server-100:nginx:198.51.100.9"
        self.center._connection.execute(
            "INSERT INTO notification_mutes(project, recipient, dedup_key, muted_at, muted_by, match_mode) "
            "VALUES ('fail2ban.server-100', 'me', ?, 1, 'legacy', 'exact')",
            (legacy_key,),
        )
        repeat = self.center.create_event("producer", "ban-7", {
            "schema": "notify.event.v1", "project": "fail2ban.server-100", "recipient": "me",
            "kind": "incident", "severity": "notice", "title": "ban", "body": "b",
            "dedup_key": legacy_key,
        })
        self.assertTrue(repeat["notifications_muted"])
        self.assertIsNone(repeat["initial_delivery_id"])

    def test_http_api_keeps_the_documented_exact_scope(self) -> None:
        self.center.set_incident_notifications_muted(self.first["incident_id"], True, "api:test", scope="exact")
        listed = self.center.list_notification_mutes()
        self.assertEqual(["exact"], [row["match_mode"] for row in listed])
        self.assertEqual("fail2ban:fail2ban.server-100:sshd:203.0.113.10", listed[0]["dedup_key"])
        # A next ban of the same jail with a new IP still goes out: that is
        # exactly the behavior the owner reported, so the API keeps it and the
        # Telegram button owns the broadened rule.
        second = ban_event(self.center, "sshd", "203.0.113.11", "ban-8")
        self.assertFalse(second["notifications_muted"])

    def test_ipv6_source_mute_survives_new_address_and_restart(self) -> None:
        event = ban_event(self.center, 'sshd', '2001:db8:1::7', 'ipv6-first')
        muted = self.center.set_incident_notifications_muted(event['incident_id'], True, 'qa')
        self.assertEqual('fail2ban:fail2ban.server-100:sshd:',muted['dedup_key'])
        self.center._connection.close()
        self.center = NotificationCenter(Path(self.tempdir.name)/'notify.sqlite3', {'producer':{'project':'fail2ban.server-100','max_severity':'critical'}})
        repeated = ban_event(self.center,'sshd','2001:db8:ffff::8','ipv6-repeat')
        self.assertTrue(repeated['notifications_muted'])
        self.assertIsNone(repeated['initial_delivery_id'])
        other = ban_event(self.center,'nginx','2001:db8:ffff::8','ipv6-other')
        self.assertFalse(other['notifications_muted'])

    def test_prefix_underscore_does_not_match_other_jail_on_claim(self) -> None:
        event = ban_event(self.center, 'ssh_test', '203.0.113.5', 'underscore')
        other = ban_event(self.center, 'sshXtest', '203.0.113.6', 'literal-other')
        self.center.apply_telegram_action(event['incident_id'], 'mute', 'qa')
        due = self.center.claim_due_deliveries(now_epoch=10**12,channel_group='message')
        self.assertIn(other['incident_id'], [row['incident_id'] for row in due])

    def test_migration_uses_preview_and_never_overrides_later_family_unmute(self) -> None:
        from notification_center.notification_mute_policy import legacy_fail2ban_mutes, migrate_legacy_fail2ban_mutes
        self.center.set_incident_notifications_muted(self.first['incident_id'],True,'legacy',scope='exact')
        preview = legacy_fail2ban_mutes(self.center._connection)
        self.assertEqual(1,len(preview))
        with self.center._connection:
            self.assertEqual(1,migrate_legacy_fail2ban_mutes(self.center._connection,preview))
        self.assertTrue(ban_event(self.center,'sshd','2001:db8::9','migrated')['notifications_muted'])
        self.center.set_incident_notifications_muted(self.first['incident_id'],False,'human')
        self.assertEqual([],legacy_fail2ban_mutes(self.center._connection))
        with self.assertRaises(ValueError): migrate_legacy_fail2ban_mutes(self.center._connection,preview)
        self.assertFalse(ban_event(self.center,'sshd','2001:db8::a','unmuted')['notifications_muted'])


if __name__ == "__main__":
    unittest.main()
