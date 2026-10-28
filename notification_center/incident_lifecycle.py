"""Incident-bound completion and source-owned, generation-fenced checks.

Token registration is server configuration, never event metadata. A verifier
must declare verification_sources and principal_id, and cannot finish attempts.
"""
import hashlib
import json
import math
import time
import uuid


BUILTIN_DELIVERY_PROFILES = frozenset({'profile_emergency', 'profile_important', 'profile_log'})


PROBLEM_TYPE_ALIASES = {
    **dict.fromkeys(('load', 'loadavg', 'cpu_load', 'cpu'), 'cpu'),
    **dict.fromkeys(('disk', 'disk_space', 'disk_full', 'inodes'), 'disk'),
}


def canonical_problem_type(event):
    explicit = str(event.get('problem_type') or '')
    signal = str(event.get('signal_type') or '')
    return explicit or PROBLEM_TYPE_ALIASES.get(signal, signal)


def initialize(center):
    with center._connection:
        center._connection.executescript('''
        CREATE TABLE IF NOT EXISTS incident_lifecycle (
          incident_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
          source_principal TEXT NOT NULL, metadata_json TEXT NOT NULL, group_key TEXT NOT NULL, source_id TEXT NOT NULL,
          attempt_state TEXT, verification_state TEXT, verification_due REAL,
          request_id TEXT, session_id TEXT, harness TEXT, attempt_id TEXT,
          executor_principal TEXT, latest_observed_at REAL);
        CREATE INDEX IF NOT EXISTS incident_lifecycle_group ON incident_lifecycle(group_key);
        CREATE INDEX IF NOT EXISTS incident_lifecycle_due_source ON incident_lifecycle(source_id,verification_state,verification_due);
        CREATE TABLE IF NOT EXISTS incident_verification_requests (
          request_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
          generation INTEGER NOT NULL, attempt_id TEXT NOT NULL,
          finish_json TEXT NOT NULL, result_json TEXT NOT NULL,
          UNIQUE(incident_id, attempt_id));
        CREATE TABLE IF NOT EXISTS incident_finish_keys (
          idempotency_key TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
          attempt_id TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incident_check_receipts (
          idempotency_key TEXT PRIMARY KEY, request_id TEXT NOT NULL,
          payload_json TEXT NOT NULL, result_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS incident_check_receipts_request ON incident_check_receipts(request_id);
        ''')

        columns = {row['name'] for row in center._connection.execute('PRAGMA table_info(incident_lifecycle)').fetchall()}
        for name in ('continuation_json', 'last_check_receipt_json'):
            if name not in columns:
                center._connection.execute('ALTER TABLE incident_lifecycle ADD COLUMN ' + name + ' TEXT')
        # Upgrade existing accepted receipts without replaying a delivery. A
        # previously queued continuation retains its exact original operation.
        legacy = center._connection.execute("""SELECT l.*,r.idempotency_key,r.result_json
          FROM incident_lifecycle l JOIN incident_check_receipts r ON r.request_id=l.request_id
          WHERE l.last_check_receipt_json IS NULL ORDER BY r.rowid""").fetchall()
        for row in legacy:
            result = json.loads(row['result_json'])
            receipt = dict(idempotency_key=row['idempotency_key'],request_id=row['request_id'],result=result)
            continuation = result.get('continuation')
            if continuation:
                continuation = {**continuation, 'delivery_id': result.get('continuation_delivery_id')}
            nonhealthy = row['verification_state'] in ('degraded', 'unknown', 'stale')
            center._connection.execute("""UPDATE incident_lifecycle SET last_check_receipt_json=?,
              continuation_json=?,request_id=?,verification_due=?,verification_state=? WHERE incident_id=?""",
              (json.dumps(receipt,sort_keys=True),json.dumps(continuation,sort_keys=True) if continuation else None,
               'check_' + uuid.uuid4().hex if nonhealthy else row['request_id'],
               time.time()+60 if nonhealthy else row['verification_due'], 'due' if row['verification_state']=='stale' else row['verification_state'], row['incident_id']))

        # One joined historical scan, not an incident-by-incident lookup. No
        # recovery or attempt is inferred from existing cards or session status.
        rows = center._connection.execute("""SELECT i.id, i.consumer_id, e.payload_json
          FROM incidents i JOIN events e ON e.incident_id=i.id
          WHERE e.event_type='health.degraded' AND NOT EXISTS
            (SELECT 1 FROM incident_lifecycle l WHERE l.incident_id=i.id)
          ORDER BY i.id,e.created_at,e.rowid""").fetchall()
        episodes = {}
        for row in rows:
            try:
                event = json.loads(row['payload_json'])
            except (ValueError, TypeError):
                continue
            if not typed(event):
                continue
            item = episodes.setdefault(row['id'], [event, row['consumer_id'], 0, None])
            item[2] += 1
            item[0] = event
            observed = event.get('observed_at')
            if isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isfinite(observed):
                item[3] = max(item[3] if item[3] is not None else observed, observed)
        for incident_id, (event, consumer_id, generation, observed) in episodes.items():
            group = group_identity(event, consumer_id)
            center._connection.execute("""INSERT OR IGNORE INTO incident_lifecycle
              (incident_id,generation,source_principal,metadata_json,group_key,source_id,latest_observed_at)
              VALUES (?,?,'historical-unbound',?,?,?,?)""",
              (incident_id,generation,json.dumps(group,sort_keys=True),group_key(group),group['source_id'],observed))


        # Built-in profiles select a notification route, not a producer. Repair
        # only the projection keys; source trust, attempts and health are intact.
        routed = center._connection.execute("""SELECT l.incident_id,l.metadata_json,l.group_key,i.consumer_id
          FROM incident_lifecycle l JOIN incidents i ON i.id=l.incident_id
          WHERE i.consumer_id IN (?,?,?)""",tuple(sorted(BUILTIN_DELIVERY_PROFILES))).fetchall()
        for row in routed:
            group = json.loads(row['metadata_json'])
            canonical = group_identity(group, row['consumer_id'])['consumer_id']
            if group.get('consumer_id') != canonical:
                group['consumer_id'] = canonical
                center._connection.execute('UPDATE incident_lifecycle SET metadata_json=?,group_key=? WHERE incident_id=?',
                    (json.dumps(group,sort_keys=True),group_key(group),row['incident_id']))


