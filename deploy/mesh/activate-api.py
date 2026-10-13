#!/usr/bin/env python3
"""Activate an owned staged replica after the frozen import is reconciled.

Preserve the original config files used by the operator console. Redirect the
old service name to the same mesh unit so a watchdog cannot revive SQLite.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import tarfile


def main():
    ap=argparse.ArgumentParser()
    for key in ('host','source','archive','sha256','configuration','receipt'):
        ap.add_argument('--'+key,required=True)
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!=args.host:raise RuntimeError('exact host administrator required')
    if len(args.source)!=40 or any(c not in '0123456789abcdef' for c in args.source):raise RuntimeError('published source required')
    receipt=Path(args.receipt)
    if receipt.exists():raise RuntimeError('existing activation receipt requires reconciliation')
    for name in ['notification-center.service','noticeplace-mesh-api.service']:
        if subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0:
            raise RuntimeError('API writers must be quiesced before activation')
    archive=Path(args.archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=args.sha256:raise RuntimeError('source archive differs')
    receipt.write_text(json.dumps({'status':'intent','source':args.source,'host':args.host})+'\n');receipt.chmod(0o600)
    release=Path('/opt/noticeplace-releases')/('mesh-'+args.source)
    release.mkdir(mode=0o755,parents=True,exist_ok=False)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if not (release/member.name).resolve().is_relative_to(release) or not (member.isfile() or member.isdir()):
                raise RuntimeError('unsupported source path')
        source.extractall(release)
    (release/'.source-revision').write_text(args.source+'\n')
    sys_source=str(release)
    import sys
    sys.path.insert(0,sys_source)
    from notification_center.mesh_store import Connection
    connection=Connection('/run/noticeplace-mesh/sql.sock')
    imported=connection.execute('SELECT identity FROM mesh_import_batches WHERE identity LIKE ? LIMIT 1',('%:complete',)).fetchone()
    connection.close()
    # The importer receipt and exact row readback are checked by the caller;
    # this gate also prevents accidentally initializing an empty business DB.
    if imported is None:
        raise RuntimeError('completed frozen migration is not recorded')
    inputs=Path(args.configuration)
    filenames=['notification-center.env','notification-center-telegram-routes.env','notification-center-agentcall-phone.env']
    keys=set()
    for name in filenames:
        data=(inputs/name).read_text()
        keys.update(line.split('=',1)[0].strip() for line in data.splitlines() if '=' in line and not line.lstrip().startswith('#'))
        target=Path('/etc')/name
        if name=='notification-center.env':
            if args.host=='roomhacker-server-100':data=target.read_text()
            data,changed=re.subn(r'^NOTIFY_CENTER_DB=.*$', 'NOTIFY_CENTER_DB=mesh:/run/noticeplace-mesh/sql.sock',data,flags=re.MULTILINE)
            if changed!=1:raise RuntimeError('canonical database configuration needs reconciliation')
            target.write_text(data);target.chmod(0o600)
        elif args.host!='roomhacker-server-100':
            target.write_text(data);target.chmod(0o600)
    env=json.loads((inputs/'effective-environment.json').read_text())
    runtime={'INVOCATION_ID','JOURNAL_STREAM','SYSTEMD_EXEC_PID','NOTIFY_SOCKET','LISTEN_PID','LISTEN_FDS','WATCHDOG_PID'}
    defaults={key:value for key,value in env.items() if key not in keys and key not in runtime}
    host_id={'roomhacker-server-100':'server-100','server-44':'server-44','roomhacker-server-88':'server-88'}[args.host]
    defaults.update(NOTIFY_CENTER_DB='mesh:/run/noticeplace-mesh/sql.sock',NOTIFY_CENTER_HOST='127.0.0.1',NOTIFY_CENTER_PORT='8091',
        NOTICEPLACE_MESH_HOSTS_JSON=json.dumps(['server-100','server-44','server-88']),NOTICEPLACE_MESH_HOST_ID=host_id,
        NOTICEPLACE_MESH_CORE_SOCKET='/run/noticeplace-mesh-core/core.sock',NOTICEPLACE_MESH_CA='/etc/noticeplace-mesh/ca.pem',
        NOTICEPLACE_MESH_CERT='/etc/noticeplace-mesh/cert.pem',NOTICEPLACE_MESH_KEY='/etc/noticeplace-mesh/key.pem',
        NOTICEPLACE_PHONE_PEERS_JSON=json.dumps({'server-100':{'address':'192.168.2.100','port':19094},'server-44':{'address':'192.168.2.5','port':19094},'server-88':{'address':'192.168.2.75','port':19094}}))
    if host_id!='server-100':defaults['NOTIFY_CENTER_AGENT_WORKERS']='0'
    lines=[]
    for key,value in sorted(defaults.items()):
        if '\n' in value or '\r' in value:raise RuntimeError('unsupported environment value')
        lines.append(key+'="'+value.replace('\\','\\\\').replace('"','\\"')+'"')
    config=Path('/etc/noticeplace-mesh-api.env');config.write_text('\n'.join(lines)+'\n');config.chmod(0o600)
    unit=Path('/etc/systemd/system/noticeplace-mesh-api.service')
    content=unit.read_text()
    content='\n'.join('WorkingDirectory='+str(release) if line.startswith('WorkingDirectory=') else
        'ExecStart=/usr/bin/python3 '+str(release/'bin/notify-center') if line.startswith('ExecStart=') else line for line in content.splitlines())+'\n'
    content=content.replace('EnvironmentFile=/etc/noticeplace-mesh-api.env','\n'.join('EnvironmentFile=/etc/'+name for name in filenames)+'\nEnvironmentFile=/etc/noticeplace-mesh-api.env')
    unit.write_text(content)
    alias=Path('/etc/systemd/system/notification-center.service')
    if alias.exists():
        backup=Path('/etc/systemd/system/notification-center.pre-mesh.service.saved')
        if backup.exists():raise RuntimeError('existing alias migration needs reconciliation')
        alias.rename(backup)
    alias.symlink_to(unit)
    pointer=Path('/opt/noticeplace')
    if pointer.exists() and not pointer.is_symlink():raise RuntimeError('existing source pointer is not a managed link')
    temporary=Path('/opt/noticeplace.mesh-candidate')
    temporary.symlink_to(release);os.replace(temporary,pointer)
    subprocess.run(['systemd-analyze','verify',str(unit)],check=True,timeout=20)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=20)
    subprocess.run(['systemctl','enable','--now',unit.name,'noticeplace-mesh-edge.service'],check=True,timeout=30)
    result={'status':'activated_api','source':args.source,'host':args.host,'archive_sha256':args.sha256,'legacy_name_uses_mesh':True}
    receipt=Path(args.receipt);receipt.write_text(json.dumps(result,indent=2)+'\n');receipt.chmod(0o600)
    print(json.dumps({'status':result['status'],'host':args.host}))


if __name__=='__main__':main()
