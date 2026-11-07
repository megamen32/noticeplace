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
import subprocess
import tempfile

REPOSITORY = 'megamen32/noticeplace-incident-skills'
DEFAULT_ROOT = Path('/home/roomhacker/agents-projects/noticeplace-incident-skills')
_SECRET = re.compile(r'(?i)(?:\b(?:[\w-]*(?:password|token|secret|credential|api[-_]?key|cookie)[\w-]*|authorization)\s*[:=]\s*\S+|\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{15,}|sk-[A-Za-z0-9_-]{16,}|https?://[^\s/]+:[^\s/]+@)')


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
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16 or any(not isinstance(v,str) or not v.strip() or len(v)>512 or _SECRET.search(v) for v in evidence):
        raise ValueError('skill requires bounded non-secret evidence references')


class SkillStore:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = root.resolve()

    def _paths(self, project, source, problem):
        family, key = _family(project, source, problem)
        folder = self.root / 'skills' / ('incident-'+key)
        if folder.resolve() != folder or (folder/'SKILL.md').is_symlink() or (folder/'metadata.json').is_symlink():
            raise ValueError('skill path must not contain symlinks')
        return family, folder

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/'.git'/'noticeplace-skills.lock' if (self.root/'.git').is_dir() else self.root/'.skills.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def lookup(self, project, source, problem):
        family, folder = self._paths(project, source, problem)
        if not (folder/'metadata.json').exists():
            return None
        metadata = json.loads((folder/'metadata.json').read_text())
        document = (folder/'SKILL.md').read_text()
        _validate(document, metadata.get('evidence_refs'))
        if metadata.get('family') != family or metadata.get('sha256') != hashlib.sha256(document.encode()).hexdigest():
            raise ValueError('skill content or identity changed without its receipt')
        return {**metadata, 'path': str(folder/'SKILL.md'), 'document': document}

    def save(self, project, source, problem, document, evidence):
        _validate(document, evidence)
        family, folder = self._paths(project, source, problem)
        folder.mkdir(parents=True, exist_ok=True)
        metadata = {'schema': 'noticeplace.skill.v1', 'family': family,
                    'sha256': hashlib.sha256(document.encode()).hexdigest(), 'evidence_refs': evidence}
        for name, content in [('SKILL.md',document), ('metadata.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')]:
            fd, temp = tempfile.mkstemp(dir=folder)
            try:
                with os.fdopen(fd,'w') as output:
                    output.write(content); output.flush(); os.fsync(output.fileno())
                os.replace(temp,folder/name)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        return {**metadata,'path':str(folder/'SKILL.md')}

    def _run(self, *args):
        try:
            result = subprocess.run(args,cwd=self.root,capture_output=True,text=True,timeout=45,check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('skill Git operation unavailable; local skill retained') from error
        if result.returncode:
            raise RuntimeError('skill Git operation failed; local skill retained, no repair replay')
        return result.stdout.strip()

    def publish(self, saved):
        # The owner authorized this private repository only. Never publish to
        # an arbitrary remote supplied by incident telemetry or skill prose.
        remote = self._run('git','remote','get-url','origin')
        if remote not in ('https://github.com/'+REPOSITORY+'.git','git@github.com:'+REPOSITORY+'.git'):
            raise RuntimeError('unexpected skill repository remote')
        if self._run('gh','api','repos/'+REPOSITORY,'--jq','.private') != 'true':
            raise RuntimeError('skill repository privacy is not confirmed')
        if self._run('git','branch','--show-current') != 'main':
            raise RuntimeError('skill store must use main')
        relative = str(Path(saved['path']).parent.relative_to(self.root))
        self._run('git','add','--',relative)
        if self._run('git','diff','--cached','--name-only','--',relative):
            self._run('git','commit','--only','-m','learn: update verified incident runbook','--',relative)
        commit = self._run('git','rev-parse','HEAD')
        self._run('git','push','origin','HEAD:main')
        remote_sha = self._run('git','ls-remote','origin','refs/heads/main').split()[0]
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
