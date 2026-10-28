#!/usr/bin/env python3
"""Prepare/review one host's v2 scopes; never print credentials or restart services.

Apply only inside the coordinated stopped-launch cutover. The two protected files
are individually atomic; this is not a cross-process or cross-host transaction.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


class SourceCollision(RuntimeError):
    pass


def read_env(text):
    for line in text.splitlines():
        if line.startswith('NOTIFY_CENTER_TOKENS_JSON='):
            return json.loads(line.split('=', 1)[1].strip().strip("'\""))
    raise ValueError('missing scope map')


def replace_env(text, scopes):
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith('NOTIFY_CENTER_TOKENS_JSON=')]
    if len(matches) != 1:
        raise ValueError('ambiguous scope map')
    lines[matches[0]] = 'NOTIFY_CENTER_TOKENS_JSON=' + json.dumps(scopes, separators=(',', ':')) + '\n'
    return ''.join(lines)


def prepare(scopes, callback, plan, new_token=lambda: secrets.token_urlsafe(48)):
    if any(scope.get('incident_role') in ('executor', 'verifier') for scope in scopes.values()):
        raise ValueError('already migrated; never rotate or replay implicitly')
    projects = {}
    for token, scope in scopes.items():
        project = scope.get('project')
        if not isinstance(project, str) or not project or project in projects:
            raise ValueError('one explicit legacy token per project is required')
        projects[project] = (token, scope)
    grants = plan.get('projects')
    if not isinstance(grants, dict) or set(grants) != set(projects):
        raise ValueError('reviewed plan must cover exact existing projects')
    output = {}
    callback_tokens = {}
    identities = set()
    summary = []
    for project, (token, original) in projects.items():
        grant = grants[project]
        sources = grant.get('verification_sources')
        owned_sources = grant.get('verification_owned_sources', False)
        if not isinstance(owned_sources, bool):
            raise ValueError('verification_owned_sources must be boolean')
        source_principal, executor_principal = grant.get('source_principal'), grant.get('executor_principal')
        if (not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() or '*' in s for s in sources)
                or len(sources) != len(set(sources))):
            raise ValueError('verification sources must be explicit unique exact identities')
        for identity in (source_principal, executor_principal):
            if not isinstance(identity, str) or not identity.strip() or identity in identities:
                raise ValueError('principals must be explicit and globally distinct')
            identities.add(identity)
        producer = dict(original, principal_id=source_principal,
                        incident_role='verifier' if sources or owned_sources else 'producer')
        if owned_sources:
            producer['verification_owned_sources'] = True
        if sources:
            producer['verification_sources'] = sources
        else:
            producer.pop('verification_sources', None)
        output[token] = producer
        # Only callback authority is copied. Producer policy/launch flags do not
        # move into an executor credential and are never modified on producers.
        executor = {k: v for k, v in original.items()
                    if k not in ('automatic_health_diagnosis', 'verification_sources', 'verification_owned_sources', 'agent_jobs')}
        executor.update(principal_id=executor_principal, incident_role='executor')
        fresh = new_token()
        if not isinstance(fresh, str) or not fresh or fresh in scopes or fresh in output:
            raise ValueError('executor credential collision')
        output[fresh] = executor
        callback_tokens[project] = fresh
        summary.append({'project': project, 'sources': len(sources),
                        'source_principal': source_principal, 'executor_principal': executor_principal,
                        'verification_owned_sources': owned_sources})
    updated_callback = dict(callback, tokens=callback_tokens)
    updated_callback.pop('token', None)
    if not callback.get('url'):
        raise ValueError('callback URL required')
    return output, updated_callback, summary


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data, metadata):
    temporary = path.with_name('.' + path.name + '.lifecycle-new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode))
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def protected(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError('protected regular mode0600 file required')
    return metadata


def scope_hash(scopes):
    return digest(json.dumps(scopes, sort_keys=True, separators=(',', ':')).encode())


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=Path('/etc/notification-center.env'))
    parser.add_argument('--callback', type=Path, default=Path('/home/roomhacker/.config/gptadmin/health-callback.json'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--prepare-dir', type=Path)
    parser.add_argument('--apply-prepared', type=Path)
    parser.add_argument('--check-prepared', type=Path, help='read-only base-map compatibility preflight')
    parser.add_argument('--scope-only', action='store_true', help='API replica without local executor callback')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    paths = [args.env] if args.scope_only else [args.env, args.callback]
    metadata = [protected(p) for p in paths]
    old = [p.read_bytes() for p in paths]
    if args.rollback:
        if args.apply_prepared or args.check_prepared or args.prepare_dir or not args.backup_dir:
            raise ValueError('rollback requires separate backup directory')
        protected(args.backup_dir / 'manifest.json')
        manifest = json.loads((args.backup_dir / 'manifest.json').read_text())
        if manifest['paths'] != [str(p.resolve()) for p in paths] or manifest['applied_hashes'] != [digest(x) for x in old]:
            raise ValueError('rollback refuses concurrent edits')
        originals = [(args.backup_dir / str(i)).read_bytes() for i in range(len(paths))]
        for path, data, meta in zip(paths, originals, metadata):
            write_atomic(path, data, meta)
        print('rollback completed; services unchanged')
        return
    existing = read_env(old[0].decode())
    if args.apply_prepared or args.check_prepared:
        if args.plan or args.prepare_dir or (args.apply_prepared and args.check_prepared):
            raise ValueError('apply consumes one prepared candidate, never generates credentials')
        candidate_path = (args.apply_prepared or args.check_prepared) / 'candidate.json'
        protected(candidate_path)
        candidate = json.loads(candidate_path.read_text())
        if scope_hash(existing) != candidate['base_scope_hash']:
            raise SourceCollision('source collision: local credential scope map differs from prepared base')
        scopes = candidate['scopes']
        if scope_hash(scopes) != candidate['candidate_scope_hash']:
            raise ValueError('candidate hash mismatch')
        new = [replace_env(old[0].decode(), scopes).encode()]
        if not args.scope_only:
            callback = dict(json.loads(old[1]), tokens=candidate['callback_tokens'])
            callback.pop('token', None)
            new.append((json.dumps(callback, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode())
        summary = candidate['summary']
        if args.check_prepared:
            print(json.dumps({'mode': 'compatible', 'projects': summary, 'services_restarted': False}))
            return
        print(json.dumps({'mode': 'apply-prepared', 'projects': summary, 'services_restarted': False}))
    else:
        if not args.plan or args.scope_only:
            raise ValueError('preparation needs explicit plan and callback')
        scopes, callback, summary = prepare(existing, json.loads(old[1]), json.loads(args.plan.read_text()))
        if args.prepare_dir:
            args.prepare_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            write_private(args.prepare_dir / 'candidate.json', dict(
                schema='noticeplace.credentials.v2', base_scope_hash=scope_hash(existing),
                candidate_scope_hash=scope_hash(scopes), scopes=scopes,
                callback_tokens=callback['tokens'], summary=summary))
        print(json.dumps({'mode': 'prepared' if args.prepare_dir else 'preview', 'projects': summary,
                          'existing_policy_preserved': True, 'services_restarted': False}))
        return
    if not args.backup_dir:
        raise ValueError('apply requires new protected backup directory')
    args.backup_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    for i, data in enumerate(old):
        fd = os.open(args.backup_dir / str(i), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    manifest = {'paths': [str(p.resolve()) for p in paths], 'applied_hashes': [digest(x) for x in new]}
    fd = os.open(args.backup_dir / 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(manifest, handle)
        handle.flush()
        os.fsync(handle.fileno())
    if [p.read_bytes() for p in paths] != old:
        raise ValueError('concurrent edit detected before apply')
    try:
        for path, data, prior, meta in zip(paths, new, old, metadata):
            if path.read_bytes() != prior:
                raise ValueError('concurrent edit during apply')
            write_atomic(path, data, meta)
    except Exception:
        # Restore only bytes this invocation installed, preserving any later
        # concurrent owner's edit rather than manufacturing a clean rollback.
        for path, prior, applied, meta in zip(paths, old, new, metadata):
            if path.read_bytes() == applied:
                write_atomic(path, prior, meta)
        raise


if __name__ == '__main__':
    try:
        main()
    except SourceCollision:
        raise SystemExit('source collision: local credential scope map differs from prepared base; no files changed')
    except Exception:
        raise SystemExit('credential preparation failed; no secret details emitted; inspect protected state')
