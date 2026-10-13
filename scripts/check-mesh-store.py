#!/usr/bin/env python3
"""Focused integration: actual TLS Dqlite processes, HTTP core and crash failover.

Purpose: catch lost transactions, duplicate claims and replay of uncertain sends.
Expected60s; maximum180s including cluster setup/cleanup. No external providers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
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
    parser.add_argument("--voice-reader")
    parser.add_argument("--lease-helper")
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
                                 env={**os.environ, "GOMAXPROCS": "2",
                                      "SNAP_INSTANCE_NAME": "npmh-test-"+hashlib.sha256(str(root).encode()).hexdigest()[:12]})
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
        aliases = connections[0].execute("SELECT 1 AS id,2 AS id").fetchone()
        assert aliases[0] == 1 and aliases[1] == 2 and aliases['id'] == 1 and dict(aliases)['id'] == 1
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
        if args.lease_helper:
            helper = subprocess.Popen(['sudo','-n',sys.executable,args.lease_helper,
                '--source-root',str(Path(__file__).resolve().parents[1]),
                '--socket',str(socket_root/'sql-1.sock')],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            def lease_request(op,value=None):
                helper.stdin.write(json.dumps({'op':op,'value':value})+'\n')
                helper.stdin.flush()
                result=json.loads(helper.stdout.readline())
                assert result['ok']
                return result['value']
            lease_request('open')
            assert lease_request('read') is None
            lease_request('write',{'lease_id':'actual-lease-check','expires_at':time.time()+60})
            assert lease_request('read')['lease_id']=='actual-lease-check'
            lease_request('delete')
            lease_request('commit')
            helper.stdin.close()
            assert helper.wait(timeout=5)==0
            helper.stdout.close(); helper.stderr.close()
            assert centers[2].get_runtime_setting('s21-operator-lease') is None
        assert centers[2].get_incident(imported["incident_id"]) is not None
        # The actual phone-side receipt code writes only report state; this
        # scenario never enters its native dial or physical-phone boundary.
        os.environ['NOTIFY_CENTER_DB']='mesh:'+str(socket_root/'sql-0.sock')
        loader=SourceFileLoader('actual_mesh_phone_control',str(Path(__file__).resolve().parents[1]/'bin/notify-phone-mesh'))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        phone_module=importlib.util.module_from_spec(spec);loader.exec_module(phone_module)
        phone=phone_module.PhoneControl()
        receipt='67d136ca-dc71-4bb3-9369-d97e9e2a52dd'
        assert phone.sms_report({'id':receipt,'event':'submitted'})['ok']
        assert phone.sms_report({'id':receipt,'event':'delivered'})['ok']
        assert not phone.sms_report({'id':receipt,'event':'submitted'})['ok']
        assert centers[2]._connection.execute('SELECT event FROM gsm2sip_mesh_receipts WHERE id=?',(receipt,)).fetchone()['event']=='delivered'
        phone.center._connection.close()
        if args.voice_reader:
            spec = importlib.util.spec_from_file_location('actual_mesh_voice_reader', args.voice_reader)
            voice = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(voice)
            reader = voice.NoticePlaceVoiceStore('mesh:'+str(socket_root/'sql-2.sock'))
            result = reader.list_incidents('preserved')
            assert result['total_in_scope'] == 1 and result['incidents'][0]['id'] == imported['incident_id']
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
        # A caller sharing one IPC connection must never read another thread's
        # uncommitted transaction or steal that thread's protocol response.
        shared=centers[0]._connection
        entered,reader_started=threading.Event(),threading.Event()
        def rollback_thread():
            try:
                with shared:
                    shared.execute('INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?)',
                                   ('mesh-thread-rollback','uncommitted',time.time()))
                    entered.set()
                    assert reader_started.wait(2)
                    time.sleep(.05)
                    raise ValueError('intentional thread rollback')
            except ValueError:pass
        def concurrent_reader():
            assert entered.wait(2)
            reader_started.set()
            return shared.execute('SELECT value FROM runtime_settings WHERE key=?',('mesh-thread-rollback',)).fetchone()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            writer=pool.submit(rollback_thread)
            reader=pool.submit(concurrent_reader)
            writer.result(timeout=5)
            assert reader.result(timeout=5) is None, 'shared connection leaked a foreign uncommitted transaction'
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