def typed(event):
    return isinstance(event, dict) and all(str(event.get(k) or '').strip() for k in ('project','source_id','host_id','signal_type','correlation_id'))


def group_identity(event, consumer_id):
    group = {key: str(event.get(key) or '') for key in ('project','source_id','host_id','signal_type','correlation_id')}
    identity = str(consumer_id or '')
    group['consumer_id'] = (identity if identity not in BUILTIN_DELIVERY_PROFILES else '') or group['project'] + '+' + group['source_id']
    group['problem_type'] = canonical_problem_type(event)
    return group


def group_key(group):
    return json.dumps([group[k] for k in ('project','consumer_id','host_id','problem_type')],separators=(',',':'))


def bind_source(center, token, row):
    from .core import AuthorizationError
    identity = principal(center, token)
    if row['source_principal'] == 'historical-unbound':
        verifier(center, token, row['incident_id'])
        center._connection.execute("UPDATE incident_lifecycle SET source_principal=? WHERE incident_id=? AND source_principal='historical-unbound'",(identity,row['incident_id']))
    elif row['source_principal'] != identity:
        raise AuthorizationError('source update requires the bound source principal')



def principal(center, token):
    scope = center._token_scope(token)
    return str(scope.get('principal_id') or 'credential:' + hashlib.sha256(token.encode()).hexdigest())


