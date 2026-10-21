#!/usr/bin/env python3
"""Stage a published NoticePlace replica; never import or start its workers.

The source archive and effective old environment are separate inputs. Secrets
stay in a root-only file and never appear in the installation receipt.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile


def main():
    parser=argparse.ArgumentParser()
    for key in ('source','archive','archive-sha256','environment','host','host-id','address','receipt'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--nginx',default='/usr/sbin/nginx')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!=args.host:
        raise RuntimeError('exact owning host required')
    if len(args.source)!=40 or any(c not in '0123456789abcdef' for c in args.source):
        raise RuntimeError('published revision required')
    archive=Path(args.archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=args.archive_sha256:
        raise RuntimeError('source archive identity differs')
    unit=Path('/etc/systemd/system/noticeplace-mesh-api.service')
    if unit.exists():raise RuntimeError('existing replica requires owner reconciliation')
    receipt=Path(args.receipt)
    receipt.write_text(json.dumps({'status':'intent','source':args.source,'host':args.host})+'\n')
    receipt.chmod(0o600)
    release=Path('/opt/noticeplace-releases')/('mesh-'+args.source)
    release.mkdir(mode=0o755,parents=True,exist_ok=False)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target=(release/member.name).resolve()
            if not target.is_relative_to(release) or not (member.isfile() or member.isdir()):
                raise RuntimeError('source archive contains an unsupported path')
        source.extractall(release)
    (release/'.source-revision').write_text(args.source+'\n')
    environment=json.loads(Path(args.environment).read_text())
    for key in ('INVOCATION_ID','JOURNAL_STREAM','SYSTEMD_EXEC_PID','NOTIFY_SOCKET','LISTEN_PID','LISTEN_FDS','LISTEN_FDNAMES','WATCHDOG_PID'):
        environment.pop(key,None)
    # Existing native investigation sessions belong to100 and must never be
    # silently recreated on another host. Standbys keep their durable jobs.
    if args.host_id!='server-100':environment['NOTIFY_CENTER_AGENT_WORKERS']='0'
    environment.update(NOTIFY_CENTER_DB='mesh:/run/noticeplace-mesh/sql.sock',
        NOTICEPLACE_MESH_HOSTS_JSON=json.dumps(['server-100','server-44','server-88']),
        NOTICEPLACE_MESH_HOST_ID=args.host_id,
        NOTICEPLACE_MESH_CORE_SOCKET='/run/noticeplace-mesh-core/core.sock',
        NOTICEPLACE_MESH_CA='/etc/noticeplace-mesh/ca.pem',
        NOTICEPLACE_MESH_CERT='/etc/noticeplace-mesh/cert.pem',
        NOTICEPLACE_MESH_KEY='/etc/noticeplace-mesh/key.pem',
        NOTICEPLACE_PHONE_PEERS_JSON=json.dumps({
          'server-100':{'address':'192.168.2.100','port':19094},
          'server-44':{'address':'192.168.2.5','port':19094},
          'server-88':{'address':'192.168.2.75','port':19094}}),
        NOTIFY_CENTER_HOST='127.0.0.1',NOTIFY_CENTER_PORT='8091')
    lines=[]
    for key,value in sorted(environment.items()):
        if not key.replace('_','').isalnum() or '\n' in value or '\r' in value:
            raise RuntimeError('unsupported environment field')
        escaped=value.replace('\\','\\\\').replace('"','\\"')
        lines.append(key+'="'+escaped+'"')
    config=Path('/etc/noticeplace-mesh-api.env')
    config.write_text('\n'.join(lines)+'\n');config.chmod(0o600)
    core='/opt/mesh-core/releases/dd93b21e8791780013ef560e7e61944d06533f62/mesh-core-service'
    if not Path(core).is_file():raise RuntimeError('installed shared Rust core is missing')
    core_unit=Path('/etc/systemd/system/noticeplace-mesh-core.service')
    if core_unit.exists():raise RuntimeError('existing product core requires owner reconciliation')
    core_unit.write_text(f'''[Unit]
Description=Shared Rust mesh core for NoticePlace service identity
After=local-fs.target
[Service]
User=notification-center
Group=notification-center
RuntimeDirectory=noticeplace-mesh-core
RuntimeDirectoryMode=0700
UMask=0077
ExecStart={core} --socket /run/noticeplace-mesh-core/core.sock
Restart=on-failure
RestartSec=2
CPUQuota=25%
MemoryHigh=32M
MemoryMax=64M
MemorySwapMax=0
TasksMax=16
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
''')
    unit.write_text(f'''[Unit]
Description=NoticePlace quorum-backed API and delivery workers
After=network-online.target noticeplace-mesh-store.service noticeplace-mesh-core.service
Requires=noticeplace-mesh-store.service noticeplace-mesh-core.service
[Service]
User=notification-center
Group=notification-center
EnvironmentFile=/etc/noticeplace-mesh-api.env
WorkingDirectory={release}
ExecStart=/usr/bin/python3 {release}/bin/notify-center
Restart=on-failure
RestartSec=3
KillMode=mixed
KillSignal=SIGINT
TimeoutStopSec=infinity
StateDirectory=notification-center
MemoryHigh=512M
MemoryMax=1G
MemorySwapMax=256M
CPUQuota=200%
TasksMax=256
IOWeight=50
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/notification-center
[Install]
WantedBy=multi-user.target
''')
    subprocess.run(['systemd-analyze','verify',str(unit),str(core_unit)],check=True,timeout=20)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=20)
    subprocess.run(['systemctl','enable','--now',core_unit.name],check=True,timeout=20)
    edge=Path('/etc/noticeplace-mesh-edge.conf')
    if edge.exists():raise RuntimeError('existing edge requires owner reconciliation')
    edge.write_text(f'''pid /run/noticeplace-mesh-edge/nginx.pid;
error_log stderr warn;
worker_processes 1;
events {{ worker_connections 128; }}
http {{
  access_log off;
  client_body_temp_path /var/lib/noticeplace-mesh-edge/body;
  proxy_temp_path /var/lib/noticeplace-mesh-edge/proxy;
  fastcgi_temp_path /var/lib/noticeplace-mesh-edge/fastcgi;
  uwsgi_temp_path /var/lib/noticeplace-mesh-edge/uwsgi;
  scgi_temp_path /var/lib/noticeplace-mesh-edge/scgi;
  server {{
    listen {args.address}:11492 ssl;
    listen 127.0.0.1:11492 ssl;
    ssl_certificate /etc/noticeplace-mesh/cert.pem;
    ssl_certificate_key /etc/noticeplace-mesh/key.pem;
    ssl_client_certificate /etc/noticeplace-mesh/ca.pem;
    ssl_verify_client on;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 2m;
    location / {{
      proxy_pass http://127.0.0.1:8091;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_buffering off;
      proxy_connect_timeout 2s;
      proxy_read_timeout 180s;
    }}
  }}
}}
''')
    edge_unit=Path('/etc/systemd/system/noticeplace-mesh-edge.service')
    if not Path(args.nginx).is_file():raise RuntimeError('verified native nginx binary is missing')
    edge_unit.write_text(f'''[Unit]
Description=Mutually authenticated NoticePlace private ingress
After=network-online.target noticeplace-mesh-api.service
[Service]
User=notification-center
Group=notification-center
RuntimeDirectory=noticeplace-mesh-edge
StateDirectory=noticeplace-mesh-edge
ExecStart={args.nginx} -c /etc/noticeplace-mesh-edge.conf -g "daemon off;"
Restart=on-failure
RestartSec=2
MemoryHigh=64M
MemoryMax=128M
MemorySwapMax=0
CPUQuota=100%
TasksMax=64
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/noticeplace-mesh-edge /run/noticeplace-mesh-edge
[Install]
WantedBy=multi-user.target
''')
    subprocess.run(['systemd-analyze','verify',str(edge_unit)],check=True,timeout=20)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=20)
    result={'status':'staged_api_not_activated','source':args.source,'host':args.host,
            'release':str(release),'archive_sha256':args.archive_sha256,'migration_completed':False}
    receipt.write_text(json.dumps(result,indent=2)+'\n');receipt.chmod(0o600)
    print(json.dumps({'status':result['status'],'host':args.host}))


if __name__=='__main__':main()
