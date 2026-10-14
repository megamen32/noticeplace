"""Fast unit: admission classification only; no runtime/failover proof.

Purpose: prevent a lost or ambiguous native response from redialing elsewhere.
Expected1s/max10s. Network doubles belong only to this local logic scenario.
"""
import unittest
from unittest.mock import Mock, patch

from notification_center.mesh_phone import PhoneMeshRequester, read_frame


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

    def test_preflight_retries_registration_then_sends_payload_once(self):
        requester=self.requester();connection=Mock();requester.tls.wrap_socket.return_value=connection
        clock={'now':0.0}
        def sleep(seconds):clock['now']+=seconds
        with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first','second']}), \
             patch('notification_center.mesh_phone.socket.create_connection') as connect, \
             patch('notification_center.mesh_phone.time.monotonic',side_effect=lambda:clock['now']), \
             patch('notification_center.mesh_phone.time.sleep',side_effect=sleep), \
             patch('notification_center.mesh_phone.read_frame',side_effect=[b'{"ready":false}',b'{"ready":false}',b'{"ready":true}',b'{"ok":true,"call_id":"unit-call"}']):
            self.assertIn(b'unit-call',requester('',b'{"message":"unit"}',30))
            self.assertEqual(connect.call_count,3)
            self.assertEqual(1,sum(call.args[0]==b'{"message":"unit"}\n' for call in connection.sendall.call_args_list))
            self.assertGreater(clock['now'],0)

    def test_registration_wait_uses_request_deadline_without_payload(self):
        for timeout,limit in ((2,2),(30,15)):
            with self.subTest(timeout=timeout):
                requester=self.requester();connection=Mock();requester.tls.wrap_socket.return_value=connection
                clock={'now':0.0}
                def sleep(seconds):clock['now']+=seconds
                with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first']}), \
                     patch('notification_center.mesh_phone.socket.create_connection'), \
                     patch('notification_center.mesh_phone.time.monotonic',side_effect=lambda:clock['now']), \
                     patch('notification_center.mesh_phone.time.sleep',side_effect=sleep), \
                     patch('notification_center.mesh_phone.read_frame',return_value=b'{"ready":false}'):
                    with self.assertRaises(RuntimeError):requester('',b'{"message":"unit"}',timeout)
                    self.assertEqual(clock['now'],limit)
                    self.assertTrue(all(call.args[0]==b'{"op":"status"}\n' for call in connection.sendall.call_args_list))

    def test_partial_status_frames_cannot_outlive_readiness_deadline(self):
        connection=Mock();connection.recv.return_value=b'x'
        with patch('notification_center.mesh_phone.time.monotonic',side_effect=[0,1,2]):
            with self.assertRaises(OSError):read_frame(connection,deadline=2)
        self.assertEqual(2,connection.recv.call_count)
        self.assertEqual([((2,),{}),((1,),{})],connection.settimeout.call_args_list)

    def test_stalled_status_leaves_time_for_ready_second_peer(self):
        requester=self.requester();first=Mock();second=Mock();clock={'now':0.0,'timeout':0.0}
        first.settimeout.side_effect=lambda seconds:clock.update(timeout=seconds)
        def stalled_recv(_size):
            clock['now']+=clock['timeout']
            raise TimeoutError('status did not answer')
        first.recv.side_effect=stalled_recv
        second.recv.side_effect=[b'{"ready":true}\n',b'{"ok":true,"call_id":"unit-call"}\n']
        requester.tls.wrap_socket.side_effect=[first,second]
        with patch('notification_center.mesh_phone.normalize',return_value={'hosts':['first','second']}), \
             patch('notification_center.mesh_phone.socket.create_connection') as connect, \
             patch('notification_center.mesh_phone.time.monotonic',side_effect=lambda:clock['now']):
            self.assertIn(b'unit-call',requester('',b'{"message":"unit"}',30))
        self.assertEqual(3,clock['now']);self.assertEqual(2,connect.call_count)
        self.assertEqual([((b'{"op":"status"}\n',),{})],first.sendall.call_args_list)
        self.assertEqual(1,sum(call.args[0]==b'{"message":"unit"}\n' for call in second.sendall.call_args_list))
