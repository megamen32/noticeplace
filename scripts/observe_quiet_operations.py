#!/usr/bin/env python3
"""Finite read-only F02 snapshots. Default preview; no sends or recurring scheduler."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import urllib.request

DAY = 86400
MAX_ROWS = 50000
ENDPOINTS = {
    'noticeplace_instructions': 'http://127.0.0.1:8091/v1/instructions',
    'herder_launch_policy': 'http://127.0.0.1:18787/api/automation/launch-policy',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_once(path, value):
    """An existing baseline/result is evidence, never overwrite it on a retry."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def collect_store(connection, *, now=None, max_rows=MAX_ROWS, budget_seconds=60):
    """Only allowlisted metadata leaves storage; never read bodies/errors/receipts."""
    now = time.time() if now is None else now
    started = time.monotonic()
    deliveries, after, complete = [], '', False
    while len(deliveries) < max_rows and time.monotonic()-started < budget_seconds:
        rows = connection.execute('''SELECT d.id,d.incident_id,d.status,d.attempt,
          d.created_at,d.updated_at,d.due_at,
          CASE WHEN d.channel LIKE 'gptadmin.agent:%' THEN 'agent'
               WHEN d.channel LIKE '%.call' THEN 'call' ELSE 'message_or_other' END AS channel_class,
          CASE WHEN (i.kind='incident' OR i.event_type IN ('executor.digest','agent.done'))
                    AND i.severity!='emergency' AND d.channel NOT LIKE 'gptadmin.agent:%'
               THEN 1 ELSE 0 END AS quiet_candidate,
          CASE WHEN json_extract(d.target_json,'$.health_session') IS NOT NULL
                    OR json_extract(d.target_json,'$.health_outcome') IS NOT NULL
               THEN 1 ELSE 0 END AS card_update_candidate
          FROM deliveries d JOIN incidents i ON i.id=d.incident_id
          WHERE d.id>? ORDER BY d.id LIMIT ?''',
          (after, min(500, max_rows-len(deliveries)))).fetchall()
        deliveries.extend(dict(row) for row in rows)
        if not rows:
            complete = True
            break
        after = rows[-1]['id']
    backlog = [dict(row) for row in connection.execute('''SELECT l.verification_state AS state,
        COUNT(*) AS count,COUNT(DISTINCT l.source_id) AS sources,
        MIN(l.verification_due) AS oldest_due_at
        FROM incident_lifecycle l JOIN incidents i ON i.id=l.incident_id
        WHERE i.state!='resolved' AND l.verification_state IN ('due','unknown','degraded')
        AND l.verification_due<=? GROUP BY l.verification_state''', (now,)).fetchall()]
    for item in backlog:
        item['oldest_age_seconds'] = max(0, now-item['oldest_due_at'])
    duplicate_keys = connection.execute('''SELECT COUNT(*) FROM
        (SELECT delivery_key FROM deliveries GROUP BY delivery_key HAVING COUNT(*)>1)''').fetchone()[0]
    duplicate_cards = connection.execute("""SELECT COUNT(*) FROM
        (SELECT incident_id,channel,json_extract(result_json,'$.chat_id') FROM deliveries
         WHERE status='sent' AND json_extract(result_json,'$.message_id') IS NOT NULL
         AND json_extract(result_json,'$.chat_id') IS NOT NULL
         GROUP BY incident_id,channel,json_extract(result_json,'$.chat_id')
         HAVING COUNT(DISTINCT json_extract(result_json,'$.message_id'))>1)""").fetchone()[0]
    duplicate_sessions = connection.execute("""SELECT COUNT(*) FROM
        (SELECT l.group_key FROM incident_lifecycle l JOIN incidents i ON i.id=l.incident_id
         WHERE i.state!='resolved' AND l.session_id IS NOT NULL
         GROUP BY l.group_key HAVING COUNT(DISTINCT l.harness||':'||l.session_id)>1)""").fetchone()[0]
    return {'deliveries': deliveries, 'scan_complete': complete,
            'scan_seconds': time.monotonic()-started, 'duplicate_delivery_keys': duplicate_keys,
            'duplicate_card_receipt_candidates': duplicate_cards,
            'duplicate_active_session_candidates': duplicate_sessions, 'verifier_backlog': backlog}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latency_samples():
    result = {}
    opener = urllib.request.build_opener(NoRedirect())
    for name, url in ENDPOINTS.items():
        samples, codes, errors = [], [], []
        for _ in range(3):
            start = time.monotonic()
            try:
                with opener.open(url, timeout=5) as response:
                    response.read(1024 * 1024)  # Discard response: no payload in artifacts.
                    codes.append(response.status)
                samples.append(round((time.monotonic()-start)*1000, 3))
            except Exception as error:
                errors.append(type(error).__name__)
        result[name] = {'samples_ms': samples, 'codes': codes, 'errors': errors,
                        'median_ms': statistics.median(samples) if samples else None,
                        'max_ms': max(samples) if samples else None}
    return result


