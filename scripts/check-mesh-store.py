#!/usr/bin/env python3
"""Focused integration: actual TLS Dqlite processes, HTTP core and crash failover.

Purpose: catch lost transactions, duplicate claims and replay of uncertain sends.
Expected60s; maximum180s including cluster setup/cleanup. No external providers.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tempfile
import shutil
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notification_center.core import NotificationCenter
from notification_center.http_api import build_handler
from notification_center.mesh_store import Connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    root = Path(args.work).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    # sockaddr_un has a108-byte limit; the authorized worktree path is longer.
    socket_root = Path(tempfile.mkdtemp(prefix="notice-mesh-"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem"),
                    "-days", "1", "-subj", "/CN=noticeplace-mesh",
                    "-addext", "subjectAltName=DNS:noticeplace-mesh,IP:127.0.0.1"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    (root / "key.pem").chmod(0o600)
    addresses = []
    for _ in range(3):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            addresses.append("127.0.0.1:" + str(sock.getsockname()[1]))
    children = []
    files = []
    connections = []
    servers = []

    def launch(index: int):
        state = root / str(index)
        state.mkdir(mode=0o700, exist_ok=True)
        log = (root / f"node-{index}.log").open("ab")
        files.append(log)
        command = [str(Path(args.binary).resolve()), "--state", str(state),
                   "--address", addresses[index], "--socket", str(socket_root / f"sql-{index}.sock"),
                   "--cert", str(root / "cert.pem"), "--key", str(root / "key.pem"),
                   "--ca", str(root / "cert.pem")]
        if index:
            command += ["--join", addresses[0]]
        child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True,
                                 env={**os.environ, "GOMAXPROCS": "2"})
        children.append(child)
        return child

    def until(operation, maximum=25):
        deadline = time.monotonic() + maximum
        last = None
        while time.monotonic() < deadline:
            try:
                result = operation()
                if result:
                    return result
            except (OSError, sqlite3.Error, AssertionError) as error:
                last = error
            time.sleep(0.15)
        raise RuntimeError(f"real cluster condition unavailable: {type(last).__name__}")

    try:
        for i in range(3):
            launch(i)
            until(lambda i=i: (socket_root / f"sql-{i}.sock").exists())
        for i in range(3):
            conn = Connection(str(socket_root / f"sql-{i}.sock"))
            connections.append(conn)
        assert connections[0].execute("SELECT voice_casefold(?) AS folded", ("АвТоСеЛЛер Straße",)).fetchone()["folded"] == "автоселлер strasse"
        membership = until(lambda: (r if len(r.get("cluster", [])) == 3 and
                                   all(n.get("Role") == 0 for n in r["cluster"]) else None)
                           if (r := connections[0].cluster()) else None)
        fixture = NotificationCenter(root / "frozen.sqlite3",
                   {"mesh-integration": {"project": "mesh-canary", "max_severity": "critical"}}, default_quiet_hours=[])
        imported = fixture.create_event("mesh-integration", "sqlite-preserved", {
            "schema": "notify.event.v1", "kind": "incident", "severity": "notice",
            "project": "mesh-canary", "recipient": "me", "title": "Preserved before migration",
            "body": "Real SQLite source retains the unknown send", "dedup_key": "sqlite-preserved"})
        source_claim = fixture.claim_due_deliveries(limit=1, channel_group="message")[0]
        assert fixture.reserve_delivery_send(source_claim["id"], source_claim["claimed_at"], source_claim["attempt"])
        fixture._connection.close()
        migration = [sys.executable, str(Path(__file__).with_name("migrate-mesh-store.py")),
                     "--source", str(root / "frozen.sqlite3"), "--socket", str(socket_root / "sql-0.sock"),
                     "--receipt", str(root / "migration.json")]
        subprocess.run(migration, check=True, stdout=subprocess.DEVNULL, timeout=45)
        # An exact explicit resume verifies canonical batch receipts and does
        # not add rows or repeat a delivery.
        subprocess.run(migration, check=True, stdout=subprocess.DEVNULL, timeout=45)
        centers = [NotificationCenter("mesh:" + str(socket_root / f"sql-{i}.sock"),
                   {"mesh-integration": {"project": "mesh-canary", "max_severity": "critical"}},
                   default_quiet_hours=[]) for i in range(3)]
        assert centers[2].get_incident(imported["incident_id"]) is not None
        assert centers[2]._connection.execute("SELECT status FROM deliveries WHERE id=?", (source_claim["id"],)).fetchone()["status"] == "sending"
        for center in centers:
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(center, "health-integration", mcp_token="mesh-integration-mcp"))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)

        def intake(index, key, body="Quorum-backed real HTTP test"):
            event = {"schema": "notify.event.v1", "kind": "incident", "severity": "notice",
                     "project": "mesh-canary", "recipient": "me", "title": "Mesh integration",
                     "body": body, "dedup_key": key}
            request = urllib.request.Request(f"http://127.0.0.1:{servers[index].server_port}/v1/events",
                      data=json.dumps(event).encode(), method="POST",
                      headers={"Authorization": "Bearer mesh-integration", "Idempotency-Key": key,
                               "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                assert response.status == 202
                return json.load(response)

        first = intake(0, "real-mesh-first")
        replay = intake(1, "real-mesh-first")
        assert first["event_id"] == replay["event_id"]
        assert centers[2].get_incident(first["incident_id"]) is not None
        readonly = Connection(str(socket_root / "sql-1.sock"), readonly=True)
        connections.append(readonly)
        with readonly:
            assert readonly.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"] > 0
        try:
            readonly.execute("DELETE FROM events")
            raise AssertionError("read-only caller unexpectedly modified durable state")
        except sqlite3.OperationalError:
            pass
        assert centers[0].claim_runtime_lease("poller-test", "node-0", now=100)
        assert not centers[1].claim_runtime_lease("poller-test", "node-1", now=101)
        assert centers[2].claim_runtime_lease("poller-test", "node-2", now=146)
        # A failed transaction must not leak a partial mutation to another node.
        try:
            with centers[0]._lock, centers[0]._connection:
                centers[0]._connection.execute("INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?)",
                                              ("mesh-rollback", "must-disappear", time.time()))
                raise ValueError("intentional local rollback")
        except ValueError:
            pass
        assert centers[1].get_runtime_setting("mesh-rollback") is None
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda center: center.claim_due_deliveries(limit=1, channel_group="message"), centers))
        claimed = [row for rows in claims for row in rows]
        assert len(claimed) == 1, "real concurrent nodes duplicated a claim"
        row = claimed[0]
        assert centers[0].reserve_delivery_send(row["id"], row["claimed_at"], row["attempt"])
        assert not centers[1].reserve_delivery_send(row["id"], row["claimed_at"], row["attempt"])
        leader = membership["leader"]
        failed_index = addresses.index(leader)
        children[failed_index].kill()
        children[failed_index].wait(timeout=5)
        survivor = next(i for i in range(3) if i != failed_index)
        for connection in connections:
            connection.close()
        for center in centers:
            center._connection.close()
        second = until(lambda: intake(survivor, "real-mesh-after-crash"), maximum=35)
        another = next(i for i in range(3) if i not in (failed_index, survivor))
        assert centers[another].get_incident(second["incident_id"]) is not None
        old = centers[another]._connection.execute("SELECT status FROM deliveries WHERE id=?", (row["id"],)).fetchone()
        assert old["status"] == "sending", "uncertain external reservation was lost"
        due = centers[another].claim_due_deliveries(time.time() + 120, limit=20, channel_group="message")
        assert row["id"] not in {item["id"] for item in due}, "sending action was replayed after node crash"
        receipt = {"status": "passed", "category": "focused_integration", "seconds": round(time.monotonic()-started, 3),
                   "real_processes": 3, "quorum_voters": 3, "real_http_intake": True,
                   "transaction_rollback": True, "exclusive_claim": True, "leader_crash": True,
                   "real_sqlite_migration_and_resume": True,
                   "unknown_send_not_replayed": True, "external_notification_or_phone_proof": False}
        (root / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for connection in connections:
            connection.close()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for file in files:
            file.close()
        shutil.rmtree(socket_root)


if __name__ == "__main__":
    main()
