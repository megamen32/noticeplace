"""Fast unit: admission classification only; no runtime/failover proof.

Purpose: prevent a lost or ambiguous native response from redialing elsewhere.
Expected1s/max5s. Network doubles belong only to this local logic scenario.
"""
import unittest
from unittest.mock import Mock, patch

from notification_center.mesh_phone import PhoneMeshRequester


class PhoneAdmissionTests(unittest.TestCase):
    def requester(self):
        result=object.__new__(PhoneMeshRequester)
        result.peers={host:{'address':'127.0.0.1','port':1} for host in ('first','second')}
        result.core_socket='unit-only';result.local_host='first';result.tls=Mock()
        return result

    def test_lost_receipt_never_uses_second_peer(self):
        requester=self.requester();connection=Mock();requester.tls.wrap_socket.return_value=connection
        with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first','second']}), \
             patch('notification_center.mesh_phone.socket.create_connection') as connect, \
             patch('notification_center.mesh_phone.read_frame',side_effect=[b'{"ready":true}',OSError('lost')]):
            with self.assertRaises(TimeoutError):requester('',b'{"message":"unit"}',30)
            self.assertEqual(connect.call_count,1)
            self.assertEqual(connection.sendall.call_count,2)
            connection.close.assert_called_once()

    def test_native_error_without_nonadmission_proof_is_unknown(self):
        requester=self.requester();requester.tls.wrap_socket.return_value=Mock()
        with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first','second']}), \
             patch('notification_center.mesh_phone.socket.create_connection') as connect, \
             patch('notification_center.mesh_phone.read_frame',side_effect=[b'{"ready":true}',b'{"ok":false,"error":"after originate"}']):
            with self.assertRaises(TimeoutError):requester('',b'{"message":"unit"}',30)
            self.assertEqual(connect.call_count,1)

    def test_preflight_unavailable_can_select_another_peer(self):
        requester=self.requester();requester.tls.wrap_socket.return_value=Mock()
        with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first','second']}), \
             patch('notification_center.mesh_phone.socket.create_connection') as connect, \
             patch('notification_center.mesh_phone.read_frame',side_effect=[b'{"ready":false}',b'{"ready":true}',b'{"ok":true,"call_id":"unit-call"}']):
            self.assertIn(b'unit-call',requester('',b'{"message":"unit"}',30))
            self.assertEqual(connect.call_count,2)