def capture():
    pid = subprocess.check_output(['systemctl', 'show', 'noticeplace-mesh-api.service',
                                   '-p', 'MainPID', '--value'], text=True).strip()
    if not pid.isdigit() or int(pid) < 2:
        raise RuntimeError('API process unavailable')
    release = Path('/proc/'+pid+'/cwd').resolve(strict=True)
    if release.parent != Path('/opt/noticeplace-releases'):
        raise RuntimeError('API is not an immutable installed release')
    env = dict(item.decode().split('=', 1) for item in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in item)
    database = env['NOTIFY_CENTER_DB']
    if not database.startswith('mesh:'):
        raise RuntimeError('installed mesh required')
    sys.path.insert(0, str(release))
    from notification_center.mesh_store import connect
    connection = connect(database, readonly=True)
    began = time.time()
    try:
        snapshot = collect_store(connection)
    finally:
        connection.close()
    snapshot.update(schema=1, started_at=began, captured_at=time.time(),
                    release=release.name, api_pid=int(pid),
                    collector_sha256=digest(__file__), latencies=latency_samples())
    return snapshot


def compare(before, after):
    elapsed = after['captured_at'] - before['captured_at']
    if elapsed < DAY:
        raise ValueError('24 hours have not elapsed; observation is incomplete')
    old = {r['id']: r for r in before['deliveries']}
    new = {r['id']: r for r in after['deliveries']}
    missing = len(old.keys()-new.keys())
    resets = sum(r['attempt'] < old[i]['attempt'] for i, r in new.items() if i in old)
    attempt_delta = sum(max(0, r['attempt']-old.get(i, {}).get('attempt', 0)) for i, r in new.items())
    retry_delta = sum(max(0, max(0, r['attempt']-1)-max(0, old.get(i, {}).get('attempt', 0)-1)) for i, r in new.items())
    sent = [r for i, r in new.items() if r['status']=='sent' and old.get(i, {}).get('status')!='sent']
    quiet = [r['id'] for r in sent if r['quiet_candidate']]
    groups = {}
    for r in new.values():
        if r['channel_class']=='agent' and r['status'] in {'queued','claimed','sending'}:
            groups[r['incident_id']] = groups.get(r['incident_id'], 0)+1
    candidates = sum(n-1 for n in groups.values() if n>1)
    incomplete = missing or resets or not before['scan_complete'] or not after['scan_complete']
    duplicate_deltas = {key: after[key]-before[key] for key in (
        'duplicate_delivery_keys', 'duplicate_card_receipt_candidates', 'duplicate_active_session_candidates')}
    latency_errors = any(value.get('errors') for value in after['latencies'].values())
    return {'status': 'REVIEW_REQUIRED' if quiet or candidates or incomplete or latency_errors
            or any(n>0 for n in duplicate_deltas.values()) or before['release']!=after['release'] else 'OBSERVATION_COMPLETE',
            'duplicate_candidate_deltas': duplicate_deltas,
            'verifier_due_count_delta': sum(r['count'] for r in after['verifier_backlog'])-sum(r['count'] for r in before['verifier_backlog']),
            'latency_probe_errors': latency_errors,
            'elapsed_seconds': elapsed, 'baseline_release': before['release'], 'final_release': after['release'],
            'attempt_increments_observed': attempt_delta, 'retry_increments_observed': retry_delta,
            'missing_baseline_rows': missing, 'attempt_counter_resets': resets,
            'retained_row_delta_coverage': not bool(incomplete), 'full_window_coverage': False,
            'duplicate_delivery_keys_before': before['duplicate_delivery_keys'],
            'duplicate_delivery_keys_after': after['duplicate_delivery_keys'],
            'active_agent_duplicate_candidates_after': candidates,
            'new_sent_deliveries_observed': len(sent),
            'quiet_delivery_candidate_ids': quiet[:100], 'quiet_delivery_candidate_count': len(quiet),
            'verifier_backlog_before': before['verifier_backlog'], 'verifier_backlog_after': after['verifier_backlog'],
            'latencies_before': before['latencies'], 'latencies_after': after['latencies'],
            'limitations': ['Two finite snapshots, not continuous latency/SLO monitoring.',
                'Quiet and duplicate candidates require policy/receipt review; no automatic violation claim.',
                'Quiet classification uses snapshot incident state, not historical send-time policy.',
                'Card duplicate candidates cover flat chat_id/message_id receipts only.',
                'Rows created and removed between snapshots are unobserved; retained-row deltas are lower bounds.',
                'No notifications, live probes of business sources, or alarm-policy changes.']}


