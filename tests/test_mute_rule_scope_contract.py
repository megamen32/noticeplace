"""Offline UI/CAS and migration contracts; temporary SQLite, no deliveries sent."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notification_center.core import NotificationCenter, ValidationError
from notification_center.notification_mute_policy import legacy_fail2ban_mutes, migrate_legacy_fail2ban_mutes


class MuteRuleScopeTests(unittest.TestCase):
    family = 'fail2ban:fail2ban.server-100:sshd:'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'notice.sqlite3'
        self.tokens = {'fixture': {'project': 'fail2ban.server-100', 'max_severity': 'critical'}}
        self.center = NotificationCenter(self.path, self.tokens)
        self.addCleanup(lambda: self.center._connection.close())
        self.counter = 0

    def event(self, ip='203.0.113.10', jail='sshd'):
        self.counter += 1
        return self.center.create_event('fixture', str(self.counter), {
            'schema': 'notify.event.v1', 'project': 'fail2ban.server-100', 'recipient': 'test-only',
            'kind': 'notification', 'severity': 'notice', 'title': 'Тест защиты',
            'dedup_key': f'fail2ban:fail2ban.server-100:{jail}:{ip}',
        })

    def legacy_pair(self):
        first, second = self.event(), self.event('203.0.113.11')
        with patch('notification_center.core.time.time', return_value=100):
            for event in (first, second):
                self.center.set_incident_notifications_muted(event['incident_id'], True, 'old-operator', scope='exact')
        return first, second, legacy_fail2ban_mutes(self.center._connection)

    def test_ui_scope_matches_the_family_actually_mutated(self):
        first = self.event()['incident_id']
        before = self.center.notification_rule_settings(first)
        self.assertEqual(self.family, before['dedup_key'])
        self.assertEqual('prefix', before['match_mode'])
        after = self.center.set_notification_rule_option(first, 'notifications', False, before['revision'], 'fixture:A')
        second = self.event('2001:db8::11')
        self.assertTrue(second['notifications_muted'])
        self.assertEqual(after['dedup_key'], self.center.notification_rule_settings(second['incident_id'])['dedup_key'])
        self.center._connection.close()
        self.center = NotificationCenter(self.path, self.tokens)
        self.assertEqual(after, self.center.notification_rule_settings(first))

    def test_family_write_rejects_stale_revision_from_another_ip(self):
        first, second = self.event()['incident_id'], self.event('203.0.113.11')['incident_id']
        stale = self.center.notification_rule_settings(second)
        self.center.set_notification_rule_option(first, 'notifications', False, stale['revision'], 'fixture:A')
        with self.assertRaisesRegex(ValidationError, 'reload'):
            self.center.set_notification_rule_option(second, 'notifications', True, stale['revision'], 'fixture:B')
        self.assertTrue(self.event('203.0.113.12')['notifications_muted'])

    def test_first_exact_rule_write_also_rejects_stale_revision(self):
        incident = self.event()['incident_id']
        with self.center._connection:
            self.center._connection.execute('UPDATE incidents SET dedup_key=? WHERE id=?', ('monitor:cpu', incident))
        old = self.center.notification_rule_settings(incident)
        after = self.center.set_notification_rule_option(incident, 'notifications', False, old['revision'], 'fixture:A')
        self.assertNotEqual(old['revision'], after['revision'])
        with self.assertRaisesRegex(ValidationError, 'reload'):
            self.center.set_notification_rule_option(incident, 'notifications', True, old['revision'], 'fixture:B')

    def test_existing_per_ip_ai_choice_survives_family_notification_scope(self):
        first, second = self.event()['incident_id'], self.event('203.0.113.11')['incident_id']
        off = self.center.set_notification_rule_option(first, 'auto_ai', False, 0, 'fixture:A')
        muted = self.center.set_notification_rule_option(first, 'notifications', False, off['revision'], 'fixture:A')
        self.assertFalse(muted['auto_ai_enabled'])
        self.assertTrue(self.center.notification_rule_settings(second)['auto_ai_enabled'])
        self.assertTrue(self.center.get_incident(second)['notifications_muted'])
        with self.assertRaisesRegex(ValidationError, 'reload'):
            self.center.set_notification_rule_option(first, 'auto_ai', True, off['revision'], 'fixture:B')
        restored = self.center.set_notification_rule_option(first, 'notifications', True, muted['revision'], 'fixture:A')
        self.assertFalse(restored['auto_ai_enabled'])
        self.assertEqual(self.family, restored['dedup_key'])

    def test_family_unmute_releases_all_migrated_ips_and_preserves_other_jail(self):
        first, second, preview = self.legacy_pair()
        with self.center._connection:
            self.assertEqual(1, migrate_legacy_fail2ban_mutes(self.center._connection, preview))
        other = self.event('203.0.113.10', 'nginx')
        self.center.set_incident_notifications_muted(other['incident_id'], True, 'other-operator', scope='exact')
        self.center._connection.close()
        self.center = NotificationCenter(self.path, self.tokens)
        self.center.set_incident_notifications_muted(first['incident_id'], False, 'fixture:A')
        for ip in ('203.0.113.10', '203.0.113.11', '2001:db8::12'):
            self.assertFalse(self.event(ip)['notifications_muted'], ip)
        self.assertTrue(self.center.get_incident(other['incident_id'])['notifications_muted'])
        self.assertEqual(2, self.center._connection.execute(
            "SELECT COUNT(*) FROM notification_mutes WHERE project=? AND match_mode='exact' AND dedup_key IN (?,?)",
            ('fail2ban.server-100', self.family+'203.0.113.10', self.family+'203.0.113.11')).fetchone()[0])

    def test_later_exact_choice_survives_family_enable_even_on_the_same_card(self):
        first, _, preview = self.legacy_pair()
        with self.center._connection:
            migrate_legacy_fail2ban_mutes(self.center._connection, preview)
        # Same timestamp as the original: order must not be guessed from time.
        with patch('notification_center.core.time.time', return_value=100):
            self.center.set_incident_notifications_muted(first['incident_id'], True, 'new-operator', scope='exact')
        rule = self.center.notification_rule_settings(first['incident_id'])
        restored = self.center.set_notification_rule_option(first['incident_id'], 'notifications', True, rule['revision'], 'fixture:A')
        self.assertTrue(restored['notifications_enabled'], 'the displayed family rule is enabled')
        self.assertTrue(self.center.get_incident(first['incident_id'])['notifications_muted'], 'independent exact rule survives')
        self.assertFalse(self.event('203.0.113.11')['notifications_muted'])
        repeated = self.center.set_incident_notifications_muted(first['incident_id'], False, 'fixture:A')
        self.assertTrue(repeated['idempotent'], 'family already enabled despite the independent IP mute')

    def test_saved_preview_can_finish_old_migration_without_overwriting_later_choices(self):
        from notification_center.notification_mute_policy import retire_migrated_exact_mutes
        first, second, preview = self.legacy_pair()
        # Old deployment inserted only the family row and retained exact rows.
        with self.center._connection:
            self.center._connection.execute(
                "INSERT INTO notification_mutes(project,recipient,dedup_key,muted_at,muted_by,match_mode) VALUES (?,?,?,?,?,'prefix')",
                ('fail2ban.server-100', 'test-only', self.family, 100, 'migrated:old-operator'))
        with patch('notification_center.core.time.time', return_value=100):
            self.center.set_incident_notifications_muted(first['incident_id'], True, 'new-operator', scope='exact')
        with self.center._connection:
            self.assertEqual(1, retire_migrated_exact_mutes(self.center._connection, preview))
            self.assertEqual(0, retire_migrated_exact_mutes(self.center._connection, preview))
        self.center.set_incident_notifications_muted(second['incident_id'], False, 'fixture:A')
        self.assertTrue(self.center.get_incident(first['incident_id'])['notifications_muted'])
        self.assertFalse(self.center.get_incident(second['incident_id'])['notifications_muted'])

    def test_old_preview_cannot_retire_exact_rules_after_a_family_operator_change(self):
        from notification_center.notification_mute_policy import retire_migrated_exact_mutes
        first, second, preview = self.legacy_pair()
        with self.center._connection:
            self.center._connection.execute(
                "INSERT INTO notification_mutes(project,recipient,dedup_key,muted_at,muted_by,match_mode) VALUES (?,?,?,?,?,'prefix')",
                ('fail2ban.server-100', 'test-only', self.family, 100, 'migrated:old-operator'))
        self.center.set_incident_notifications_muted(first['incident_id'], False, 'new-operator')
        with self.center._connection:
            self.assertEqual(0, retire_migrated_exact_mutes(self.center._connection, preview))
        self.assertTrue(self.center.get_incident(second['incident_id'])['notifications_muted'])

    def test_exact_unmute_does_not_enable_a_separate_family_rule(self):
        first = self.event()['incident_id']
        self.center.set_incident_notifications_muted(first, True, 'exact-operator', scope='exact')
        self.center.set_incident_notifications_muted(first, True, 'family-operator')
        restored = self.center.set_incident_notifications_muted(first, False, 'exact-operator', scope='exact')
        self.assertTrue(restored['notifications_muted'])
        self.assertTrue(self.event('203.0.113.11')['notifications_muted'])
