"""One exact published immutable replica activation; reconcile intent, never replay."""
import hashlib,json,os,socket,subprocess,sys,tarfile,time
from pathlib import Path
revision,archive_name,digest,expected_host=sys.argv[1:]
assert os.geteuid()==0 and socket.gethostname()==expected_host
assert len(revision)==40 and all(x in '0123456789abcdef' for x in revision)
archive=Path(archive_name);assert archive.stat().st_size<128*1024**2
assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest
pointer=Path('/opt/noticeplace');old=pointer.resolve();assert pointer.is_symlink()
unit=Path('/etc/systemd/system/noticeplace-mesh-api.service');assert unit.is_file()
sys.path.insert(0,str(old))
from notification_center.admin import AdminConfigStore
s=AdminConfigStore(Path('/etc/notification-center.env'),Path('/etc/notification-center-telegram-routes.env'),Path('/var/lib/notification-center-admin'))
c=s._consumer_notification_center()
assert not c._connection.execute("select id from deliveries where channel like '%.call' and status in ('claimed','sending') limit 1").fetchone(), 'active call; activation not attempted'
receipt=Path('/var/lib/notification-center')/('cleanup-upgrade-'+revision+'.json')
fd=os.open(receipt,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.write(fd,json.dumps({'status':'intent','previous':str(old),'source':revision}).encode());os.fsync(fd);os.close(fd)
new=old.parent/('cleanup-'+revision);new.mkdir(mode=0o755,exist_ok=False)
with tarfile.open(archive) as contents:
 members=contents.getmembers();assert sum(m.size for m in members)<128*1024**2
 for m in members:assert (new/m.name).resolve().is_relative_to(new) and (m.isfile() or m.isdir())
 contents.extractall(new)
(new/'.source-revision').write_text(revision+'\n')
for p in new.rglob('*'):
 p.chmod(0o755 if p.is_dir() or p.stat().st_mode&0o111 else 0o644)
dropin=unit.with_name(unit.name+'.d')/'98-managed-release.conf';dropin.parent.mkdir(exist_ok=True)
previous=dropin.read_text() if dropin.exists() else None
if previous is not None:(new/'previous-managed-release.conf').write_text(previous)
temporary=dropin.with_suffix('.candidate');temporary.write_text('[Service]\nWorkingDirectory='+str(new)+'\nExecStart=\nExecStart=/usr/bin/python3 '+str(new/'bin/notify-center')+'\n');temporary.chmod(0o644);os.replace(temporary,dropin)
temp_link=pointer.with_name('noticeplace.cleanup-candidate');temp_link.symlink_to(new);os.replace(temp_link,pointer)
admin_active=subprocess.run(['systemctl','is-active','--quiet','notification-center-admin']).returncode==0
subprocess.run(['systemctl','daemon-reload'],check=True,timeout=20)
subprocess.run(['systemctl','restart','noticeplace-mesh-api']+(['notification-center-admin'] if admin_active else []),check=True,timeout=30)
for _ in range(60):
    pid=int(subprocess.check_output(['systemctl','show','noticeplace-mesh-api','--value','-p','MainPID'],text=True))
    try: ready=pid>0 and str(new).encode() in Path('/proc',str(pid),'cmdline').read_bytes()
    except FileNotFoundError: ready=False
    if ready: break
    time.sleep(.5)
else: raise RuntimeError('Activation accepted; readiness unconfirmed, inspect receipt instead of replay')
r={'status':'activated','previous':str(old),'source':revision,'host':expected_host,'pid':pid,'archiveSHA256':digest,'adminRestarted':admin_active}
receipt.write_text(json.dumps(r)+'\n');print(json.dumps(r))
