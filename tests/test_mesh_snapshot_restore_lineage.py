from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "migrate-mesh-store.py"
spec = importlib.util.spec_from_file_location("migrate_mesh_store", SCRIPT)
migrate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(migrate)


class _DisposableSqlConnection:
    """SQLite-backed stand-in for the importer's SQL/batch client contract."""

    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self.connection.__exit__(exc_type, exc, tb)

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement, parameters)

    def batch(self, statements):
        with self.connection:
            cursor = None
            for statement, parameters in statements:
                cursor = self.connection.execute(statement, parameters)
        return cursor

    def close(self):
        self.connection.close()


class _LoseFirstBatchResponse(_DisposableSqlConnection):
    lost = False

    def batch(self, statements):
        result = super().batch(statements)
        if not type(self).lost:
            type(self).lost = True
            raise RuntimeError("simulated lost response after disposable batch commit")
        return result


def _snapshot(path: Path, *, suffix: str = "one") -> None:
    old_restore_table = "mesh_restore_batches_" + "a" * 64
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE events(event_key TEXT, payload TEXT NOT NULL)")
        db.execute("INSERT INTO events(rowid,event_key,payload) VALUES(41,?,?)", ("event-1", suffix))
        db.execute("CREATE TABLE mesh_import_batches(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)")
        db.execute("INSERT INTO mesh_import_batches(rowid,identity,digest,rows) VALUES(17,?,?,?)",
                   ("legacy-source:events:41", "legacy-digest", 1))
        db.execute(f'CREATE TABLE "{old_restore_table}"(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)')
        db.execute(f'INSERT INTO "{old_restore_table}"(rowid,identity,digest,rows) VALUES(29,?,?,?)',
                   ("older-snapshot:events:41", "older-digest", 1))


class MeshSnapshotRestoreLineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="notice-mesh-lineage-")
        self.root = Path(self.temp.name)
        self.source = self.root / "snapshot.sqlite3"
        _snapshot(self.source)
        self.target = self.root / "target.sqlite3"
        self.receipt = self.root / "receipt.json"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, source: Path | None = None, *, restore: bool,
             expected_error: str | None = None, connection_class=_DisposableSqlConnection):
        argv = ["migrate-mesh-store.py", "--source", str(source or self.source),
                "--socket", str(self.target), "--receipt", str(self.receipt)]
        if restore:
            argv.append("--restore-snapshot")
        output = io.StringIO()
        with mock.patch.object(migrate, "Connection", connection_class), \
             mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            if expected_error is None:
                migrate.main()
            else:
                with self.assertRaisesRegex((RuntimeError, SystemExit), expected_error):
                    migrate.main()
        return output.getvalue()

    def _target_rows(self, table: str):
        with sqlite3.connect(self.target) as db:
            return db.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid').fetchall()

    def test_snapshot_restore_preserves_legacy_and_earlier_ledgers_and_resumes_same_sha(self):
        self._run(restore=True)
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        control = f"mesh_restore_batches_{source_sha}"
        result = json.loads(self.receipt.read_text())
        self.assertEqual("snapshot_restore", result["mode"])
        self.assertEqual(control, result["control_table"])
        self.assertEqual(source_sha, result["source_sha256"])
        self.assertEqual([(17, "legacy-source:events:41", "legacy-digest", 1)],
                         self._target_rows("mesh_import_batches"))
        old_restore = "mesh_restore_batches_" + "a" * 64
        self.assertEqual([(29, "older-snapshot:events:41", "older-digest", 1)],
                         self._target_rows(old_restore))
        self.assertEqual([(41, "event-1", "one")], self._target_rows("events"))
        first_control_rows = self._target_rows(control)

        self._run(restore=True)
        self.assertEqual(first_control_rows, self._target_rows(control))
        self.assertEqual([(17, "legacy-source:events:41", "legacy-digest", 1)],
                         self._target_rows("mesh_import_batches"))
        self.assertEqual([(41, "event-1", "one")], self._target_rows("events"))

    def test_lost_batch_response_reconciles_only_by_same_frozen_sha(self):
        _LoseFirstBatchResponse.lost = False
        self._run(restore=True, expected_error="simulated lost response",
                  connection_class=_LoseFirstBatchResponse)
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        control = f"mesh_restore_batches_{source_sha}"
        committed = self._target_rows("events")
        self.assertEqual([(41, "event-1", "one")], committed)
        self.assertTrue(any(row[1] == f"{source_sha}:events:41" for row in self._target_rows(control)))

        self._run(restore=True)
        self.assertEqual(committed, self._target_rows("events"))
        self.assertEqual([(17, "legacy-source:events:41", "legacy-digest", 1)],
                         self._target_rows("mesh_import_batches"))

    def test_changed_snapshot_and_unrelated_target_are_denied_before_mixing(self):
        self._run(restore=True)
        before = {name: self._target_rows(name) for name in
                  ("events", "mesh_import_batches", "mesh_restore_batches_" + "a" * 64)}
        changed = self.root / "changed.sqlite3"
        _snapshot(changed, suffix="changed")
        self._run(changed, restore=True,
                  expected_error="another snapshot restore lineage")
        after = {name: self._target_rows(name) for name in before}
        self.assertEqual(before, after)

        unrelated_target = self.root / "unrelated.sqlite3"
        with sqlite3.connect(unrelated_target) as db:
            db.execute("CREATE TABLE events(event_key TEXT, payload TEXT NOT NULL)")
            db.execute("INSERT INTO events(rowid,event_key,payload) VALUES(73,'other-event','preserve')")
            db.execute("CREATE TABLE unrelated(value TEXT)")
            db.execute("INSERT INTO unrelated(rowid,value) VALUES(89,'preserve')")
        self.target = unrelated_target
        self._run(restore=True, expected_error="destination already contains data/control state")
        with sqlite3.connect(unrelated_target) as db:
            self.assertEqual([(73, "other-event", "preserve")],
                             db.execute("SELECT rowid,event_key,payload FROM events").fetchall())
            self.assertEqual([(89, "preserve")], db.execute("SELECT rowid,value FROM unrelated").fetchall())
            self.assertEqual([], db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'mesh_restore_batches_%'").fetchall())

    def test_source_cannot_contain_its_own_derived_control_namespace(self):
        fixed_sha = "b" * 64
        self.source.unlink()
        with sqlite3.connect(self.source) as db:
            db.execute(f'CREATE TABLE "mesh_restore_batches_{fixed_sha}"(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)')
            db.execute(f'INSERT INTO "mesh_restore_batches_{fixed_sha}"(rowid,identity,digest,rows) VALUES(11,?,?,?)',
                       ("old", "digest", 1))
        with mock.patch.object(migrate, "frozen_source_sha256", return_value=fixed_sha):
            self._run(restore=True, expected_error="contains its own snapshot restore control table")
        with sqlite3.connect(self.target) as db:
            self.assertEqual([], db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())

    def test_default_legacy_foreign_source_guard_is_unchanged(self):
        first = self.root / "legacy-one.sqlite3"
        second = self.root / "legacy-two.sqlite3"
        for path, value in ((first, "one"), (second, "two")):
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE events(value TEXT)")
                db.execute("INSERT INTO events(rowid,value) VALUES(5,?)", (value,))
        self._run(first, restore=False)
        self._run(second, restore=False, expected_error="another frozen source")
        with sqlite3.connect(self.target) as db:
            self.assertEqual([(5, "one")], db.execute("SELECT rowid,value FROM events").fetchall())
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM mesh_import_batches WHERE identity LIKE '%:complete'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
