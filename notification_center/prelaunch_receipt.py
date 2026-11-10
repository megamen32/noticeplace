"""Validate explicit negative admission evidence without inferring it from errors."""
from collections.abc import Mapping

_ALLOWED = {('agent-herder-owner-disarm', 'herder_owner_state_unknown'),
            ('agent-herder-owner-disarm', 'herder_preflight_unavailable'),
            ('agent-herder', 'herder_rejected'), ('agent-herder', 'legacy_model_unknown')}


def no_admission_contradictions(value):
    """Reject any admitted, pending, malformed or nonempty attempt evidence."""
    if not isinstance(value, Mapping):
        return False
    for key in ('created', 'accepted', 'admitted', 'pending', 'uncertain',
                'admissionUnknown', 'admission_unknown', 'in_flight', 'inFlight'):
        if key in value and value[key] is not False:
            return False
    for key in ('session_id', 'sessionId', 'session', 'session_ref', 'sessionRef'):
        if key in value and value[key] not in (None, ''):
            return False
    if 'attempts' in value and (not isinstance(value['attempts'], list) or value['attempts']):
        return False
    if 'delivery' in value and value['delivery'] not in (None, '', 'not_attempted'):
        return False
    if 'admission' in value and value['admission'] != 'not_attempted':
        return False
    return True


def valid_prelaunch_negative(value, incident_id=None):
    if not isinstance(value, Mapping):
        return False
    def identity(item):
        return isinstance(item, str) and bool(item.strip()) and len(item) <= 128 and not any(ord(c) < 32 or ord(c) == 127 for c in item)
    source, reason = value.get('source'), value.get('reason')
    return bool(isinstance(source, str) and isinstance(reason, str) and (source, reason) in _ALLOWED
        and value.get('status') == 'source_blocked' and value.get('admission') == 'not_attempted'
        and identity(value.get('operation_id')) and identity(value.get('incident_id'))
        and (incident_id is None or value['incident_id'] == incident_id)
        and value.get('human_held') is True and value.get('retryable') is False and value.get('non_retryable') is True
        and no_admission_contradictions(value)
        and value.get('outcome') in (None, '', 'no_harness', 'pre_admission_rejected'))
