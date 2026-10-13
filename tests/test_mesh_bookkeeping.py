"""Fast unit only: local future bookkeeping and lease policy, max10s.

These tests use a stub worker for local exception logic; they do not claim
provider, installation or distributed runtime acceptance.
"""
from __future__ import annotations
from concurrent.futures import Future
import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

from notification_center.core import NotificationCenter


class MeshBookkeepingTests(unittest.TestCase):
    def test_admitted_future_stays_tracked_when_later_claim_loses_storage(self):
        path = Path(__file__).resolve().parents[1] / 'bin/notify-center'
        loader = importlib.machinery.SourceFileLoader('notify_center_worker_unit', str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        future = Future()

        class Center:
            def due_human_completions(self, limit): return [{'request_id':'unit'}]
            def due_human_completion_cards(self, limit): return []

        class Worker:
            _center = Center()
            def replay_human_completions(self, limit): pass
            def claim_due(self, **kwargs): raise sqlite3.OperationalError('local injected storage exception')

        class Executor:
            def submit(self, *args): return future

        inflight = set()
        with self.assertRaises(sqlite3.OperationalError):
            module.worker_iteration(Worker(),'agent',2,inflight,None,None,0,None,0,Executor())
        self.assertEqual({future}, inflight)

    def test_runtime_lease_has_one_owner_and_expired_owner_cannot_renew_over_successor(self):
        with tempfile.TemporaryDirectory() as temp:
            tokens={'unit-token':{'project':'unit','max_severity':'critical'}}
            a=NotificationCenter(Path(temp)/'state.sqlite3',tokens)
            b=NotificationCenter(Path(temp)/'state.sqlite3',tokens)
            try:
                self.assertTrue(a.claim_runtime_lease('poller','a',now=100))
                self.assertFalse(b.claim_runtime_lease('poller','b',now=101))
                self.assertTrue(b.claim_runtime_lease('poller','b',now=146))
                self.assertFalse(a.claim_runtime_lease('poller','a',now=147))
            finally:
                a._connection.close();b._connection.close()


if __name__=='__main__':
    unittest.main()
