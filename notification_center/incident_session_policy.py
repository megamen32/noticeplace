"""Operator-owned session reuse settings carried in signed health deliveries."""
from collections.abc import Mapping

DEFAULT_TTL_SECONDS = 86400
MAX_TTL_SECONDS = 2592000


def normalize_policy(value=None):
    if value is None:
        return {'enabled': True, 'ttlSeconds': DEFAULT_TTL_SECONDS}
    if not isinstance(value, Mapping) or type(value.get('enabled')) is not bool:
        raise ValueError('invalid incident session policy')
    ttl = value.get('ttlSeconds')
    if type(ttl) is not int or not 0 <= ttl <= MAX_TTL_SECONDS:
        raise ValueError('incident session TTL must be an integer between 0 and 2592000 seconds')
    return {'enabled': value['enabled'], 'ttlSeconds': ttl}


def read_policy(center):
    enabled = center.get_runtime_setting('incident_session_stickiness_enabled', '1')
    if enabled not in ('0', '1'):
        raise ValueError('invalid incident session stickiness setting')
    ttl = int(center.get_runtime_setting('incident_session_ttl_seconds', str(DEFAULT_TTL_SECONDS)))
    return normalize_policy({'enabled': enabled == '1', 'ttlSeconds': ttl})