def observe_degraded(center, token, incident_id, event):
    if event.get('event_type') != 'health.degraded' or not typed(event):
        return
    from .core import ValidationError
    import math
    observed = event.get('observed_at')
    if observed is not None and (isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed)):
        raise ValidationError('observed_at must be a finite source measurement timestamp')
    incident = center._connection.execute('SELECT consumer_id FROM incidents WHERE id=?',(incident_id,)).fetchone()
    group = group_identity(event, incident['consumer_id'])
    from .core import AuthorizationError, ValidationError
    prior = center._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(incident_id,)).fetchone()
    if prior:
        original = json.loads(prior['metadata_json'])
        if any(group[k] != original[k] for k in group):
            raise ValidationError('health episode source identity cannot change')
        bind_source(center, token, prior)
    pending_nonce = 'check_' + uuid.uuid4().hex if prior and prior['attempt_id'] else None
    center._connection.execute('''INSERT INTO incident_lifecycle
      (incident_id,generation,source_principal,metadata_json,group_key,source_id,latest_observed_at) VALUES (?,1,?,?,?,?,?)
      ON CONFLICT(incident_id) DO UPDATE SET generation=generation+1,
      metadata_json=excluded.metadata_json, group_key=excluded.group_key, latest_observed_at=CASE WHEN latest_observed_at IS NULL OR excluded.latest_observed_at > latest_observed_at THEN excluded.latest_observed_at ELSE latest_observed_at END, verification_state=CASE WHEN attempt_id IS NOT NULL THEN 'due' ELSE verification_state END, request_id=CASE WHEN attempt_id IS NOT NULL THEN ? ELSE request_id END, verification_due=CASE WHEN attempt_id IS NOT NULL THEN ? ELSE verification_due END''',
      (incident_id,principal(center,token),json.dumps(group,sort_keys=True),group_key(group),group['source_id'],observed,pending_nonce,time.time()))


def view(center, incident_id):
    row = center._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(incident_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['group'] = json.loads(result.pop('metadata_json'))
    result.pop('source_principal')
    result.pop('executor_principal')
    continuation = result.pop('continuation_json')
    receipt = result.pop('last_check_receipt_json')
    if receipt:
        result['last_check_receipt'] = json.loads(receipt)
    if continuation:
        result['continuation'] = json.loads(continuation)
        result['continuation'].pop('delivery_id', None)
    return result


def verifier(center, token, incident_id):
    from .core import AuthorizationError, ValidationError
    center.authorize_incident(token,incident_id)
    row = center._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(incident_id,)).fetchone()
    if not row:
        raise ValidationError('incident has no registered source identity')
    source = json.loads(row['metadata_json'])['source_id']
    scope = center._token_scope(token)
    sources = scope.get('verification_sources', [])
    authenticated = principal(center, token)
    exact = isinstance(sources, (list, tuple)) and source in sources
    owned = row['source_principal'] == authenticated
    dynamic = scope.get('incident_role') == 'verifier' and scope.get('verification_owned_sources') is True
    allowed = (owned and (exact or dynamic)) or (row['source_principal'] == 'historical-unbound' and exact)
    if (not scope.get('principal_id') or not allowed
            or scope.get('incident_role') == 'executor'
            or (row['executor_principal'] and authenticated == row['executor_principal'])):
        raise AuthorizationError('source verification requires a registered independent source principal')
    return row


