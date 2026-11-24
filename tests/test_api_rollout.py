import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from notification_center import api_rollout
from notification_center.api_rollout import Api, Queue, Release, tick


HOSTS = ['primary', 'standby-a', 'standby-b']
OLD = Api('0.1.19', 'a' * 40, '/before')
TARGET = Release('0.1.20', 'b' * 40)
UPDATED = Api(TARGET.version, TARGET.revision, '/after')


@pytest.mark.parametrize(('redirect', 'valid'), [
    ('https://github.com/megamen32/noticeplace/releases/tag/v999.1.21', True),
    ('https://github.com/megamen32/noticeplace/releases', False),
    ('https://github.com/megamen32/noticeplace/releases/tag/v999.1.21-rc.1', False),
])
def test_discovery_uses_github_stable_release_redirect(monkeypatch, redirect, valid):
    latest = 'https://github.com/megamen32/noticeplace/releases/latest'
    tag = 'refs/tags/v999.1.21'
    revision = 'c' * 40

    def get_latest(url, *, timeout):
        assert url == latest
        return nullcontext(SimpleNamespace(geturl=lambda: redirect))

    def resolve_tag(*args):
        assert args[-2:] == (tag, tag + '^{}')
        return f'{revision}\t{tag}'

    monkeypatch.setattr(api_rollout.urllib.request, 'urlopen', get_latest)
    monkeypatch.setattr(api_rollout, 'command', resolve_tag)

    if not valid:
        with pytest.raises(ValueError, match='stable published GitHub release'):
            api_rollout.latest_release()
    else:
        assert api_rollout.latest_release() == Release('999.1.21', revision)


def shared_queue(tmp_path):
    db = sqlite3.connect(tmp_path / "mesh.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE runtime_leases(name TEXT PRIMARY KEY,holder TEXT,expires_at REAL)")
    queue = Queue(db, HOSTS)
    queue.initialize()
    with db:
        for host in HOSTS:
            queue.observe(host, OLD)
    return queue


def test_wrong_host_cannot_confirm_a_reserved_step(tmp_path):
    queue = shared_queue(tmp_path)
    queue.discover('primary:owner', lambda: TARGET)
    assert queue.reserve(queue.plan(), 'standby-a', OLD)

    assert not queue.finish(queue.plan(), 'standby-b', UPDATED)
    assert queue.plan()['state'] == 'applying'
    assert queue.plan()['step'] == 0


def test_discovery_owner_expires_and_queue_survives(tmp_path):
    queue = shared_queue(tmp_path)
    calls = []
    queue.discover('primary:owner', lambda: calls.append('primary') or TARGET)
    queue.discover('standby-b:owner', lambda: calls.append('standby-b') or TARGET)
    assert calls == ['primary']
    start = queue.plan()

    with queue.db:
        queue.db.execute("UPDATE runtime_leases SET expires_at=0")
    queue.discover('standby-b:owner', lambda: Release('0.1.21', 'c' * 40))
    assert queue.db.execute("SELECT holder FROM runtime_leases").fetchone()[0] == 'standby-b:owner'
    assert queue.plan() == start

    assert queue.reserve(start, 'standby-a', OLD)
    assert queue.finish(queue.plan(), 'standby-a', UPDATED)
    assert queue.plan()['step'] == 1


def test_interrupted_step_requires_exact_target_before_advancing(tmp_path, monkeypatch):
    queue = shared_queue(tmp_path)
    monkeypatch.setattr(api_rollout, 'WORK', tmp_path)
    queue.discover('primary:owner', lambda: TARGET)
    assert queue.reserve(queue.plan(), 'standby-a', OLD)
    calls = []

    def process(host, observed):
        tick(queue, host, inspect=lambda: observed, quorum=lambda db: None,
             prepare=lambda *args: None, install=lambda *args: calls.append(host))

    process('standby-b', OLD)
    process('standby-a', Api(TARGET.version, 'e' * 40, '/wrong'))
    assert calls == []
    assert queue.plan()['state'] == 'applying'
    process('standby-a', UPDATED)
    assert queue.plan()['step'] == 1
    assert queue.plan()['state'] == 'ready'


def test_stale_peer_prevents_reservation(tmp_path):
    queue = shared_queue(tmp_path)
    queue.discover('primary:owner', lambda: TARGET)

    with queue.db:
        queue.db.execute("UPDATE api_rollout_peers SET seen_at=0 WHERE host='standby-b'")
    assert not queue.reserve(queue.plan(), 'standby-a', OLD)
    assert queue.plan()['state'] == 'ready'


def test_discovery_refuses_a_release_that_reuses_or_predates_an_installed_version(tmp_path):
    """A published version may not be reused and may not go backwards.

    A node that runs one version at another revision is exactly the state a
    manual deployment of an unreleased commit creates.  The rollout refuses to
    discover such a release instead of rolling the fleet onto it.
    """
    queue = shared_queue(tmp_path)
    reused = Release('0.1.19', 'd' * 40)
    with pytest.raises(RuntimeError, match='version was reused'):
        queue.discover('primary:owner', lambda: reused)
    older = Release('0.1.18', 'e' * 40)
    with pytest.raises(RuntimeError, match='older than an installed API'):
        queue.discover('primary:owner', lambda: older)
    assert queue.plan() is None
