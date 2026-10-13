"""Fast unit: idle renewal must preserve transaction boundaries and no replay.

Detected defect: renewal abandoned a pending COMMIT after 90 seconds idle.
Expected 1s/max 5s. Mocked wire and clock prove local state logic only.
"""
import io
import sqlite3
import unittest
from unittest.mock import Mock, patch

from notification_center.mesh_store import Connection


class IdleConnectionTests(unittest.TestCase):
    def wire(self):
        wire = Mock()
        wire.makefile.return_value = io.BytesIO(b'{}\n' * 12)
        return wire

    @patch('notification_center.mesh_store.time.monotonic')
    @patch('notification_center.mesh_store.socket.socket')
    def test_idle_operation_renews_before_send(self, constructor, clock):
        constructor.side_effect = [self.wire(), self.wire()]
        clock.return_value = 1
        connection = Connection('/test/socket')
        connection.execute('SELECT 1')
        first = connection._socket
        clock.return_value = 92
        connection.execute('SELECT 2')
        first.close.assert_called_once()
        self.assertEqual(constructor.call_count, 2)

    @patch('notification_center.mesh_store.time.monotonic')
    @patch('notification_center.mesh_store.socket.socket')
    def test_idle_commit_and_rollback_keep_original_connection(self, constructor, clock):
        for failed in (False, True):
            with self.subTest(failed=failed):
                constructor.reset_mock()
                constructor.return_value = self.wire()
                clock.return_value = 1
                connection = Connection('/test/socket')
                connection.__enter__()
                wire = connection._socket
                clock.return_value = 92
                connection.__exit__(RuntimeError if failed else None, None, None)
                wire.close.assert_not_called()
                self.assertEqual(constructor.call_count, 1)
                self.assertFalse(connection._transaction_active)
                self.assertIn(b'ROLLBACK' if failed else b'COMMIT', wire.sendall.call_args.args[0])

    @patch('notification_center.mesh_store.time.monotonic')
    @patch('notification_center.mesh_store.socket.socket')
    def test_transaction_control_punctuation_and_whitespace(self, constructor, clock):
        constructor.return_value = self.wire()
        clock.return_value = 1
        connection = Connection('/test/socket')
        connection.execute('/* operator comment */ BEGIN;')
        self.assertTrue(connection._transaction_active)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'savepoint transactions are unsupported'):
            connection.execute('SAVEPOINT nested')
        connection.execute('ROLLBACK\tTO savepoint')
        self.assertTrue(connection._transaction_active)
        clock.return_value = 92
        connection.execute('COMMIT;')
        self.assertFalse(connection._transaction_active)
        self.assertEqual(constructor.call_count, 1)

    @patch('notification_center.mesh_store.time.monotonic')
    @patch('notification_center.mesh_store.socket.socket')
    def test_explicit_begin_is_protected_and_lost_write_is_not_replayed(self, constructor, clock):
        constructor.return_value = self.wire()
        clock.return_value = 1
        connection = Connection('/test/socket')
        connection.execute('BEGIN IMMEDIATE')
        clock.return_value = 92
        connection._socket.sendall.side_effect = OSError('lost response')
        with self.assertRaisesRegex(sqlite3.OperationalError, 'not replayed'):
            connection.execute('INSERT INTO test VALUES (1)')
        with self.assertRaisesRegex(sqlite3.OperationalError, 'cannot continue'):
            connection.execute('SELECT 1')
        self.assertEqual(constructor.call_count, 1)