def finish(center, token, incident_id, key, body):
    from .core import AuthorizationError, ValidationError, IdempotencyConflict
    center.authorize_incident(token,incident_id)
    scope = center._token_scope(token)
    if not scope.get('principal_id') or scope.get('incident_role') != 'executor' or scope.get('verification_sources'):
        raise AuthorizationError('source verification credentials cannot finish executor attempts')
    if not str(key).strip():
        raise ValidationError('Idempotency-Key is required')
    fields = ('attempt_id','session_id','harness')
    if any(not isinstance(body.get(f),str) or not body[f].strip() or len(body[f])>128 for f in fields):
        raise ValidationError('bounded attempt_id, session_id and harness are required')
    payload = {f:body[f] for f in fields}
    payload['evidence_refs'] = center._health_value(body.get('evidence_refs') or [])
    encoded = json.dumps(payload,sort_keys=True)
    with center._lock, center._connection:
        row = center._connection.execute('SELECT * FROM incident_lifecycle WHERE incident_id=?',(incident_id,)).fetchone()
        if not row:
            raise ValidationError('work_finished requires a health source incident')
        key_row = center._connection.execute('SELECT * FROM incident_finish_keys WHERE idempotency_key=?',(key,)).fetchone()
        if key_row and (key_row['incident_id'] != incident_id or key_row['attempt_id'] != body['attempt_id'] or key_row['payload_json'] != encoded):
            raise IdempotencyConflict('Idempotency-Key already bound to another completion')
        previous = center._connection.execute('SELECT * FROM incident_verification_requests WHERE incident_id=? AND attempt_id=?' ,(incident_id,body['attempt_id'])).fetchone()
        if previous:
            if previous['finish_json'] != encoded:
                raise IdempotencyConflict('attempt completion content is already bound')
            if not key_row:
                center._connection.execute('INSERT INTO incident_finish_keys VALUES (?,?,?,?)',(key,incident_id,body['attempt_id'],encoded))
            return json.loads(previous['result_json'])
        if center.get_incident(incident_id)['state']=='resolved':
            raise ValidationError('incident is already resolved')
        bound = center.latest_health_agent_session(incident_id)
        if bound and (bound.get('session_id') != body['session_id'] or bound.get('harness') != body['harness']):
            raise ValidationError('completion must match the incident native session')
        if row['session_id'] and (row['session_id'] != body['session_id'] or row['harness'] != body['harness']):
            raise ValidationError('continuation must preserve the incident native session')
        request_id = 'check_' + uuid.uuid4().hex
        due = time.time()
        result = dict(incident_id=incident_id,request_id=request_id,generation=row['generation'],attempt_state='work_finished',verification_state='due',verification_due=due,**payload)
        center._connection.execute('INSERT INTO incident_finish_keys VALUES (?,?,?,?)',(key,incident_id,body['attempt_id'],encoded))
        center._connection.execute('INSERT INTO incident_verification_requests VALUES (?,?,?,?,?,?)',
            (request_id,incident_id,row['generation'],body['attempt_id'],encoded,json.dumps(result,sort_keys=True)))
        center._connection.execute('''UPDATE incident_lifecycle SET attempt_state='work_finished', verification_state='due',verification_due=?,request_id=?,session_id=?,harness=?,attempt_id=?,executor_principal=?,continuation_json=NULL WHERE incident_id=?''',
            (due,request_id,body['session_id'],body['harness'],body['attempt_id'],principal(center,token),incident_id))
        center._audit(incident_id,'health.work_finished',principal(center,token),result)
        return result


def due_checks(center, token, project, source_id, limit=20):
    from .core import AuthorizationError
    center.authorize_project(token,project)
    scope = center._token_scope(token)
    sources = scope.get('verification_sources', [])
    exact = isinstance(sources, (list, tuple)) and source_id in sources
    dynamic = scope.get('incident_role') == 'verifier' and scope.get('verification_owned_sources') is True
    if not scope.get('principal_id') or not (exact or dynamic) or scope.get('incident_role')=='executor':
        raise AuthorizationError('source verification scope is required')
    authenticated = principal(center, token)
    with center._lock:
        rows=center._connection.execute("SELECT l.* FROM incident_lifecycle l JOIN incidents i ON i.id=l.incident_id WHERE i.project=? AND l.source_id=? AND (l.source_principal=? OR (l.source_principal='historical-unbound' AND ?)) AND i.state!='resolved' AND l.verification_state IN ('due','unknown','degraded') AND l.verification_due<=? ORDER BY l.verification_due LIMIT ?",(project,source_id,authenticated,int(exact),time.time(),max(1,min(100,int(limit))))).fetchall()
        result=[]
        for row in rows:
            if json.loads(row['metadata_json'])['source_id']==source_id:
                verifier(center,token,row['incident_id'])
                result.append(view(center,row['incident_id']))
        return result


