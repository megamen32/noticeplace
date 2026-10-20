#!/usr/bin/env python3
"""Attempt a real disposable Dqlite snapshot restore without fake exporters.

Starts a disposable three-voter Notice source and the canonical pinned tool.
It imports the exported snapshot into a separate empty cluster using the
explicit snapshot-restore mode and verifies exact rows plus same-source resume
without any delivery worker/provider. Export or import failure is not a PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notification_center.core import NotificationCenter
from notification_center.mesh_store import Connection


def free_addresses(count: int) -> list[str]:
    result = []
    for _ in range(count):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            result.append("127.0.0.1:" + str(sock.getsockname()[1]))
    return result


def wait_for(operation, timeout: float = 25):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = operation()
            if value:
                return value
        except (OSError, sqlite3.Error, AssertionError) as error:
            last_error = error
        time.sleep(0.15)
    raise RuntimeError(f"disposable cluster did not become ready ({type(last_error).__name__})")


def start_cluster(name: str, root: Path, socket_root: Path, binary: Path,
                  addresses: list[str], cert: Path, key: Path, children: list,
                  logs: list):
    sockets = []
    for index, address in enumerate(addresses):
        state = root / str(index)
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        sql_socket = socket_root / f"{name[0]}{index}.sock"
        log = (root / f"node-{index}.log").open("ab")
        logs.append(log)
        command = [str(binary), "--state", str(state), "--address", address,
                   "--socket", str(sql_socket), "--cert", str(cert), "--key", str(key),
                   "--ca", str(cert)]
        if index:
            command += ["--join", addresses[0]]
        env = {**os.environ, "GOMAXPROCS": "2",
               "SNAP_INSTANCE_NAME": "npmbr-" + name + "-" + hashlib.sha256(str(root).encode()).hexdigest()[:8]}
        child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True, env=env)
        children.append(child)
        deadline = time.monotonic() + 25
        while not sql_socket.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                log.flush()
                details = Path(log.name).read_text(errors="replace")[-3000:]
                raise RuntimeError(f"disposable {name} node {index} exited {child.returncode}: {details}")
            time.sleep(0.15)
        if not sql_socket.exists():
            raise RuntimeError(f"disposable {name} node {index} did not create its SQL socket")
        sockets.append(sql_socket)
    connections = [Connection(str(path)) for path in sockets]
    membership = wait_for(lambda: (cluster if len(cluster.get("cluster", [])) == 3
                                   and all(node.get("Role") == 0 for node in cluster["cluster"])
                                   else None) if (cluster := connections[0].cluster()) else None)
    return sockets, connections, membership


def accepted_event(center: NotificationCenter, key: str) -> dict:
    return center.create_event("mesh-fixture", key, {
        "schema": "notify.event.v1", "kind": "incident", "severity": "notice",
        "project": "mesh-canary", "recipient": "local-fixture", "title": key,
        "body": "Disposable Dqlite backup restore fixture", "dedup_key": key,
    })


def table_digest(connection: Connection, exclude_tables: set[str] | None = None
                 ) -> tuple[str, dict[str, int], dict[str, str], dict[str, list[int]]]:
    excluded = exclude_tables or set()
    table_names = [row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        if row["name"] not in excluded]
    rows_by_table: dict[str, list[list]] = {}
    counts: dict[str, int] = {}
    rowids_by_table: dict[str, list[int]] = {}
    for table in table_names:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = connection.execute(f"SELECT rowid, * FROM {quoted} ORDER BY rowid").fetchall()
        values = [list(row) for row in rows]
        if any(isinstance(value, bytes) for row in values for value in row):
            raise RuntimeError(f"fixture table {table} unexpectedly contains a binary column")
        rows_by_table[table] = values
        counts[table] = len(values)
        rowids_by_table[table] = [int(row[0]) for row in values]
    encoded = json.dumps(rows_by_table, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    per_table = {
        table: hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        for table, rows in rows_by_table.items()
    }
    return hashlib.sha256(encoded).hexdigest(), counts, per_table, rowids_by_table


def restore_control_rows(connection: Connection, table: str) -> list[tuple]:
    quoted = '"' + table.replace('"', '""') + '"'
    rows = connection.execute(
        f"SELECT rowid,identity,digest,rows FROM {quoted} ORDER BY rowid").fetchall()
    return [tuple(row[key] for key in ("rowid", "identity", "digest", "rows")) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="source-pinned Notice mesh-store executable")
    parser.add_argument("--dqlite-utils", required=True, help="canonical dqlite-utils v0.2.2 executable")
    parser.add_argument("--work", required=True, help="new private disposable work directory; removed on exit")
    args = parser.parse_args()
    binary = Path(args.binary).resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("--binary must be an executable mesh-store file")
    work = Path(args.work).resolve()
    if work.exists():
        raise FileExistsError("--work must not already exist")
    work.mkdir(mode=0o700, parents=True)
    socket_root = Path(tempfile.mkdtemp(prefix="npmbr-"))
    started = time.monotonic()
    children = []
    logs = []
    source_connections = []
    restored_connections = []
    centers = []
    source_center = None
    try:
        cert, key = work / "cert.pem", work / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=noticeplace-mesh-backup-fixture",
                        "-addext", "subjectAltName=DNS:noticeplace-mesh,IP:127.0.0.1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        key.chmod(0o600)

        source_sockets, source_connections, _ = start_cluster(
            "source", work / "source", socket_root, binary, free_addresses(3), cert, key,
            children, logs)
        source = "mesh:" + str(source_sockets[0])
        source_center = NotificationCenter(source,
            {"mesh-fixture": {"project": "mesh-canary", "max_severity": "critical"}},
            default_quiet_hours=[])
        queued_event = accepted_event(source_center, "backup-queued")
        sending_event = accepted_event(source_center, "backup-sending")
        uncertain_event = accepted_event(source_center, "backup-uncertain")

        def delivery_for(incident_id: str):
            row = source_center._connection.execute(
                "SELECT id FROM deliveries WHERE incident_id=? ORDER BY id LIMIT 1", (incident_id,)).fetchone()
            assert row is not None
            return row["id"]

        def reserve(incident_id: str):
            delivery_id = delivery_for(incident_id)
            claimed = source_center.claim_due_deliveries(now_epoch=10**12, limit=1,
                channel_group="message", delivery_id=delivery_id)
            assert len(claimed) == 1
            assert source_center.reserve_delivery_send(delivery_id, claimed[0]["claimed_at"], claimed[0]["attempt"])
            return claimed[0]

        sending_delivery = reserve(sending_event["incident_id"])
        uncertain_delivery = reserve(uncertain_event["incident_id"])
        source_center.complete_delivery(uncertain_delivery["id"], "uncertain",
            error="disposable fixture records an ambiguous outcome", claimed_at=uncertain_delivery["claimed_at"],
            attempt=uncertain_delivery["attempt"], schedule_failure_neighbours=False)

        # events has no INTEGER PRIMARY KEY alias: create a hidden-rowid gap
        # while leaving its explicit event/idempotency identities untouched.
        source_center._connection.execute("UPDATE events SET rowid=rowid+1000 WHERE event_id=?",
                                          (sending_event["event_id"],))
        source_rowids = [row[0] for row in source_center._connection.execute(
            "SELECT rowid FROM events ORDER BY rowid").fetchall()]
        assert any(right - left > 1 for left, right in zip(source_rowids, source_rowids[1:]))

        # Model a source produced by earlier migrations/restores. These rows
        # are snapshot data; --restore-snapshot must preserve them and use a
        # separate SHA-derived control table for this restore operation.
        previous_restore_table = "mesh_restore_batches_" + "a" * 64
        source_center._connection.execute(
            "CREATE TABLE mesh_import_batches(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)")
        source_center._connection.execute(
            "INSERT INTO mesh_import_batches(rowid,identity,digest,rows) VALUES(17,?,?,?)",
            ("prior-migration:events:41", "prior-migration-digest", 1))
        source_center._connection.execute(
            f'CREATE TABLE "{previous_restore_table}"(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)')
        source_center._connection.execute(
            f'INSERT INTO "{previous_restore_table}"(rowid,identity,digest,rows) VALUES(29,?,?,?)',
            ("older-snapshot:events:41", "older-snapshot-digest", 1))

        expected_event_ids = {queued_event["event_id"], sending_event["event_id"], uncertain_event["event_id"]}
        expected_delivery_ids = {
            delivery_for(queued_event["incident_id"]): "queued",
            sending_delivery["id"]: "sending",
            uncertain_delivery["id"]: "uncertain",
        }
        before_digest, before_counts, before_tables, before_rowids = table_digest(source_center._connection)
        source_center._connection.close()
        source_center = None
        for connection in source_connections:
            connection.close()
        source_connections.clear()

        snapshot = work / "snapshot.db"
        manifest_path = work / "snapshot.json"
        backup = subprocess.run([sys.executable, str(Path(__file__).with_name("backup-mesh-store.py")),
            "--state-dir", str(work / "source" / "0"), "--output", str(snapshot),
            "--receipt", str(manifest_path), "--dqlite-utils", args.dqlite_utils],
            check=False, capture_output=True, text=True, timeout=300)
        if backup.returncode:
            raise RuntimeError(f"Dqlite snapshot export failed: {backup.stderr[-3000:]}")
        backup_result = json.loads(backup.stdout)
        manifest = json.loads(manifest_path.read_text())
        snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        assert snapshot_hash == manifest["snapshot_sha256"] == backup_result["snapshot_sha256"]
        restore_control_table = "mesh_restore_batches_" + snapshot_hash
        assert restore_control_table != previous_restore_table

        target_sockets, restored_connections, target_membership = start_cluster(
            "target", work / "target", socket_root, binary, free_addresses(3), cert, key,
            children, logs)
        migration_receipt = work / "restore-first.json"
        migration_cmd = [sys.executable, str(Path(__file__).with_name("migrate-mesh-store.py")),
            "--restore-snapshot", "--source", str(snapshot), "--socket", str(target_sockets[0]),
            "--receipt", str(migration_receipt)]
        imported = subprocess.run(migration_cmd, check=False, capture_output=True, text=True, timeout=120)
        if imported.returncode:
            raise RuntimeError(f"fresh-cluster snapshot import failed: {imported.stderr[-3000:]}")
        first_receipt = json.loads(migration_receipt.read_text())
        assert first_receipt["mode"] == "snapshot_restore"
        assert first_receipt["control_table"] == restore_control_table
        first_control_rows = [restore_control_rows(connection, restore_control_table)
                              for connection in restored_connections]
        assert first_control_rows and all(rows == first_control_rows[0] for rows in first_control_rows)
        assert any(row[1] == snapshot_hash + ":complete" for row in first_control_rows[0])
        resumed = subprocess.run([sys.executable, str(Path(__file__).with_name("migrate-mesh-store.py")),
            "--restore-snapshot", "--source", str(snapshot), "--socket", str(target_sockets[0]),
            "--receipt", str(work / "restore-resume.json")],
            check=False, capture_output=True, text=True, timeout=120)
        if resumed.returncode:
            raise RuntimeError(f"same-source importer resume failed: {resumed.stderr[-3000:]}")
        resume_receipt = json.loads((work / "restore-resume.json").read_text())
        assert first_receipt["source_sha256"] == resume_receipt["source_sha256"] == snapshot_hash
        assert first_receipt["mode"] == resume_receipt["mode"] == "snapshot_restore"
        assert first_receipt["control_table"] == resume_receipt["control_table"] == restore_control_table
        assert first_receipt["tables"] == resume_receipt["tables"]
        resumed_control_rows = [restore_control_rows(connection, restore_control_table)
                                for connection in restored_connections]
        assert resumed_control_rows == first_control_rows

        target_centers = [NotificationCenter("mesh:" + str(path),
            {"mesh-fixture": {"project": "mesh-canary", "max_severity": "critical"}},
            default_quiet_hours=[]) for path in target_sockets]
        centers.extend(target_centers)
        readbacks = [table_digest(center._connection, {restore_control_table}) for center in target_centers]
        for node_index, (digest, counts, tables, rowids) in enumerate(readbacks):
            if tables != before_tables:
                changed = sorted(table for table in set(before_tables) | set(tables)
                                 if before_tables.get(table) != tables.get(table))
                rowid_changes = {table: {"source": before_rowids.get(table), "target": rowids.get(table)}
                                 for table in changed if before_rowids.get(table) != rowids.get(table)}
                raise RuntimeError(
                    f"snapshot row/data identity changed on target voter {node_index}; "
                    f"tables={changed}; rowids={json.dumps(rowid_changes, sort_keys=True)}"
                )
            if digest != before_digest or counts != before_counts:
                raise RuntimeError(f"snapshot business table rows/counts changed on target voter {node_index}")
        events = target_centers[0]._connection.execute("SELECT event_id FROM events").fetchall()
        assert {row["event_id"] for row in events} == expected_event_ids
        statuses = {row["id"]: row["status"] for row in target_centers[0]._connection.execute(
            "SELECT id,status FROM deliveries").fetchall()}
        assert {delivery_id: statuses.get(delivery_id) for delivery_id in expected_delivery_ids} == expected_delivery_ids
        completed = target_centers[0]._connection.execute(
            f'SELECT identity FROM "{restore_control_table}" WHERE identity=?',
            (snapshot_hash + ":complete",)).fetchone()
        assert completed is not None
        historical_migrations = target_centers[0]._connection.execute(
            "SELECT rowid,identity,digest,rows FROM mesh_import_batches ORDER BY rowid").fetchall()
        assert [(row["rowid"], row["identity"], row["digest"], row["rows"])
                for row in historical_migrations] == [(17, "prior-migration:events:41", "prior-migration-digest", 1)]
        historical_restores = target_centers[0]._connection.execute(
            f'SELECT rowid,identity,digest,rows FROM "{previous_restore_table}" ORDER BY rowid').fetchall()
        assert [(row["rowid"], row["identity"], row["digest"], row["rows"])
                for row in historical_restores] == [(29, "older-snapshot:events:41", "older-snapshot-digest", 1)]

        result = {
            "status": "passed",
            "category": "disposable_dqlite_backup_restore",
            "seconds": round(time.monotonic() - started, 3),
            "dqlite_utils_version": manifest["dqlite_utils_version"],
            "source_cluster_voters": 3,
            "target_cluster_voters": len(target_membership["cluster"]),
            "source_database": manifest["database"],
            "snapshot_sha256": snapshot_hash,
            "source_tables_read_back": len(before_counts),
            "same_source_resume": True,
            "snapshot_restore_mode": True,
            "restore_control_table": restore_control_table,
            "restore_control_rows_identical_after_resume": True,
            "historical_import_ledgers_preserved": True,
            "accepted_events_preserved": len(expected_event_ids),
            "delivery_states_preserved": sorted(expected_delivery_ids.values()),
            "uncertain_effect_not_replayed": True,
            "external_provider_actions": 0,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        for center in centers:
            try:
                center._connection.close()
            except Exception:
                pass
        if source_center is not None:
            try:
                source_center._connection.close()
            except Exception:
                pass
        for connection in [*source_connections, *restored_connections]:
            try:
                connection.close()
            except Exception:
                pass
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for log in logs:
            log.close()
        shutil.rmtree(socket_root, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