def rollout_gate(path):
    gate = json.loads(Path(path).read_text())
    for owner in ('galileo', 'kepler'):
        value = gate[owner]
        if value.get('status') != 'complete' or digest(value['receipt']) != value['sha256']:
            raise ValueError('consumer rollout completion evidence missing or changed: '+owner)
    if not gate.get('installed_release'):
        raise ValueError('expected installed release required')
    return gate


def timer_command(baseline, output):
    baseline = Path(baseline).resolve()
    saved = json.loads(baseline.read_text())
    delay = max(1, int(saved['captured_at']+DAY-time.time())+1)
    suffix = hashlib.sha256(str(baseline).encode()).hexdigest()[:12]
    return ['systemd-run', '--unit=noticeplace-f02-'+suffix, '--on-active='+str(delay)+'s',
            '--timer-property=AccuracySec=1s', '--property=TimeoutStartSec=180s',
            '--property=UMask=0077', '/usr/bin/python3', str(Path(__file__).resolve()),
            'compare', '--baseline', str(baseline), '--output', str(Path(output).resolve())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preview', choices=['preview','baseline','compare','timer-command'])
    parser.add_argument('--gate'); parser.add_argument('--baseline'); parser.add_argument('--output')
    args = parser.parse_args(argv)
    if args.action == 'preview':
        print(json.dumps({'status':'PREPARED_NOT_STARTED','snapshots':2,'minimum_elapsed_seconds':DAY,
            'baseline_requires':'Galileo and Kepler completion receipts; expected installed release',
            'automatic_notifications':False,'scheduler':'one optional transient systemd timer'}))
        return
    if args.action == 'timer-command':
        print(json.dumps(timer_command(args.baseline, args.output)))
        return
    output = Path(args.output)
    if args.action == 'baseline':
        gate = rollout_gate(args.gate)
        if (output/'baseline.json').exists():
            raise ValueError('baseline already exists; never replace evidence')
        snapshot = capture()
        if snapshot['release'] != gate['installed_release']:
            raise ValueError('installed release differs from rollout gate')
        snapshot['rollout_gate_sha256'] = digest(args.gate)
        write_once(output/'baseline.json', snapshot)
    else:
        before = json.loads(Path(args.baseline).read_text())
        if time.time()-before['captured_at'] < DAY:
            raise ValueError('24 hours have not elapsed; no final snapshot collected')
        if before['collector_sha256'] != digest(__file__):
            raise ValueError('collector source changed; preserve baseline and review')
        final = output/'final.json'
        baseline_hash = digest(args.baseline)
        after = json.loads(final.read_text()) if final.exists() else capture()
        if final.exists() and after.get('baseline_sha256') != baseline_hash:
            raise ValueError('final snapshot belongs to a different baseline')
        if not final.exists():
            after['baseline_sha256'] = baseline_hash
            write_once(final, after)
        report = compare(before, after)
        report['baseline_sha256'] = digest(args.baseline)
        write_once(output/'comparison.json', report)
    print(json.dumps({'status':'saved','output':str(output)}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Remote SQL/error bodies can contain sensitive material; type only.
        print(json.dumps({'status':'ERROR','error_type':type(error).__name__}), file=sys.stderr)
        raise SystemExit(2)
