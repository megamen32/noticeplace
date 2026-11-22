"""Coordinate one native API activation at a time through Dqlite."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import closing
from dataclasses import dataclass

from .mesh_store import connect
from .runtime_lease import claim

REPOSITORY = "https://github.com/megamen32/noticeplace.git"
LATEST = "https://api.github.com/repos/megamen32/noticeplace/releases/latest"
DISCOVERY_LEASE = "noticeplace-api-release-discovery"
WORK = Path("/var/lib/noticeplace-api-rollout")
PEER_TTL = 90
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def version(value):
    if not VERSION.fullmatch(value):
        raise ValueError("invalid API release version")
    return tuple(map(int, value.split(".")))


@dataclass(frozen=True)
class Api:
    version: str
    revision: str
    path: str


@dataclass(frozen=True)
class Release:
    version: str
    revision: str

    def matches(self, api):
        return self.version == api.version and self.revision == api.revision


class Queue:
    def __init__(self, db, hosts):
        self.db = db
        self.hosts = hosts

    def initialize(self):
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS api_rollout_peers (
                host TEXT PRIMARY KEY, version TEXT NOT NULL, revision TEXT NOT NULL,
                path TEXT NOT NULL, seen_at INTEGER NOT NULL
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS api_rollout_plan (
                id INTEGER PRIMARY KEY CHECK (id=1), version TEXT NOT NULL, revision TEXT NOT NULL,
                hosts TEXT NOT NULL, step INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('ready','applying','blocked','done')),
                previous_path TEXT, error TEXT
            )""")

    def now(self):
        return self.db.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]

    def observe(self, host, api):
        self.db.execute("""INSERT INTO api_rollout_peers(host,version,revision,path,seen_at)
            VALUES (?,?,?,?,CAST(strftime('%s','now') AS INTEGER))
            ON CONFLICT(host) DO UPDATE SET version=excluded.version,revision=excluded.revision,
                path=excluded.path,seen_at=excluded.seen_at""",
            (host, api.version, api.revision, api.path))

    def fresh(self):
        before = self.now() - PEER_TTL
        rows = {row['host']: Api(row['version'], row['revision'], row['path'])
                for row in self.db.execute("SELECT * FROM api_rollout_peers WHERE seen_at>=?", (before,))}
        return rows if set(rows) == set(self.hosts) else None

    def plan(self):
        row = self.db.execute("SELECT * FROM api_rollout_plan WHERE id=1").fetchone()
        return dict(row) if row is not None else None

    def discover(self, holder, lookup):
        with self.db:
            if not self.fresh() or not claim(self.db, DISCOVERY_LEASE, holder, self.now(), 120):
                return
        target = lookup()
        with self.db:
            lease = self.db.execute("SELECT holder,expires_at FROM runtime_leases WHERE name=?",
                                    (DISCOVERY_LEASE,)).fetchone()
            if not lease or lease['holder'] != holder or lease['expires_at'] <= self.now():
                return
            plan = self.plan()
            if plan and (plan['state'] != 'done' or version(target.version) <= version(plan['version'])):
                return
            peers = self.fresh()
            if not peers or all(target.matches(api) for api in peers.values()):
                return
            if any(version(api.version) > version(target.version)
                   or (version(api.version) == version(target.version) and not target.matches(api))
                   for api in peers.values()):
                raise RuntimeError("published release is older than an installed API, or its version was reused")
            order = self.hosts[1:] + self.hosts[:1]
            self.db.execute("""INSERT INTO api_rollout_plan VALUES (1,?,?,?,0,'ready',NULL,NULL)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version,revision=excluded.revision,
                    hosts=excluded.hosts,step=0,state='ready',previous_path=NULL,error=NULL""",
                (target.version, target.revision, json.dumps(order)))

    def reserve(self, plan, host, api):
        if json.loads(plan['hosts'])[plan['step']] != host:
            return False
        with self.db:
            self.observe(host, api)
            if not self.fresh():
                return False
            cursor = self.db.execute("""UPDATE api_rollout_plan SET state='applying',previous_path=?
                WHERE id=1 AND revision=? AND step=? AND state='ready'""",
                (api.path, plan['revision'], plan['step']))
            reserved = cursor.rowcount == 1
        # The context manager commits before the caller can start the installer.
        return reserved

    def finish(self, plan, host, api):
        target = Release(plan['version'], plan['revision'])
        if json.loads(plan['hosts'])[plan['step']] != host or not target.matches(api):
            return False
        step = plan['step'] + 1
        state = 'done' if step == len(self.hosts) else 'ready'
        with self.db:
            self.observe(host, api)
            cursor = self.db.execute("""UPDATE api_rollout_plan SET step=?,state=?,previous_path=NULL,error=NULL
                WHERE id=1 AND revision=? AND step=? AND state IN ('ready','applying','blocked')""",
                (step, state, plan['revision'], plan['step']))
            advanced = cursor.rowcount == 1
        return advanced

    def block(self, plan, error):
        with self.db:
            self.db.execute("""UPDATE api_rollout_plan SET state='blocked',error=?
                WHERE id=1 AND revision=? AND step=? AND state='applying'""",
                (error, plan['revision'], plan['step']))


