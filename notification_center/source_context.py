"""Bounded producer evidence, never launch, delivery or recovery authority."""
from collections.abc import Mapping
import json
import math
import time

SCHEMA = 'notify.source-context.v1'
TEXT_FIELDS = {'service':128, 'impact':1000, 'expected_behavior':600, 'verifier_ref':256}
LIST_FIELDS = {'symptoms':500, 'diagnostics':500, 'allowed_actions':256, 'runbook_refs':256}
TIME_FIELDS = ('observed_at', 'first_seen_at', 'last_good_at')
REQUIRED = ('service','impact','symptoms','observed_at','freshness_seconds','verifier_ref')


def normalize_source_context(value):
    if not isinstance(value, Mapping):
        raise ValueError('source_context must be an object')
    allowed = {'schema','freshness_seconds',*TEXT_FIELDS,*LIST_FIELDS,*TIME_FIELDS}
    if set(value) - allowed:
        raise ValueError('source_context contains unsupported fields')
    if value.get('schema', SCHEMA) != SCHEMA:
        raise ValueError('unsupported source_context schema')
    if len(json.dumps(dict(value),ensure_ascii=False).encode()) > 16000:
        raise ValueError('source_context exceeds 16000 bytes')
    from .health_workflow import sanitize_bounded_text
    result = {'schema':SCHEMA}
    for key, limit in TEXT_FIELDS.items():
        if key in value:
            if not isinstance(value[key],str) or len(value[key]) > limit:
                raise ValueError(f'source_context {key} must be bounded text')
            result[key] = sanitize_bounded_text(value[key],limit)
    for key, limit in LIST_FIELDS.items():
        if key in value:
            items = value[key]
            if not isinstance(items,list) or len(items)>8 or any(not isinstance(x,str) or len(x)>limit for x in items):
                raise ValueError(f'source_context {key} must be a bounded text list')
            result[key] = [sanitize_bounded_text(item,limit) for item in items if item.strip()]
    for key in TIME_FIELDS:
        if key in value:
            stamp = value[key]
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp < 0:
                raise ValueError(f'source_context {key} must be a finite Unix timestamp')
            result[key] = stamp
    if 'freshness_seconds' in value:
        seconds = value['freshness_seconds']
        if type(seconds) is not int or not 1 <= seconds <= 86400:
            raise ValueError('source_context freshness_seconds must be an integer 1..86400')
        result['freshness_seconds'] = seconds
    return result


def context_for_delivery(value, *, now=None):
    # Computed status is not stored in the idempotent producer payload. On each
    # delivery the server reevaluates freshness; the source cannot self-certify.
    raw = {key:item for key,item in (value or {}).items() if key not in {'context_status','missing_fields'}}
    context = normalize_source_context(raw)
    missing = [key for key in REQUIRED if not context.get(key)]
    status = 'incomplete' if missing else 'complete'
    now = time.time() if now is None else now
    if not missing and not 0 <= now-context['observed_at'] <= context['freshness_seconds']:
        status = 'stale'
    return {**context,'context_status':status,'missing_fields':missing}
