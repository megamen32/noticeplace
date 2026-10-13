#!/usr/bin/env python3
"""Install the owned private phone, voice-routing and operator replicas."""
import argparse
import os
from pathlib import Path
import socket
import subprocess


def main():
    ap=argparse.ArgumentParser()
    for key in ('host','address','source','configuration'):
        ap.add_argument('--'+key,required=True)
    ap.add_argument('--nginx',default='/usr/sbin/nginx')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!=args.host:raise RuntimeError('exact host required')
    if Path('/opt/noticeplace/.source-revision').read_text().strip()!=args.source:raise RuntimeError('active source pointer differs')
    units=Path('/etc/systemd/system')
    inputs=Path(args.configuration)
    admin_port=8092 if args.host=='roomhacker-server-100' else 18092
    if args.host!='roomhacker-server-100':
        for name in ['notification-center-admin.env','health-incident-fleet.json']:
            target=Path('/etc')/name
            if target.exists():raise RuntimeError('existing operator configuration requires reconciliation')
            target.write_bytes((inputs/name).read_bytes());target.chmod(0o600)
        admin_config=Path('/etc/notification-center-admin.env')
        lines=[line for line in admin_config.read_text().splitlines() if not line.startswith('NOTIFY_ADMIN_PORT=')]
        admin_config.write_text('\n'.join(lines+['NOTIFY_ADMIN_PORT='+str(admin_port)])+'\n')
        admin_unit=Path('/opt/noticeplace/deploy/notification-center-admin.service').read_text()
        admin_unit=admin_unit.replace('[Service]', '[Service]\nEnvironment=NOTIFY_ADMIN_PORT='+str(admin_port))
        (units/'notification-center-admin.service').write_text(admin_unit)
        (units/'notification-center-admin-apply@.service').write_bytes(Path('/opt/noticeplace/deploy/notification-center-admin-apply@.service').read_bytes())
    phone=units/'noticeplace-phone-mesh.service'
    if phone.exists():raise RuntimeError('existing phone shim requires reconciliation')
    phone.write_text(f'''[Unit]
Description=NoticePlace private fenced phone control and durable SMS reports
After=network-online.target noticeplace-mesh-api.service agentcall-gatewayd.service asterisk.service
[Service]
User=root
Group=asterisk
EnvironmentFile=/etc/noticeplace-mesh-api.env
Environment=NOTICEPLACE_PHONE_MESH_ADDRESS={args.address}
Environment=AGENTCALL_PHONE_SOCKET=/run/agentcall/control.sock
RuntimeDirectory=noticeplace-phone
RuntimeDirectoryMode=0750
ExecStart=/usr/bin/python3 /opt/noticeplace/bin/notify-phone-mesh
Restart=on-failure
RestartSec=3
MemoryHigh=256M
MemoryMax=512M
MemorySwapMax=0
CPUQuota=100%
TasksMax=128
UMask=0077
[Install]
WantedBy=multi-user.target
''')
    config=Path('/etc/noticeplace-voice-route.conf')
    if config.exists():raise RuntimeError('existing speech route requires reconciliation')
    config.write_text('''pid /run/noticeplace-voice-route/nginx.pid;
error_log stderr warn;
worker_processes 1;
events { worker_connections 128; }
http {
  access_log off;
  client_body_temp_path /var/lib/noticeplace-voice-route/body;
  proxy_temp_path /var/lib/noticeplace-voice-route/proxy;
  fastcgi_temp_path /var/lib/noticeplace-voice-route/fastcgi;
  uwsgi_temp_path /var/lib/noticeplace-voice-route/uwsgi;
  scgi_temp_path /var/lib/noticeplace-voice-route/scgi;
  upstream noticeplace_stt {
    server 192.168.2.75:7653 max_fails=1 fail_timeout=5s;
    server 192.168.2.5:7653 backup;
  }
  server {
    listen 127.0.0.1:7654;
    client_max_body_size 20m;
    location / {
      proxy_pass http://noticeplace_stt;
      proxy_connect_timeout 2s;
      proxy_read_timeout 20s;
      proxy_next_upstream error timeout http_502 http_503 http_504 non_idempotent;
      proxy_next_upstream_tries 2;
    }
  }
}
''')
    route=units/'noticeplace-voice-route.service'
    route.write_text(f'''[Unit]
Description=Independent native speech recognition route for NoticePlace
After=network-online.target
[Service]
User=notification-center
Group=notification-center
RuntimeDirectory=noticeplace-voice-route
StateDirectory=noticeplace-voice-route
ExecStart={args.nginx} -c /etc/noticeplace-voice-route.conf -g "daemon off;"
Restart=on-failure
RestartSec=2
CPUQuota=25%
MemoryHigh=32M
MemoryMax=64M
MemorySwapMax=0
TasksMax=32
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/noticeplace-voice-route /run/noticeplace-voice-route
[Install]
WantedBy=multi-user.target
''')
    edge=Path('/etc/noticeplace-mesh-edge.conf')
    content=edge.read_text()
    if 'listen 127.0.0.1:11492 ssl;' not in content:
        content=content.replace('    listen '+args.address+':11492 ssl;', '    listen '+args.address+':11492 ssl;\n    listen 127.0.0.1:11492 ssl;')
    marker='    location / {'
    if content.count(marker)!=1:raise RuntimeError('private ingress requires reconciliation')
    admin_location='''    location ^~ /admin/ {
      proxy_pass http://127.0.0.1:8092;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Notify-Admin $http_x_notify_admin;
      proxy_set_header X-Forwarded-Proto https;
    }
'''.replace('127.0.0.1:8092','127.0.0.1:'+str(admin_port))
    content=content.replace(marker,admin_location+marker)
    edge.write_text(content)
    subprocess.run(['systemd-analyze','verify',str(phone),str(route)],check=True,timeout=20)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=20)
    subprocess.run(['systemctl','enable','--now',phone.name,route.name,'notification-center-admin.service'],check=True,timeout=30)
    subprocess.run(['systemctl','restart','noticeplace-mesh-edge.service'],check=True,timeout=20)
    print('Installed private phone, speech route and operator replica:',args.host)


if __name__=='__main__':main()
