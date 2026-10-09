"""Exercise the upgrade entrypoint against a real disposable Git remote."""
import os
import subprocess
from pathlib import Path
import pytest

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy' / 'noticeplace'

def git(source, *args):
    return subprocess.check_output(['git','-C',str(source),*args], text=True, stderr=subprocess.DEVNULL).strip()

@pytest.fixture
def publication(tmp_path):
    source=tmp_path/'fixture'; source.mkdir()
    remote=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    git(source,'init','-b','main');git(source,'config','user.email','test@example.invalid');git(source,'config','user.name','Test')
    (source/'deploy').mkdir();script=source/'deploy'/'noticeplace';script.write_bytes(DEPLOY.read_bytes());script.chmod(0o755)
    for name in ['notification-center.service','notification-center-admin.service','notification-center-admin-apply@.service','notification-center-telegram-route.conf','notification-center-severity-policy.conf','notification-center.env.example','notification-center-admin.env.example','notification-center-telegram-routes.env']:
        (source/'deploy'/name).write_text('fixture\n')
    (source/'notification_center').mkdir();(source/'notification_center'/'runtime.py').write_text('approved = True\n')
    git(source,'add','.');git(source,'commit','-m','Approved runtime');sha=git(source,'rev-parse','HEAD')
    git(source,'remote','add','origin',str(remote));git(source,'push','origin','main')
    root=tmp_path/'root';link=root/'opt'/'noticeplace';link.parent.mkdir(parents=True);link.symlink_to('/previous-reviewed-release')
    env={**os.environ,'NOTICEPLACE_DESTDIR':str(root),'NOTICEPLACE_REQUIRE_PUBLISHED':'1','NOTICEPLACE_PUBLISHED_SHA':sha}
    env.pop('SUDO_USER',None)
    return source,remote,sha,root,env,script

def upgrade(fixture):
    source,_,_,_,env,script=fixture
    return subprocess.run([str(script),'upgrade','--no-start'],cwd=source,env=env,capture_output=True,text=True,timeout=15)

@pytest.mark.parametrize('refusal',['unpublished_sha','failed_verification','runtime_collision'])
def test_unpublished_upgrade_preserves_current_release(publication,refusal):
    source,remote,sha,root,env,_=publication
    if refusal=='failed_verification':env['NOTICEPLACE_PUBLICATION_REMOTE']=str(remote.parent/'missing.git')
    else:
        (source/'notification_center'/'runtime.py').write_text('unreviewed = True\n')
        git(source,'add','.');git(source,'commit','-m','New runtime')
        if refusal=='unpublished_sha':env['NOTICEPLACE_PUBLISHED_SHA']=git(source,'rev-parse','HEAD')
        else:git(source,'push','origin','main')
    result=upgrade(publication)
    assert result.returncode!=0
    assert 'upgrade was not started' in result.stderr
    assert os.readlink(root/'opt'/'noticeplace')=='/previous-reviewed-release'
    assert not (root/'opt'/'noticeplace-releases').exists()

def test_published_ancestor_with_unrelated_remote_advance_uses_approved_inputs(publication):
    source,_,sha,root,_,_=publication
    (source/'docs').mkdir();(source/'docs'/'unrelated.md').write_text('Another owner updated a document.\n')
    git(source,'add','.');git(source,'commit','-m','Unrelated documentation');git(source,'push','origin','main')
    assert git(source,'rev-parse','HEAD')!=sha
    # Shared WIP is preserved and never enters the approved release archive.
    (source/'notification_center'/'runtime.py').write_text('foreign_wip = True\n')
    result=upgrade(publication)
    assert result.returncode==0,result.stderr
    assert 'Verified published source: '+sha in result.stdout
    assert (root/'opt'/'noticeplace'/'notification_center'/'runtime.py').read_text()=='approved = True\n'
    assert (source/'notification_center'/'runtime.py').read_text()=='foreign_wip = True\n'
    assert not (root/'opt'/'noticeplace'/'docs'/'unrelated.md').exists()
