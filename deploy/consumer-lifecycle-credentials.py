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
import sqlite3
import sys


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


CONSUMER_GRANTS_ENV = 'NOTICEPLACE_CONSUMER_VERIFIER_GRANTS_JSON'


def read_consumer_grants(text):
    lines = [line.split('=', 1)[1].strip().strip("'\"") for line in text.splitlines()
             if line.startswith(CONSUMER_GRANTS_ENV + '=')]
    if len(lines) > 1:
        raise ValueError('ambiguous consumer grant map')
    grants = json.loads(lines[0]) if lines else []
    if not isinstance(grants, list):
        raise ValueError('consumer grants must be a list')
    return grants


def replace_consumer_grants(text, grants):
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(CONSUMER_GRANTS_ENV + '=')]
    if len(matches) > 1:
        raise ValueError('ambiguous consumer grant map')
    encoded = CONSUMER_GRANTS_ENV + '=' + json.dumps(grants, separators=(',', ':')) + '\n'
    if matches:
        lines[matches[0]] = encoded
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(encoded)
    return ''.join(lines)


def prepare_consumer_grants(plan):
    registrations = {r['consumer_id']: r['project'] for r in plan.get('registered_consumers', [])}
    grants = plan.get('consumer_grants', [])
    if not isinstance(grants, list) or len(grants) > 1024:
        raise ValueError('bounded consumer grant list required')
    output = []
    selected = set()
    fields = {'consumer_id', 'project', 'incident_role', 'verification_owned_sources', 'verification_sources'}
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) - fields:
            raise ValueError('unsupported consumer grant fields')
        consumer, project = grant.get('consumer_id'), grant.get('project')
        if (not isinstance(consumer, str) or not consumer or len(consumer) > 128 or '*' in consumer
                or not isinstance(project, str) or not project or len(project) > 128 or '*' in project
                or registrations.get(consumer) != project or consumer in selected
                or grant.get('incident_role') != 'verifier'):
            raise ValueError('explicit reviewed same-project independent verifier required')
        sources = grant.get('verification_sources', [])
        owned = grant.get('verification_owned_sources', False)
        if (not isinstance(owned, bool) or not isinstance(sources, list) or len(sources) > 1024
                or any(not isinstance(source, str) or not source or len(source) > 128 or '*' in source for source in sources)
                or len(sources) != len(set(sources)) or not (owned or sources)):
            raise ValueError('exact source grants or authenticated owned source policy required')
        selected.add(consumer)
        output.append(dict(grant))
    legacy = [consumer for grant in plan.get('projects', {}).values() for consumer in grant.get('verification_consumer_ids', [])]
    if selected.intersection(legacy):
        raise ValueError('ambiguous supplemental and legacy consumer grants')
    return output


def validate_consumer_grant_join(scopes, grants):
    seen = set()
    for grant in grants:
        identity = (grant.get('consumer_id'), grant.get('project'))
        if identity in seen:
            raise ValueError('duplicate supplemental consumer grant')
        seen.add(identity)
        if any(scope.get('incident_role') == 'verifier' and scope.get('project') == identity[1]
               and identity[0] in scope.get('verification_consumer_ids', []) for scope in scopes.values()):
            raise ValueError('ambiguous supplemental and legacy consumer grants')


def merge_consumer_grants(existing, planned):
    """Preserve unrelated policy; never silently change an existing consumer grant."""
    merged = list(existing)
    for grant in planned:
        matches = [prior for prior in merged if prior.get('consumer_id') == grant['consumer_id']]
        if matches:
            if len(matches) != 1 or matches[0] != grant:
                raise ValueError('existing consumer grant collision requires explicit reconciliation')
        else:
            merged.append(grant)
    return merged


