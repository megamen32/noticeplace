"""F01: repeated real HTTP intake, durable dispatch fencing; no native transports.

The executor below is an instrumented local boundary, not a real native session.
The separate installed live acceptance receipt supplies the native proof.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
import socket
import threading
import time
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import pytest

from notification_center.core import NotificationCenter
from notification_center.http_api import DeliveryWorker, build_handler
from notification_center.source_gate import source_launch_fields

TOKENS = {'isolated-source': {'project': 'quiet-repeat-fixture',
    'max_severity': 'critical', 'agent_jobs': ['health-diagnosis']}}
EVENT = dict(schema='notify.event.v1', kind='incident', project='quiet-repeat-fixture',
    recipient='isolated-no-human', severity='critical', title='Изолированная проверка',
    body='Один источник и один эпизод без внешних действий', dedup_key='one-problem',
    event_type='health.degraded', source_id='isolated-probe', host_id='isolated-host',
    signal_type='http', correlation_id='isolated-episode')


def center_at(path):
    return NotificationCenter(path, TOKENS, default_quiet_hours=[])


def api_process(path, control):
    """Separate API process; restart preserves only the temporary SQLite file."""
    center = center_at(path)
    handler = build_handler(center, 'isolated-health', 'isolated-mcp')
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.request_queue_size = 32
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    control.send((server.server_port, os.getpid()))
    try:
        assert control.recv() == 'stop'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        center._connection.close()
        control.close()


class LocalAPI:
    def __init__(self, path, allowed, event):
        self.event = event
        parent, child = multiprocessing.get_context('fork').Pipe()
        self.control = parent
        self.process = multiprocessing.get_context('fork').Process(
            target=api_process, args=(path, child))
        self.process.start()
        child.close()
        assert parent.poll(15), 'isolated API failed to start'
        self.port, self.pid = parent.recv()
        allowed.add(('127.0.0.1', self.port))
        self.url = f'http://127.0.0.1:{self.port}/v1/events'

    def post(self, sequence):
        request = urllib.request.Request(self.url, method='POST',
            data=json.dumps(self.event).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer isolated-source',
                'Idempotency-Key': f'isolated-observation-{sequence}'})
        with urllib.request.urlopen(request, timeout=20) as response:
            assert response.status == 202
            return json.load(response)

    def batch(self, start, end):
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(self.post, range(start, end)))

    def close(self):
        if self.process.is_alive():
            self.control.send('stop')
            self.process.join(timeout=10)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
            raise AssertionError('isolated API did not stop gracefully')
        self.control.close()
        assert self.process.exitcode == 0


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    # Reject any accidental production I/O, including inherited proxy endpoints.
    for name in list(os.environ):
        if name.upper().endswith('_PROXY') or name.startswith(('NOTIFY_', 'NOTICEPLACE_')):
            monkeypatch.delenv(name)
    monkeypatch.setenv('TELEGRAM_ACTIVE_MODES_JSON', '["decisions","notify","log"]')
    allowed = set()
    original = socket.socket.connect
    def loopback_only(sock, address):
        assert address in allowed, f'external connection forbidden: {address!r}'
        return original(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', loopback_only)
    apis, centers = [], []
    class Sandbox:
        path = tmp_path / 'isolated.sqlite'
        event = dict(EVENT, correlation_id='isolated-' + uuid.uuid4().hex)
        def api(self):
            api = LocalAPI(self.path, allowed, self.event)
            apis.append(api)
            return api
        def center(self):
            center = center_at(self.path)
            centers.append(center)
            return center
    try:
        yield Sandbox()
    finally:
        for api in apis:
            if api.process.is_alive():
                api.close()
        for center in centers:
            center._connection.close()


class HumanTrap:
    def __init__(self):
        self.calls = 0
    def send(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('human delivery forbidden')


class SimulatedCrash(BaseException):
    pass


class LocalExecutor:
    """Real source gate with a local authority response; never calls Herder/LLM."""
    def __init__(self, mode='accepted'):
        self.mode = mode
        self.gate_checks = 0
        self.intended = []
    def send(self, payload, key):
        def read_authority(request, timeout):
            self.gate_checks += 1
            assert 'consume=0' in request.full_url
            return {'humanStopHeld': self.mode == 'stop'}
        source_launch_fields({'url': 'http://isolated.invalid', 'cwd': '/isolated'},
            [{'harness': 'opencode', 'sessionId': 'fixture-source-session'}], read_authority)
        self.intended.append(key)
        if self.mode == 'unknown':
            raise TimeoutError('isolated acceptance receipt lost')
        if self.mode == 'crash':
            raise SimulatedCrash('isolated worker died after durable sending reservation')
        return {'status': 'completed', 'job_id': 'isolated-intended-attempt',
                'result': {'evidence_refs': ['fixture:local-executor-boundary']}}


def worker(center, adapter, trap):
    return DeliveryWorker(center, trap, agent_jobs={'health-diagnosis': adapter},
                          message_adapters={}, call_adapters={})


def facts(center, incident, adapter, trap):
    db = center._connection
    deliveries = [dict(row) for row in db.execute(
        'SELECT id,channel,status,attempt FROM deliveries WHERE incident_id=?', (incident,))]
    assert len(deliveries) == 1, deliveries
    assert deliveries[0]['channel'] == 'gptadmin.agent:health-diagnosis'
    assert trap.calls == 0
    assert db.execute('SELECT COUNT(*) FROM human_requests').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0] == 1
    return {'deliveries': deliveries, 'intended_executor_calls': len(adapter.intended),
            'human_adapter_calls': trap.calls, 'native_sessions_created': 0}


def test_1000_concurrent_intakes_one_attempt_across_api_restart(sandbox):
    api = sandbox.api()
    responses = api.batch(0, 500)
    center = sandbox.center()
    first_claim = center.claim_due_deliveries(channel_group='agent')[0]
    assert first_claim['attempt'] == 1
    center._connection.close()
    old_pid = api.pid
    api.close()
    api = sandbox.api()
    assert api.pid != old_pid
    center = sandbox.center()
    adapter, trap = LocalExecutor(), HumanTrap()
    dispatcher = worker(center, adapter, trap)
    rival_center = sandbox.center()
    # Competing workers and fresh API intake overlap the preserved claim.
    with ThreadPoolExecutor(max_workers=3) as pool:
        intake = pool.submit(api.batch, 500, 1000)
        rival = pool.submit(rival_center.claim_due_deliveries, channel_group='agent')
        delivery = pool.submit(dispatcher.deliver, first_claim)
        responses.extend(intake.result())
        assert rival.result() == []
        delivery.result()
    assert len({response['incident_id'] for response in responses}) == 1
    incident = responses[0]['incident_id']
    assert center.get_incident(incident)['occurrences'] == 1000
    assert center._connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1000
    assert len(adapter.intended) == 1
    dispatcher.deliver(first_claim)  # stale worker cannot cross reservation twice
    assert center.claim_due_deliveries(now_epoch=time.time()+86400) == []
    proof = facts(center, incident, adapter, trap)
    assert proof['intended_executor_calls'] == 1
    assert proof['deliveries'][0]['status'] == 'sent'
    assert proof['deliveries'][0]['attempt'] == 1
    proof.update(http_requests=1000, concurrent_clients=8, unique_idempotency_keys=1000,
                 api_process_restarted=True, durable_claim_preserved=True)
    print('QUIET_REPEAT_PROOF=' + json.dumps(proof, sort_keys=True))


@pytest.mark.parametrize('mode,status,expected_calls', [
    ('unknown', 'uncertain', 1), ('crash', 'sending', 1), ('stop', 'cancelled', 0)])
def test_stop_unknown_and_sending_crash_not_replayed(sandbox, mode, status, expected_calls):
    api = sandbox.api()
    created = api.post(0)
    center = sandbox.center()
    claim = center.claim_due_deliveries(channel_group='agent')[0]
    adapter, trap = LocalExecutor(mode), HumanTrap()
    dispatcher = worker(center, adapter, trap)
    if mode == 'crash':
        with pytest.raises(SimulatedCrash):
            dispatcher.deliver(claim)
    else:
        dispatcher.deliver(claim)
    before = facts(center, created['incident_id'], adapter, trap)
    assert before['deliveries'][0]['status'] == status
    center._connection.close()
    api.close()
    api = sandbox.api()
    assert {r['incident_id'] for r in api.batch(1, 41)} == {created['incident_id']}
    center = sandbox.center()
    assert center.claim_due_deliveries(now_epoch=time.time()+86400) == []
    worker(center, adapter, trap).deliver(claim)
    after = facts(center, created['incident_id'], adapter, trap)
    assert after == before
    assert len(adapter.intended) == expected_calls
    assert adapter.gate_checks == 1
    print('QUIET_FENCE_PROOF=' + json.dumps(dict(mode=mode, **after), sort_keys=True))
