"""NOTICEPLACE_CONSUMER_V2: source-owned, fresh verification before event ingress.

This stdlib-only module may be installed next to an existing producer adapter.
It never launches an executor and must receive a separate verifier credential.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
from pathlib import Path
import urllib.parse
import urllib.error
import urllib.request
import time
import uuid
from typing import Callable

NOTICEPLACE_CONSUMER_V2 = 2


class VerificationPending(RuntimeError):
    """Dependent recovery needs receipt reconciliation; new faults stay independent."""


class SourceVerifier:
    def __init__(self, base_url: str, token: str, project: str, source_id: str,
                 host_id: str, state_dir: str | Path, *, transport=None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.project, self.source_id, self.host_id = project, source_id, host_id
        self.state_dir = Path(state_dir)
        self.transport = transport or self._http

    def _http(self, method, path, body=None, key=None):
        headers = {'Authorization': 'Bearer ' + self.token}
        if key:
            headers['Idempotency-Key'] = key
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base_url + path, data=data,
                                         headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.load(response)

    def _save(self, path, value):
        temporary = path.with_suffix('.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def tick(self, probe: Callable[[dict], dict], *, max_checks: int = 20,
             deadline_seconds: float | None = None,
             accept_group: Callable[[dict], bool] | None = None) -> list[dict]:
        """Pull -> fresh source probe -> persist intent -> submit -> persist receipt.

        Call before ordinary events for the same episode/generation. Exceptions fence
        dependent recovery, not registration of an independent new fault.
        A crash/network ambiguity after intent never automatically replays POST.
        Unknown measurements may be checked again on the server's next due tick.
        The optional deadline limits admission of additional checks; an in-flight
        HTTP request or probe must enforce its own timeout. Unprocessed checks
        stay due on the server and are pulled on the next existing timer tick.
        accept_group selects an owned episode before any probe or result POST.
        """
        if isinstance(max_checks, bool) or not isinstance(max_checks, int) or not 1 <= max_checks <= 100:
            raise ValueError('max_checks must be between 1 and 100')
        if deadline_seconds is not None and (isinstance(deadline_seconds, bool) or
                not isinstance(deadline_seconds, (int, float)) or not math.isfinite(deadline_seconds) or deadline_seconds <= 0):
            raise ValueError('deadline_seconds must be a positive finite number')
        deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_fd = os.open(self.state_dir / 'verifier.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.state_dir / 'verification.json'
            receipts = []
            saved = json.loads(path.read_text()) if path.exists() else {}
            binding = {'project': self.project, 'source_id': self.source_id, 'host_id': self.host_id}
            if saved and saved.get('binding') != binding:
                raise VerificationPending('state directory belongs to another source identity')
            if saved.get('status') == 'rejected_stale':
                incident = urllib.parse.quote(saved['incident_id'], safe='')
                current = self.transport('GET', '/v1/incidents/' + incident)
                lifecycle = current.get('lifecycle') or {}
                group = lifecycle.get('group') or {}
                generation = lifecycle.get('generation')
                if (any(group.get(field) != value for field, value in binding.items())
                        or isinstance(generation, bool) or not isinstance(generation, int)
                        or not lifecycle.get('request_id')
                        or (lifecycle['request_id'], generation) == (
                            saved['body']['request_id'], saved['body']['generation'])):
                    raise VerificationPending('rejected source check needs a verified superseding request')
                saved.update(status='superseded')
                self._save(path, saved)
            if saved.get('status') == 'submitting':
                incident = urllib.parse.quote(saved['incident_id'], safe='')
                current = self.transport('GET', '/v1/incidents/' + incident)
                receipt = (current.get('lifecycle') or {}).get('last_check_receipt') or {}
                result = receipt.get('result') or {}
                if (receipt.get('idempotency_key') != saved['idempotency_key']
                        or receipt.get('request_id') != saved['body']['request_id']
                        or result.get('generation') != saved['body']['generation']
                        or result.get('observed_state') != saved['body']['observed_state']):
                    raise VerificationPending('source check submission requires exact receipt reconciliation')
                saved.update(status='confirmed', receipt=result)
                self._save(path, saved)
                receipts.append(result)
            query = urllib.parse.urlencode({'project': self.project, 'source_id': self.source_id})
            requests = self.transport('GET', '/v1/verification-requests?' + query)['requests']
            processed = 0
            for request in requests:
                if processed >= max_checks or (deadline is not None and time.monotonic() >= deadline):
                    break
                group = request['group']
                if (group.get('project'), group.get('source_id'), group.get('host_id')) != (
                        self.project, self.source_id, self.host_id):
                    raise ValueError('verification request does not match source identity')
                if accept_group is not None and not accept_group(dict(group)):
                    continue
                processed += 1
                if isinstance(request['generation'], bool) or not isinstance(request['generation'], int):
                    raise ValueError('invalid verification generation')
                observation = probe(dict(group))
                state = observation.get('observed_state')
                evidence = observation.get('evidence_refs')
                if state not in ('healthy', 'degraded', 'unknown') or not isinstance(evidence, list):
                    raise ValueError('invalid source observation')
                if not evidence or not all(isinstance(ref, str) and ref.strip() for ref in evidence):
                    raise ValueError('fresh source evidence is required')
                body = {'request_id': request['request_id'], 'generation': request['generation'],
                        'observed_state': state, 'evidence_refs': evidence}
                key = 'source-check:' + uuid.uuid4().hex
                intent = {'status': 'submitting', 'binding': binding, 'incident_id': request['incident_id'],
                          'idempotency_key': key, 'body': body}
                self._save(path, intent)
                incident = urllib.parse.quote(request['incident_id'], safe='')
                try:
                    receipt = self.transport('POST', '/v1/incidents/' + incident + '/health/check-result', body, key)
                except urllib.error.HTTPError as error:
                    # Only this definitive domain rejection proves the old POST did
                    # not commit. Timeouts, 5xx and other errors retain the fence.
                    if error.code == 400:
                        try:
                            rejected = json.loads(error.read(4096))
                        except (ValueError, OSError):
                            rejected = None
                        if rejected == {'error': 'stale verification request or generation'}:
                            intent.update(status='rejected_stale')
                            self._save(path, intent)
                    raise
                intent.update(status='confirmed', receipt=receipt)
                self._save(path, intent)
                receipts.append(receipt)
            return receipts
