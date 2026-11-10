"""Optional, short-lived access to Herder's authoritative launch policy."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class HerderPolicyUnavailable(RuntimeError):
    pass


class HerderPolicyClient:
    MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(self, url=None, runner=None, timeout=1.0):
        configured = url or os.environ.get('NOTIFY_HEALTH_REMEDIATION_URL', 'http://127.0.0.1:18787/api/sessions/new-or-resume')
        parsed = urllib.parse.urlsplit(configured)
        self.url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/api/automation/launch-policy', '', ''))
        self.runner = runner or urllib.request.urlopen
        self.timeout = timeout

    def read(self):
        try:
            from .agent_job_helper import _herder_authorized_headers
            headers = _herder_authorized_headers(self.url, {'Accept': 'application/json'})
            with self.runner(urllib.request.Request(self.url, headers=headers), timeout=self.timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(raw) > self.MAX_RESPONSE_BYTES:
                    raise ValueError('policy response too large')
                policy = json.loads(raw)
                etag = response.headers.get('ETag')
            if not isinstance(policy, dict) or not etag:
                raise ValueError('missing policy or ETag')
            return policy, etag
        except (OSError, ValueError, TypeError) as error:
            raise HerderPolicyUnavailable('Настройки Herder недоступны') from error

    def set_incident_model(self, model, expected_revision):
        policy, etag = self.read()
        if not expected_revision or etag != expected_revision:
            raise HerderPolicyUnavailable('Настройки Herder изменились; обновите страницу')
        execution = policy.get('incidentExecution')
        if not isinstance(execution, dict) or not model or '/' not in model:
            raise HerderPolicyUnavailable('Маршрут модели Herder недоступен или некорректен')
        provider, _, _ = model.partition('/')
        policy['incidentExecution'] = {**execution, 'provider': provider, 'model': model}
        from .agent_job_helper import _herder_authorized_headers
        headers = _herder_authorized_headers(self.url, {'Content-Type': 'application/json', 'If-Match': expected_revision})
        request = urllib.request.Request(self.url, data=json.dumps(policy).encode(), method='PUT', headers=headers)
        try:
            with self.runner(request, timeout=self.timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(raw) > self.MAX_RESPONSE_BYTES:
                    raise ValueError('policy response too large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 412:
                raise HerderPolicyUnavailable('Настройки Herder изменились; обновите страницу') from error
            raise HerderPolicyUnavailable('Herder отклонил изменение настроек') from error
        except (OSError, ValueError) as error:
            raise HerderPolicyUnavailable('Не удалось изменить настройки Herder') from error