def check_quorum(db):
    cluster = db.cluster()
    voters = {row['Address'] for row in cluster['cluster'] if row['Role'] == 0}
    if len(voters) != 3 or cluster.get('leader') not in voters:
        raise RuntimeError("expected three Dqlite voters and a leader")
    if db.execute("SELECT 1").fetchone()[0] != 1:
        raise RuntimeError("Dqlite readback failed")


def command(*args, timeout=20):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def latest_release():
    request = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        release = json.load(response)
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not tag.startswith('v'):
        raise ValueError("expected a stable published GitHub release")
    bootstrap = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
    if version(tag[1:]) < version(bootstrap):
        raise ValueError("published API release predates this rollout controller")
    ref = 'refs/tags/' + tag
    refs = dict((name, sha) for sha, name in
                (row.split() for row in command("git", "ls-remote", "--tags", REPOSITORY, ref, ref + '^{}').splitlines()))
    revision = refs.get(ref + '^{}', refs.get(ref, ''))
    if not SHA.fullmatch(revision):
        raise ValueError("published release tag could not be resolved")
    return Release(tag[1:], revision)


def local_api():
    alias = Path("/etc/systemd/system/notification-center.service")
    if not alias.is_symlink() or alias.resolve().name != "noticeplace-mesh-api.service":
        raise RuntimeError("expected the native mesh API unit")
    path = Path("/opt/noticeplace").resolve(strict=True)
    if path.parent != Path("/opt/noticeplace-releases"):
        raise RuntimeError("API is not using a managed release")
    pid = int(command("systemctl", "show", "noticeplace-mesh-api", "-p", "MainPID", "--value"))
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b'\0')
    if len(argv) < 2 or argv[1] != os.fsencode(path / "bin/notify-center"):
        raise RuntimeError("running API does not match the installed release")
    metadata = dict(line.split('=', 1) for line in
                    (path / ".noticeplace-release").read_text().splitlines() if '=' in line)
    short = path.name.split('-')[1]
    revision = metadata.get('source_revision') or short
    if not re.fullmatch(r"[0-9a-f]{12}(?:[0-9a-f]{28})?", revision) or not revision.startswith(short):
        raise RuntimeError("installed API revision is invalid")
    release_version = (path / "VERSION").read_text().strip()
    version(release_version)
    with urllib.request.urlopen("http://127.0.0.1:8091/v1/instructions", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("local API is not ready")
    return Api(release_version, revision, str(path))


def stage(target, path):
    command("git", "clone", "--quiet", "--no-tags", "--single-branch", "--branch", "main",
            REPOSITORY, str(path), timeout=120)
    command("git", "-C", str(path), "merge-base", "--is-ancestor", target.revision, "origin/main")
    command("git", "-C", str(path), "checkout", "--quiet", "--detach", target.revision)
    if (path / "VERSION").read_text().strip() != target.version:
        raise ValueError("GitHub release and source versions differ")


def activate(target, path, previous):
    subprocess.run([str(path / "deploy/noticeplace"), "upgrade"], cwd=path,
                   env={**os.environ, "NOTICEPLACE_PUBLISHED_SHA": target.revision,
                        "NOTICEPLACE_EXPECTED_RELEASE": previous.path}, timeout=240, check=True)


def tick(queue, host, lookup=latest_release, inspect=local_api, prepare=stage, install=activate, quorum=check_quorum):
    quorum(queue.db)
    api = inspect()
    with queue.db:
        queue.observe(host, api)
    plan = queue.plan()
    if not plan or plan['state'] == 'done':
        queue.discover(f"{host}:{uuid.uuid4()}", lookup)
        return
    hosts = json.loads(plan['hosts'])
    if set(hosts) != set(queue.hosts) or len(hosts) != len(queue.hosts):
        raise RuntimeError("rollout membership changed")
    if hosts[plan['step']] != host:
        return
    target = Release(plan['version'], plan['revision'])
    if target.matches(api):
        queue.finish(plan, host, api)
        return
    if plan['state'] != 'ready' or not queue.fresh():
        return
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=WORK) as directory:
        path = Path(directory) / "source"
        prepare(target, path)
        quorum(queue.db)
        if inspect() != api or not queue.reserve(plan, host, api):
            return
        try:
            install(target, path, api)
            deadline = time.monotonic() + 35
            while True:
                try:
                    quorum(queue.db)
                    if queue.finish(plan, host, inspect()):
                        break
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("target API or quorum did not become ready")
                time.sleep(1)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            queue.block(plan, type(error).__name__)
            raise


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else "tick"
    if mode not in {"tick", "status"}:
        raise SystemExit("usage: noticeplace-api-rollout [tick|status]")
    host = os.environ['NOTICEPLACE_MESH_HOST_ID']
    hosts = json.loads(os.environ['NOTICEPLACE_MESH_HOSTS_JSON'])
    if (not isinstance(hosts, list) or len(hosts) != 3
            or any(not isinstance(item, str) or not item for item in hosts)
            or len(set(hosts)) != 3 or host not in hosts):
        raise ValueError("expected a local host in a three-node mesh")
    database = os.environ['NOTIFY_CENTER_DB']
    with closing(connect(database, readonly=mode == 'status')) as db:
        queue = Queue(db, hosts)
        if mode == 'status':
            print(json.dumps(queue.plan()))
            return
        with (WORK / "tick.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            queue.initialize()
            tick(queue, host)
            print(json.dumps(queue.plan()))
