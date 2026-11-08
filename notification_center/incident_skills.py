"""Small portable incident runbooks; Git is their durable store, never health authority."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import uuid

REPOSITORY = 'megamen32/noticeplace-incident-skills'
DEFAULT_ROOT = Path('/home/roomhacker/agents-projects/noticeplace-incident-skills')
_SECRET = re.compile(r'''(?i)(?:\b(?:[\w-]*(?:password|token|secret|credential|api[-_]?key|cookie)[\w-]*|authorization)(?:\\?["'])?\s*[:=]\s*\S+|\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{15,}|sk-[A-Za-z0-9_-]{16,}|https?://[^\s/]+:[^\s/]+@)''')
_METADATA_FIELDS = {'schema', 'family', 'sha256', 'evidence_refs'}
_SKILL_FILES = ('SKILL.md', 'metadata.json')


def skill_policy(value=None):
    if value is None:
        return {'learn': True, 'publish': True}
    if not isinstance(value, dict) or any(type(value.get(k)) is not bool for k in ('learn', 'publish')):
        raise ValueError('invalid incident skill policy')
    return {'learn': value['learn'], 'publish': value['learn'] and value['publish']}


def read_policy(center):
    values = [center.get_runtime_setting('incident_skill_'+key+'_enabled', '1') for key in ('learning','publish')]
    if any(value not in ('0','1') for value in values):
        raise ValueError('invalid incident skill setting')
    return skill_policy(dict(zip(('learn','publish'), (value == '1' for value in values))))


def _family(project, source, problem):
    values = [project, source, problem]
    if any(not isinstance(v, str) or not v.strip() or len(v) > 256 or '\x00' in v or _SECRET.search(v) for v in values):
        raise ValueError('skill requires bounded project, source and problem identity')
    return values, hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:32]


def _validate(document, evidence):
    if not isinstance(document, str) or len(document.encode()) > 24000 or '\x00' in document or _SECRET.search(document):
        raise ValueError('skill contains sensitive or oversized content')
    if not re.match(r'\A---\nname: [a-z0-9-]+\ndescription: [^\n]+\n---\n', document):
        raise ValueError('skill requires name and description frontmatter')
    for heading in ('Диагностика', 'Исправление', 'Проверка', 'Границы'):
        if '\n## '+heading+'\n' not in document:
            raise ValueError('skill requires diagnosis, repair, verification and boundaries')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16 or any(not isinstance(v,str) or not v.strip() or len(v)>512 or '\x00' in v or _SECRET.search(v) for v in evidence):
        raise ValueError('skill requires bounded non-secret evidence references')


def _validate_record(metadata, document, family, *, expected_sha=None):
    """Validate content, identity and receipt without trusting extra metadata."""
    if (not isinstance(metadata, dict) or set(metadata) != _METADATA_FIELDS
            or metadata['schema'] != 'noticeplace.skill.v1' or metadata['family'] != family):
        raise ValueError('invalid skill metadata')
    _validate(document, metadata['evidence_refs'])
    digest = hashlib.sha256(document.encode('utf-8')).hexdigest()
    if metadata['sha256'] != digest or (expected_sha is not None and digest != expected_sha):
        raise ValueError('skill content or identity changed without its receipt')


class SkillStore:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = root.resolve()
        self._thread_lock = threading.RLock()
        self._lock_file = None

    def _paths(self, project, source, problem):
        family, key = _family(project, source, problem)
        folder = self.root / 'skills' / ('incident-'+key)
        if folder.resolve() != folder or (folder/'SKILL.md').is_symlink() or (folder/'metadata.json').is_symlink():
            raise ValueError('skill path must not contain symlinks')
        return family, folder

    @contextlib.contextmanager
    def lock(self):
        with self._thread_lock:
            if self._lock_file is not None:
                yield
                return
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.resolve() != self.root or (self.root/'.git').is_symlink():
                raise ValueError('skill path must not contain symlinks')
            path = self.root/'.git'/'noticeplace-skills.lock' if (self.root/'.git').is_dir() else self.root/'.skills.lock'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                self._lock_file = handle
                try:
                    yield
                finally:
                    self._lock_file = None

    @contextlib.contextmanager
    def _directory(self, project, source, problem, *, create=False):
        family, folder = self._paths(project, source, problem)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        # Open each component without following symlinks. File operations stay
        # relative to the held directory even if another process renames it.
        with contextlib.ExitStack() as stack:
            fd = os.open(self.root, flags)
            stack.callback(os.close, fd)
            for name in ('skills', folder.name):
                if create:
                    try: os.mkdir(name, 0o700, dir_fd=fd)
                    except FileExistsError: pass
                fd = os.open(name, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            self._check_directory(family, folder, fd)
            yield family, folder, fd
            self._check_directory(family, folder, fd)

    def _check_directory(self, family, folder, fd):
        self._paths(*family)
        opened = os.fstat(fd)
        current = folder.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('skill directory changed during operation')

    @staticmethod
    def _read_file(directory_fd, name, limit):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('skill file is not bounded regular content')
            content = handle.read(limit+1)
            after = os.fstat(handle.fileno())
        if len(content) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('skill file changed while reading')
        try:
            return content.decode('utf-8')
        except UnicodeError:
            raise ValueError('skill file must be UTF-8') from None

    def lookup(self, project, source, problem):
        with self.lock():
            return self._lookup(project, source, problem)

    def _lookup(self, project, source, problem):
        family, folder = self._paths(project, source, problem)
        if not (folder/'metadata.json').exists():
            return None
        with self._directory(*family) as (_, _, fd):
            metadata = json.loads(self._read_file(fd, 'metadata.json', 65536))
            document = self._read_file(fd, 'SKILL.md', 24000)
        _validate_record(metadata, document, family)
        return {**metadata, 'path': str(folder/'SKILL.md'), 'document': document}

    def save(self, project, source, problem, document, evidence):
        _validate(document, evidence)
        with self.lock():
            return self._save(project, source, problem, document, evidence)

    def _save(self, project, source, problem, document, evidence):
        family, folder = self._paths(project, source, problem)
        metadata = {'schema': 'noticeplace.skill.v1', 'family': family,
                    'sha256': hashlib.sha256(document.encode()).hexdigest(), 'evidence_refs': evidence}
        with self._directory(*family, create=True) as (_, _, directory_fd):
            for name, content in [('SKILL.md',document), ('metadata.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')]:
                temp = '.skill-'+uuid.uuid4().hex
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
                try:
                    with os.fdopen(fd,'w',encoding='utf-8') as output:
                        output.write(content); output.flush(); os.fsync(output.fileno())
                    self._check_directory(family, folder, directory_fd)
                    os.replace(temp, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                finally:
                    try: os.unlink(temp, dir_fd=directory_fd)
                    except FileNotFoundError: pass
        return {**metadata,'path':str(folder/'SKILL.md')}

    def _run(self, *args, raw=False):
        try:
            result = subprocess.run(args,cwd=self.root,capture_output=True,text=True,encoding='utf-8',timeout=45,check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('skill Git operation unavailable; local skill retained') from error
        if result.returncode:
            raise RuntimeError('skill Git operation failed; local skill retained, no repair replay')
        return result.stdout if raw else result.stdout.strip()

    def _saved_snapshot(self, saved):
        if not isinstance(saved, dict) or not isinstance(saved.get('family'), list) or len(saved['family']) != 3:
            raise ValueError('invalid skill receipt')
        family, folder = self._paths(*saved['family'])
        if saved.get('path') != str(folder/'SKILL.md'):
            raise ValueError('skill receipt path does not match its family')
        snapshot = self._lookup(*family)
        if snapshot is None or any(saved.get(key) != snapshot[key] for key in _METADATA_FIELDS):
            raise ValueError('skill changed since its save receipt')
        return snapshot, [str((folder/name).relative_to(self.root)) for name in _SKILL_FILES]

    def _check_git_snapshot(self, ref, paths, snapshot):
        entries = self._run('git', 'ls-tree', ref, '--', *paths) if ref else self._run('git', 'ls-files', '--stage', '--', *paths)
        found = set()
        for line in entries.splitlines():
            details, path = line.split('\t', 1)
            fields = details.split()
            if fields[0] != '100644' or (not ref and fields[2] != '0'):
                raise ValueError('skill Git snapshot must contain regular files')
            found.add(path)
        if found != set(paths):
            raise ValueError('skill Git snapshot is incomplete')
        document = self._run('git', 'show', ref+':'+paths[0], raw=True)
        metadata = json.loads(self._run('git', 'show', ref+':'+paths[1], raw=True))
        _validate_record(metadata, document, snapshot['family'], expected_sha=snapshot['sha256'])
        if any(metadata[key] != snapshot[key] for key in _METADATA_FIELDS):
            raise ValueError('skill Git snapshot changed since validation')

    def _check_commit(self, commit, parent, paths, snapshot):
        if self._run('git', 'rev-parse', commit+'^') != parent:
            raise RuntimeError('skill publication has unrelated local history')
        changed = set(self._run('git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines())
        if not changed or not changed.issubset(paths):
            raise RuntimeError('skill publication contains unowned paths')
        self._check_git_snapshot(commit, paths, snapshot)

    def publish(self, saved):
        with self.lock():
            return self._publish(saved)

    def _publish(self, saved):
        snapshot, paths = self._saved_snapshot(saved)
        # The owner authorized this private repository only. Never publish to
        # an arbitrary remote supplied by incident telemetry or skill prose.
        remote = self._run('git','remote','get-url','origin')
        if remote not in ('https://github.com/'+REPOSITORY+'.git','git@github.com:'+REPOSITORY+'.git'):
            raise RuntimeError('unexpected skill repository remote')
        if self._run('gh','api','repos/'+REPOSITORY,'--jq','.private') != 'true':
            raise RuntimeError('skill repository privacy is not confirmed')
        if self._run('git','branch','--show-current') != 'main':
            raise RuntimeError('skill store must use main')
        # Use this verified URL explicitly: origin may have a different pushurl.
        remote_sha = self._run('git', 'ls-remote', remote, 'refs/heads/main').split()[0]
        base = self._run('git', 'rev-parse', 'HEAD')
        if base != remote_sha:
            # Only retry our exact validated commit after a failed push. Never
            # bundle other local history into a runbook publication.
            self._check_commit(base, remote_sha, paths, snapshot)
        self._run('git','add','--',*paths)
        self._check_git_snapshot('', paths, snapshot)
        self._saved_snapshot(saved)
        if self._run('git','diff','--cached','--name-only','--',*paths):
            if base != remote_sha or self._run('git', 'rev-parse', 'HEAD') != base:
                raise RuntimeError('skill publication base changed')
            self._run('git','commit','--only','-m','learn: update verified incident runbook','--',*paths)
        commit = self._run('git','rev-parse','HEAD')
        if commit != remote_sha:
            self._check_commit(commit, remote_sha, paths, snapshot)
        else:
            self._check_git_snapshot(commit, paths, snapshot)
        # Push the checked immutable object, not a concurrently moving HEAD.
        self._run('git','push',remote,commit+':refs/heads/main')
        remote_sha = self._run('git','ls-remote',remote,'refs/heads/main').split()[0]
        if remote_sha != commit:
            raise RuntimeError('skill publication has no matching remote receipt')
        return {'status':'published','commit':commit,'repository':REPOSITORY,'sha256':saved['sha256']}


def main():
    parser = argparse.ArgumentParser(description='Read or save a verified incident skill')
    parser.add_argument('action', choices=['lookup','save'])
    for field in ('project','source','problem'): parser.add_argument('--'+field,required=True)
    parser.add_argument('--document',type=Path)
    parser.add_argument('--evidence-ref',action='append',default=[])
    parser.add_argument('--publish',action='store_true')
    args = parser.parse_args()
    store = SkillStore()
    try:
        with store.lock():
            if args.action == 'lookup':
                result = store.lookup(args.project,args.source,args.problem)
            else:
                if not args.document: raise ValueError('document is required')
                result = store.save(args.project,args.source,args.problem,args.document.read_text(),args.evidence_ref)
                if args.publish: result['publication'] = store.publish(result)
        print(json.dumps({'ok':True,'skill':result},ensure_ascii=False))
        return 0
    except (ValueError,RuntimeError,OSError) as error:
        # Do not echo file contents, subprocess stderr, credentials or telemetry.
        print(json.dumps({'ok':False,'error':str(error) if isinstance(error,(ValueError,RuntimeError)) else 'skill file unavailable'},ensure_ascii=False))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
