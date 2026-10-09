"""A failed publication must stop the real upgrade entrypoint before mutation."""
import os
import subprocess
from pathlib import Path
import pytest

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy' / 'noticeplace'

@pytest.mark.parametrize('refusal', ['push_rejected', 'remote_unreachable', 'agreed_sha_mismatch'])
def test_unpublished_upgrade_preserves_current_release(tmp_path, refusal):
    root = tmp_path / 'root'
    current = root / 'opt' / 'noticeplace'
    current.parent.mkdir(parents=True)
    current.symlink_to('/previous-reviewed-release')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    fake_git = binaries / 'git'
    head = 'a' * 40
    remote = 'b' * 40 if refusal == 'push_rejected' else head
    fake_git.write_text('#!/bin/sh\ncase "$*" in\n*rev-parse*) echo '+head+';;\n*ls-remote*) '+('exit 128' if refusal == 'remote_unreachable' else 'echo "'+remote+' refs/heads/main"')+';;\n*) exit 99;;\nesac\n')
    fake_git.chmod(0o755)
    env = {**os.environ, 'PATH': str(binaries)+':'+os.environ['PATH'], 'NOTICEPLACE_DESTDIR':str(root), 'NOTICEPLACE_REQUIRE_PUBLISHED':'1', 'NOTICEPLACE_PUBLISHED_SHA':('c'*40 if refusal=='agreed_sha_mismatch' else head)}
    env.pop('SUDO_USER', None)
    result = subprocess.run([str(DEPLOY), 'upgrade', '--no-start'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'upgrade was not started' in result.stderr
    assert os.readlink(current) == '/previous-reviewed-release'
    assert not (root / 'opt' / 'noticeplace-releases').exists()
