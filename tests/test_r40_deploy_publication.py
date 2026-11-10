"""Exercise the upgrade entrypoint against a real disposable Git remote."""
import os
import json
import re
import subprocess
from pathlib import Path
import pytest

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy' / 'noticeplace'
TELEGRAM_ROUTE = Path(__file__).resolve().parents[1] / 'deploy' / 'notification-center-telegram-route.conf'


def _bare_service_keys(text):
    """Return assignment keys that systemd would reject in a Service section."""
    service = False
    keys = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('[') and line.endswith(']'):
            service = line == '[Service]'
            continue
        if service and line and not line.startswith('#') and '=' in line and not line.startswith(('Environment=', 'EnvironmentFile=')):
            keys.append(line.split('=', 1)[0])
    return tuple(keys)


def test_telegram_route_dropin_uses_environment_directives():
    text = TELEGRAM_ROUTE.read_text(encoding='utf-8')
    expected = ('TELEGRAM_ACTIVE_MODES_JSON', 'TELEGRAM_AUTO_CREATE_TOPICS', 'TELEGRAM_SEVERITY_ROUTES_JSON')
    assert _bare_service_keys(text) == ()
    # Keep route values in the operator-owned EnvironmentFile only. A JSON
    # value in Environment= is parsed by systemd's unit lexer, which can strip
    # the JSON quotes before the application receives it.
    assert 'EnvironmentFile=/etc/notification-center-telegram-routes.env' in text
    assert not any(re.search(rf'(?m)^Environment={re.escape(key)}=', text) for key in expected)
    env_path = Path(__file__).resolve().parents[1] / 'deploy' / 'notification-center-telegram-routes.env'
    env = {}
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            env[key] = value
    assert json.loads(env['TELEGRAM_ACTIVE_MODES_JSON']) == ['decisions', 'notify']
    assert env['TELEGRAM_AUTO_CREATE_TOPICS'] == 'false'
    assert json.loads(env['TELEGRAM_SEVERITY_ROUTES_JSON']) == {
        'decisions': {'chat_id': '-1004419894781'},
        'notify': {'chat_id': '-1004450539053'},
        'log': {'chat_id': '-1004322359393', 'message_thread_id': 122},
    }

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

def test_upgrade_preserves_existing_operator_telegram_routes_env(publication):
    _,_,_,root,_,_=publication
    routes=root/'etc'/'notification-center-telegram-routes.env'
    routes.parent.mkdir(parents=True,exist_ok=True)
    operator_config=(
        '# operator-owned customized route policy\n'
        'TELEGRAM_ACTIVE_MODES_JSON=["decisions"]\n'
        'TELEGRAM_AUTO_CREATE_TOPICS=true\n'
        'OPERATOR_ROUTE_NOTE=keep-this-setting\n'
    )
    routes.write_text(operator_config,encoding='utf-8')
    routes.chmod(0o600)

    result=upgrade(publication)

    assert result.returncode==0,result.stderr
    assert routes.read_text(encoding='utf-8')==operator_config
    assert routes.stat().st_mode & 0o777 == 0o600