def check(center, token, incident_id, key, body):
    from .core import ValidationError, IdempotencyConflict
    if not str(key).strip():
        raise ValidationError('Idempotency-Key is required')
    state=body.get('observed_state')
    if isinstance(body.get('generation'), bool) or not isinstance(body.get('generation'), int):
        raise ValidationError('generation must be an integer')
    if state not in ('healthy','degraded','unknown'):
        raise ValidationError('observed_state must be healthy, degraded or unknown')
    payload={f:body.get(f) for f in ('request_id','generation','observed_state','evidence_refs')}
    encoded=json.dumps(payload,sort_keys=True)
    with center._lock, center._connection:
        row=verifier(center,token,incident_id)
        bind_source(center,token,row)
        previous=center._connection.execute('SELECT * FROM incident_check_receipts WHERE idempotency_key=?',(key,)).fetchone()
        if previous:
            if previous['request_id']!=body.get('request_id') or previous['payload_json']!=encoded:
                raise IdempotencyConflict('check receipt key already bound')
            return json.loads(previous['result_json'])
        if row['request_id']!=body.get('request_id') or row['generation']!=body.get('generation'):
            raise ValidationError('stale verification request or generation')
        if row['verification_state'] not in ('due','unknown','degraded'):
            raise ValidationError('verification request already has a terminal result')
        if state!='unknown' and (not isinstance(body.get('evidence_refs'),list) or not body['evidence_refs']):
            raise ValidationError('a health measurement requires evidence_refs')
        result=dict(incident_id=incident_id,request_id=row['request_id'],generation=row['generation'],observed_state=state)
        if state=='healthy':
            center._transition(incident_id,'resolved',principal(center,token))
            center.schedule_health_outcome(incident_id,'recovered','source-check:'+row['request_id'])
        continuation = json.loads(row['continuation_json']) if row['continuation_json'] else None
        if state == 'degraded' and not continuation:
            continuation = {f:row[f] for f in ('session_id','harness','attempt_id','request_id','generation')}
            continuation['evidence_refs'] = center._health_value(body['evidence_refs'])
            target = center._health_agent_delivery_target()
            target['source_check_continuation'] = dict(continuation)
            continuation['delivery_id'] = center._schedule_delivery(
                incident_id, 'gptadmin.agent:health-diagnosis',
                'source-check:' + row['attempt_id'], time.time(), target)
        if state == 'degraded':
            result['continuation'] = {k:v for k,v in continuation.items() if k != 'delivery_id'}
            result['continuation_delivery_id'] = continuation.get('delivery_id')
        if state == 'healthy':
            continuation = None
        # The accepted receipt keeps the submitted nonce. Only the next round
        # rotates; a lost HTTP response can be reconciled through incident GET.
        receipt = dict(idempotency_key=key,request_id=row['request_id'],result=result)
        center._connection.execute("""UPDATE incident_lifecycle SET verification_state=?,verification_due=?,
          request_id=?,continuation_json=?,last_check_receipt_json=? WHERE incident_id=?""",
          (state,time.time()+60 if state!='healthy' else None,
           'check_' + uuid.uuid4().hex if state!='healthy' else row['request_id'],
           json.dumps(continuation,sort_keys=True) if continuation else None,
           json.dumps(receipt,sort_keys=True),incident_id))
        center._connection.execute('INSERT INTO incident_check_receipts VALUES (?,?,?,?)',(key,row['request_id'],encoded,json.dumps(result,sort_keys=True)))
        center._audit(incident_id,'health.source_check',principal(center,token),{**result,'evidence_refs':center._health_value(body.get('evidence_refs') or [])})
        return result