def resolve_reviewed_source_refs(plan, database):
    """Hydrate exact reviewed source hashes in memory; never expose source URLs."""
    resolved = json.loads(json.dumps(plan))
    wanted = {project: set(grant.get('verification_source_sha256_refs', []))
              for project, grant in resolved['projects'].items() if grant.get('verification_source_sha256_refs')}
    if not wanted:
        return resolved
    if any(not isinstance(ref, str) or len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref)
           for refs in wanted.values() for ref in refs):
        raise ValueError('exact SHA256 source references required')
    if database.startswith('mesh:'):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from notification_center.mesh_store import connect
        connection = connect(database, readonly=True)
    else:
        connection = sqlite3.connect('file:' + database + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
    try:
        placeholders = ','.join('?' for _ in wanted)
        rows = connection.execute("SELECT DISTINCT i.project,json_extract(e.payload_json,'$.source_id') AS source_id FROM incidents i JOIN events e ON e.incident_id=i.id WHERE e.event_type='health.degraded' AND i.project IN (" + placeholders + ") LIMIT 10001", tuple(wanted)).fetchall()
        if len(rows) > 10000:
            raise ValueError('source reference scan exceeds bounded limit')
        found = {project: {} for project in wanted}
        for row in rows:
            source = row['source_id']
            if isinstance(source, str):
                ref = hashlib.sha256(source.encode()).hexdigest()
                if ref in wanted[row['project']]:
                    found[row['project']][ref] = source
        for project, refs in wanted.items():
            if set(found[project]) != refs:
                raise ValueError('reviewed source reference missing from canonical state')
            grant = resolved['projects'][project]
            grant['verification_sources'] = sorted(set(grant['verification_sources']) | set(found[project].values()))
            grant.pop('verification_source_sha256_refs', None)
        return resolved
    finally:
        connection.close()


def prepare(scopes, callback, plan, new_token=lambda: secrets.token_urlsafe(48)):
    if any(grant.get('verification_source_sha256_refs') for grant in plan.get('projects', {}).values()):
        raise ValueError('reviewed source references require explicit read-only resolution')
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
    registrations = plan.get('registered_consumers', [])
    consumer_projects = {}
    if not isinstance(registrations, list):
        raise ValueError('reviewed consumer registration list required')
    for registration in registrations:
        consumer, project = registration.get('consumer_id'), registration.get('project')
        if not isinstance(consumer, str) or not consumer or '*' in consumer or consumer in consumer_projects:
            raise ValueError('explicit unique reviewed consumer IDs required')
        if not isinstance(project, str) or not project or project == '*':
            raise ValueError('exact registered consumer project required')
        consumer_projects[consumer] = project
    output = {}
    callback_tokens = {}
    identities = set()
    summary = []
    for project, (token, original) in projects.items():
        grant = grants[project]
        sources = grant.get('verification_sources')
        consumers = grant.get('verification_consumer_ids', [])
        if (not isinstance(consumers, list) or any(not isinstance(c, str) or not c or '*' in c for c in consumers)
                or len(consumers) != len(set(consumers))
                or any(consumer_projects.get(c) != project for c in consumers)):
            raise ValueError('consumer grants require exact reviewed same-project identities')
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
                        incident_role='verifier' if sources or owned_sources or consumers else 'producer')
        if consumers:
            producer['verification_consumer_ids'] = consumers
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
                    if k not in ('automatic_health_diagnosis', 'verification_sources', 'verification_owned_sources', 'verification_consumer_ids', 'agent_jobs')}
        executor.update(principal_id=executor_principal, incident_role='executor')
        fresh = new_token()
        if not isinstance(fresh, str) or not fresh or fresh in scopes or fresh in output:
            raise ValueError('executor credential collision')
        output[fresh] = executor
        callback_tokens[project] = fresh
        summary.append({'project': project, 'sources': len(sources),
                        'source_principal': source_principal, 'executor_principal': executor_principal,
                        'verification_owned_sources': owned_sources, 'verification_consumer_ids': consumers})
    extra_executors = plan.get('consumer_executor_projects', {})
    needed = {grant['project'] for grant in plan.get('consumer_grants', [])} - set(projects)
    if not isinstance(extra_executors, dict) or set(extra_executors) != needed:
        raise ValueError('registered-only projects need explicit executor callback policy')
    for project, grant in extra_executors.items():
        if (not isinstance(grant, dict) or set(grant) != {'principal_id', 'max_severity'}
                or grant['max_severity'] != 'notice' or project not in consumer_projects.values()):
            raise ValueError('registered-only executor scope must be exact project and notice ceiling')
        identity = grant['principal_id']
        if (not isinstance(identity, str) or not identity.strip() or identity in identities
                or identity in {'consumer:' + consumer for consumer in consumer_projects}):
            raise ValueError('registered-only executor requires distinct stable principal')
        identities.add(identity)
        fresh = new_token()
        if not isinstance(fresh, str) or not fresh or fresh in scopes or fresh in output:
            raise ValueError('executor credential collision')
        output[fresh] = dict(project=project, max_severity='notice', principal_id=identity, incident_role='executor')
        callback_tokens[project] = fresh
        summary.append(dict(project=project, executor_only=True, executor_principal=identity))
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


