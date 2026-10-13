#!/usr/bin/env python3
"""Install this source-pinned real Dqlite service; do not activate API workers."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--binary-sha256', required=True)
    ap.add_argument('--library-sha256', required=True)
    ap.add_argument('--source', required=True)
    ap.add_argument('--host', required=True)
    ap.add_argument('--address', required=True)
    ap.add_argument('--join', default='')
    ap.add_argument('--certificates', required=True)
    ap.add_argument('--receipt', required=True)
    args = ap.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != args.host:
        raise RuntimeError('installation requires the exact owning host administrator')
    if len(args.source) != 40 or any(c not in '0123456789abcdef' for c in args.source):
        raise RuntimeError('published source identity required')
    bundle = Path(args.bundle).resolve(strict=True)
    binary = bundle / 'noticeplace-mesh-store'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != args.binary_sha256:
        raise RuntimeError('candidate executable does not match reviewed source artifact')
    if hashlib.sha256((bundle / 'lib/libdqlite.so.0').read_bytes()).hexdigest() != args.library_sha256:
        raise RuntimeError('candidate Dqlite library does not match the reviewed artifact')
    unit_path = Path('/etc/systemd/system/noticeplace-mesh-store.service')
    if unit_path.exists():
        raise RuntimeError('existing mesh store must be reconciled with its owner before changing it')
    if Path('/etc/noticeplace-mesh').exists() or Path('/var/lib/noticeplace-mesh').exists():
        raise RuntimeError('existing mesh configuration/state requires owner reconciliation')
    receipt = Path(args.receipt)
    receipt.write_text(json.dumps({'status':'intent','host':args.host,'source':args.source})+'\n')
    receipt.chmod(0o600)
    if not Path('/etc/noticeplace-mesh/ca.pem').exists():
        config = Path('/etc/noticeplace-mesh')
        config.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        owner = pwd.getpwnam('notification-center')
    except KeyError:
        run('useradd', '--system', '--home-dir', '/var/lib/notification-center', '--shell', '/usr/sbin/nologin', 'notification-center')
        owner = pwd.getpwnam('notification-center')
    target = Path('/opt/noticeplace-mesh/releases') / args.source
    target.mkdir(mode=0o755, parents=True, exist_ok=False)
    (target / 'lib').mkdir()
    for relative in ['noticeplace-mesh-store', 'lib/libdqlite.so.0', 'lib/DQLITE-LICENSE']:
        output = target / relative
        output.write_bytes((bundle / relative).read_bytes())
        output.chmod(0o755 if relative == 'noticeplace-mesh-store' else 0o644)
    config = Path('/etc/noticeplace-mesh')
    os.chown(config, owner.pw_uid, owner.pw_gid)
    for name in ['ca.pem', 'cert.pem', 'key.pem']:
        output = config / name
        output.write_bytes((Path(args.certificates) / name).read_bytes())
        output.chmod(0o400)
        os.chown(output, owner.pw_uid, owner.pw_gid)
    unit = f'''[Unit]
Description=Notice Place three-voter durable SQLite mesh
After=network-online.target
Wants=network-online.target

[Service]
User=notification-center
Group=notification-center
StateDirectory=noticeplace-mesh
StateDirectoryMode=0700
RuntimeDirectory=noticeplace-mesh
RuntimeDirectoryMode=0700
ExecStart={target}/noticeplace-mesh-store --state /var/lib/noticeplace-mesh --socket /run/noticeplace-mesh/sql.sock --address {args.address} --join {args.join or '""'} --ca /etc/noticeplace-mesh/ca.pem --cert /etc/noticeplace-mesh/cert.pem --key /etc/noticeplace-mesh/key.pem
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
Environment=GOMAXPROCS=2
# go-dqlite uses this documented instance namespace for its private internal
# Unix socket, preventing a bootstrap-ID collision with another local cluster.
Environment=SNAP_INSTANCE_NAME=noticeplace-mesh
CPUQuota=200%
# The frozen production import measured1.63GiB anonymous memory before its
# midpoint and126892 high-limit stalls, with zero OOMs and51GiB available.
# Keep a finite working set for the full database and its replicated VFS.
MemoryHigh=3G
MemoryMax=4G
MemorySwapMax=0
TasksMax=128
IOWeight=20
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/noticeplace-mesh /run/noticeplace-mesh

[Install]
WantedBy=multi-user.target
'''
    staged = Path('/tmp/noticeplace-mesh-store-candidate.service')
    staged.write_text(unit)
    run('systemd-analyze', 'verify', str(staged))
    unit_path.write_text(unit)
    unit_path.chmod(0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'noticeplace-mesh-store.service')
    properties = run('systemctl', 'show', 'noticeplace-mesh-store.service', '-p', 'MainPID', '-p', 'InvocationID', '-p', 'ActiveState').stdout
    result = {'status':'installed_store_only', 'host':args.host, 'source':args.source,
              'binary_sha256':args.binary_sha256, 'native_properties':properties, 'api_activated':False, 'migration_completed':False}
    receipt.write_text(json.dumps(result,indent=2)+'\n')
    receipt.chmod(0o600)
    print(json.dumps({'status':result['status'],'host':args.host}))


if __name__ == '__main__':
    main()
