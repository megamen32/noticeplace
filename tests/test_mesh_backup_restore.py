"""Real-tool check for the disposable Dqlite backup and restore chain."""
from __future__ import annotations

import json
import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
MESH_STORE = os.environ.get("NOTICEPLACE_MESH_STORE_BINARY")
DQLITE_UTILS = os.environ.get("NOTICEPLACE_DQLITE_UTILS")


class BackupArtifactPublicationTests(unittest.TestCase):
    def test_generic_second_link_failure_removes_first_published_artifact(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "noticeplace_backup_mesh_store", REPO / "scripts" / "backup-mesh-store.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)

        with tempfile.TemporaryDirectory(prefix="noticeplace-artifact-publish-") as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot.tmp"
            manifest = root / "manifest.tmp"
            output = root / "snapshot.db"
            receipt = root / "snapshot.json"
            unrelated = root / "foreign.txt"
            snapshot.write_bytes(b"private snapshot fixture")
            manifest.write_bytes(b"private manifest fixture")
            unrelated.write_bytes(b"preserve this foreign file")
            real_link = os.link
            calls = 0

            def link_then_fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError(errno.EIO, "injected second hardlink failure")
                return real_link(source, destination)

            with patch.object(backup.os, "link", side_effect=link_then_fail_second):
                with self.assertRaises(OSError) as raised:
                    backup.publish_artifact_pair(snapshot, manifest, output, receipt)

            self.assertEqual(errno.EIO, raised.exception.errno)
            self.assertEqual(2, calls)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())
            self.assertTrue(snapshot.exists())
            self.assertTrue(manifest.exists())
            self.assertEqual(b"preserve this foreign file", unrelated.read_bytes())


@unittest.skipUnless(MESH_STORE and DQLITE_UTILS,
                     "set NOTICEPLACE_MESH_STORE_BINARY and NOTICEPLACE_DQLITE_UTILS for the real-tool integration")
class MeshBackupRestoreIntegration(unittest.TestCase):
    def test_real_tool_chain_and_historical_snapshot_restore_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="noticeplace-mesh-restore-") as tmp:
            work = Path(tmp) / "work"
            completed = subprocess.run(
                [sys.executable, str(REPO / "scripts/check-mesh-backup-restore.py"),
                 "--binary", str(Path(MESH_STORE).resolve()),
                 "--dqlite-utils", str(Path(DQLITE_UTILS).resolve()),
                 "--work", str(work)],
                check=False, capture_output=True, text=True, timeout=300,
            )
        self.assertTrue(completed.stdout.strip(),
                        f"restore checker returned {completed.returncode} without JSON; "
                        f"stderr={completed.stderr[-4000:]}")
        self.assertEqual(0, completed.returncode, completed.stderr[-4000:])
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"restore checker returned invalid JSON: {error}; "
                      f"stdout={completed.stdout[-4000:]}; stderr={completed.stderr[-4000:]}")
        self.assertEqual("passed", result["status"])
        self.assertEqual("0.2.2", result["dqlite_utils_version"])
        self.assertEqual(3, result["source_cluster_voters"])
        self.assertEqual(3, result["target_cluster_voters"])
        self.assertTrue(result["same_source_resume"])
        self.assertEqual(3, result["accepted_events_preserved"])
        self.assertEqual(["queued", "sending", "uncertain"], result["delivery_states_preserved"])
        self.assertTrue(result["snapshot_restore_mode"])
        self.assertTrue(result["historical_import_ledgers_preserved"])
        self.assertRegex(result["restore_control_table"], r"^mesh_restore_batches_[0-9a-f]{64}$")
        self.assertTrue(result["restore_control_rows_identical_after_resume"])
        self.assertTrue(result["uncertain_effect_not_replayed"])
        self.assertEqual(0, result["external_provider_actions"])


if __name__ == "__main__":
    unittest.main()