def permitted_baselines(scopes, allowed_missing):
    """Only the reviewed, inactive integration seed may be absent on replicas."""
    hashes = [scope_hash(scopes)]
    if not allowed_missing:
        return hashes
    if allowed_missing != ['integration']:
        raise ValueError('only the reviewed integration seed may be absent')
    seeds = [(token, scope) for token, scope in scopes.items() if scope.get('project') == 'integration']
    if len(seeds) != 1:
        raise ValueError('exact existing integration seed required')
    token, seed = seeds[0]
    severities = ('debug', 'info', 'notice', 'important')
    if (seed.get('max_severity', 'notice') not in severities or seed.get('agent_jobs')
            or seed.get('automatic_health_diagnosis') is True
            or seed.get('incident_role') in ('verifier', 'executor')
            or any(seed.get(key) for key in ('verification_sources', 'verification_owned_sources', 'verification_consumer_ids'))):
        raise ValueError('active health or elevated integration seed cannot be omitted')
    hashes.append(scope_hash({key: value for key, value in scopes.items() if key != token}))
    return hashes


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
    parser.add_argument('--allow-missing-base-project', action='append', default=[])
    parser.add_argument('--resolve-reviewed-source-refs', action='store_true')
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
    existing_consumer_grants = read_consumer_grants(old[0].decode())
    if args.apply_prepared or args.check_prepared:
        if args.plan or args.prepare_dir or args.allow_missing_base_project or args.resolve_reviewed_source_refs or (args.apply_prepared and args.check_prepared):
            raise ValueError('apply consumes one prepared candidate, never generates credentials')
        candidate_path = (args.apply_prepared or args.check_prepared) / 'candidate.json'
        protected(candidate_path)
        candidate = json.loads(candidate_path.read_text())
        if scope_hash(existing) not in candidate.get('permitted_base_hashes', [candidate['base_scope_hash']]):
            raise SourceCollision('source collision: local credential scope map differs from prepared base')
        if scope_hash(existing_consumer_grants) != candidate.get('base_grants_hash', scope_hash([])):
            raise SourceCollision('source collision: local consumer grants differ from prepared base')
        consumer_grants = candidate.get('consumer_grants', [])
        if scope_hash(consumer_grants) != candidate.get('candidate_grants_hash', scope_hash([])):
            raise ValueError('consumer grant candidate hash mismatch')
        scopes = candidate['scopes']
        validate_consumer_grant_join(scopes, consumer_grants)
        if scope_hash(scopes) != candidate['candidate_scope_hash']:
            raise ValueError('candidate hash mismatch')
        new = [replace_consumer_grants(replace_env(old[0].decode(), scopes), consumer_grants).encode()]
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
        accepted_bases = permitted_baselines(existing, args.allow_missing_base_project)
        plan = json.loads(args.plan.read_text())
        if args.resolve_reviewed_source_refs:
            database_lines = [line.split('=', 1)[1].strip().strip("'\"") for line in old[0].decode().splitlines() if line.startswith('NOTIFY_CENTER_DB=')]
            if len(database_lines) > 1:
                raise ValueError('ambiguous canonical state reference')
            database = database_lines[0] if database_lines else '/var/lib/notification-center/notify-center.sqlite3'
            plan = resolve_reviewed_source_refs(plan, database)
        scopes, callback, summary = prepare(existing, json.loads(old[1]), plan)
        consumer_grants = merge_consumer_grants(existing_consumer_grants, prepare_consumer_grants(plan))
        validate_consumer_grant_join(scopes, consumer_grants)
        if args.prepare_dir:
            args.prepare_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            write_private(args.prepare_dir / 'candidate.json', dict(
                schema='noticeplace.credentials.v2', base_scope_hash=scope_hash(existing),
                permitted_base_hashes=accepted_bases, reviewed_missing_base_projects=args.allow_missing_base_project,
                candidate_scope_hash=scope_hash(scopes), scopes=scopes,
                callback_tokens=callback['tokens'], summary=summary, consumer_grants=consumer_grants,
                base_grants_hash=scope_hash(existing_consumer_grants), candidate_grants_hash=scope_hash(consumer_grants)))
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
    except SourceCollision as error:
        raise SystemExit(str(error) + '; no files changed')
    except Exception:
        raise SystemExit('credential preparation failed; no secret details emitted; inspect protected state')
