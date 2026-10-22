"""One durable incident call episode: Matrix, Telegram, then cellular.

Continuation spends the same episode's call budget only after a proven missed
call. Unknown provider outcomes never grant another attempt or another channel.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

ORDER = ('matrix.call', 'telegram.call', 'android.phone.call')
HTTP_ERROR_BODY_LIMIT = 64 * 1024
HTTP_ERROR_METADATA_KEYS = ('code', 'error', 'type', 'status', 'message', 'detail')

_BEARER = re.compile(r'(?i)\bBearer\s+[^\s,;]+')
_SECRET_ASSIGNMENT = re.compile(
    r'(?i)\b(?:authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|token|secret|password|cookie)\b'
    r'\s*[:=]\s*[^\s,;]+'
)
_URL = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_EMAIL = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.IGNORECASE)
_PHONE = re.compile(r'(?<!\w)\+?\d(?:[\d().\s-]{5,}\d)(?!\w)')
_LONG_TOKEN = re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9])')
_METADATA_KEY = re.compile(r'^[a-zA-Z][a-zA-Z0-9_.-]{0,47}$')


def intended(row):
    if ':voice-chain:' in str(row['delivery_key'] or ''): return True
    try:
        target = json.loads(row['target_json'] or '{}')
        return isinstance(target, dict) and 'incident_call_chain' in target
    except (TypeError, ValueError, KeyError):
        return False


def metadata(row):
    try:
        target = json.loads(row['target_json'] or '{}')
        if not isinstance(target, dict): return None
        chain = target.get('incident_call_chain')
        if not isinstance(chain, dict): return None
        index = chain.get('index')
        if (type(index) is not int or not 0 <= index < len(ORDER)
            or row['channel'] != ORDER[index]
            or chain.get('root') != str(row['incident_id']) + ':voice-chain'
            or row['delivery_key'] != f"{row['incident_id']}:{ORDER[index]}:voice-chain:{index}"):
            return None
        return chain
    except (TypeError, ValueError, KeyError):
        return None


def safe_missed(receipt):
    return (isinstance(receipt, Mapping) and receipt.get('answered') is False
            and receipt.get('safe_to_fallback') is True and receipt.get('ended') is True
            and receipt.get('uncertain') is not True)


def receipt_projection(value):
    """Bound strings without truncating the serialized proof into invalid JSON."""
    if not isinstance(value, Mapping): return {}
    result = {k: value[k] for k in ('ok','answered','acknowledged','safe_to_fallback','ended','uncertain')
              if k in value and (type(value[k]) is bool or value[k] is None)}
    for key in ('actor','status','call_id','callId','channel','target','error'):
        if isinstance(value.get(key), str): result[key] = value[key][:256]
    http_status=value.get('http_status')
    if type(http_status) is int and 100 <= http_status <= 599:
        result['http_status']=http_status
    metadata=_safe_error_metadata(value.get('response_metadata'))
    if metadata: result['response_metadata']=metadata
    body_length=value.get('body_length')
    if type(body_length) is int and body_length >= 0:
        result['body_length']=body_length
    body_sha256=value.get('body_sha256')
    if isinstance(body_sha256,str) and re.fullmatch(r'[a-f0-9]{64}',body_sha256):
        result['body_sha256']=body_sha256
    if type(value.get('body_truncated')) is bool:
        result['body_truncated']=value['body_truncated']
    body_read_error=value.get('body_read_error')
    if isinstance(body_read_error,str) and _METADATA_KEY.fullmatch(body_read_error):
        result['body_read_error']=body_read_error
    return result


def _safe_diagnostic_text(value):
    if not isinstance(value,str): return None
    text=value.strip()
    if not text or len(text)>512: return None
    text=_BEARER.sub('Bearer [redacted]',text)
    text=_SECRET_ASSIGNMENT.sub('[secret redacted]',text)
    text=_URL.sub('[url redacted]',text)
    text=_EMAIL.sub('[email redacted]',text)
    text=_PHONE.sub('[number redacted]',text)
    text=_LONG_TOKEN.sub('[token redacted]',text)
    return text[:256] or None


def _safe_error_metadata(value):
    """Keep only small, sanitized top-level fields from a JSON error object."""
    if not isinstance(value,Mapping): return {}
    result={}
    for key in HTTP_ERROR_METADATA_KEYS:
        item=value.get(key)
        if isinstance(item,str):
            safe=_safe_diagnostic_text(item)
            if safe is not None: result[key]=safe
        elif key in {'code','status'} and type(item) is int and 0 <= item <= 9999:
            result[key]=item
    return result


def _http_error_receipt(error):
    """Retain bounded HTTP diagnostics while leaving admission outcome uncertain."""
    receipt={'answered':None,'uncertain':True,'status':'unknown','error':'HTTPError'}
    code=getattr(error,'code',None)
    if type(code) is int and 100 <= code <= 599: receipt['http_status']=code
    try:
        raw=error.read(HTTP_ERROR_BODY_LIMIT+1)
    except Exception as read_error:
        receipt['body_read_error']=type(read_error).__name__
        return receipt
    if not isinstance(raw,bytes): raw=bytes(raw or b'')
    truncated=len(raw)>HTTP_ERROR_BODY_LIMIT
    body=raw[:HTTP_ERROR_BODY_LIMIT]
    metadata={}
    if not truncated:
        try:
            decoded=json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError,ValueError):
            decoded=None
        metadata=_safe_error_metadata(decoded)
    if metadata:
        receipt['response_metadata']=metadata
    else:
        receipt['body_length']=len(body)
        receipt['body_sha256']=hashlib.sha256(body).hexdigest()
        receipt['body_truncated']=truncated
    return receipt


def start(center, incident_id, due_at):
    incident = center.get_incident(incident_id)
    if incident is None or incident['state'] not in ('open','snoozed'): return None
    root = incident_id + ':voice-chain'
    return center._schedule_delivery(incident_id, ORDER[0], 'voice-chain:0', due_at,
        {'incident_call_chain': {'root': root, 'index': 0}})


def advance(center, row, outcome, receipt, now):
    chain = metadata(row)
    if chain is None or outcome != 'failed' or not safe_missed(receipt): return
    if row['state'] not in ('open','snoozed') or chain['index'] == len(ORDER)-1: return
    index = chain['index'] + 1
    center._schedule_delivery(str(row['incident_id']), ORDER[index], f'voice-chain:{index}', now,
        {'incident_call_chain': {'root': chain['root'], 'index': index, 'previous': str(row['id'])}})


def continuation(center, row, budget):
    """Validate the persisted exact predecessor, not a caller's continuation flag."""
    chain = metadata(row)
    if chain is None or chain['index'] == 0 or budget is None: return False
    if chain.get('previous') != budget['last_delivery_id']: return False
    prior = center._connection.execute('SELECT * FROM deliveries WHERE id=?',
                                        (chain['previous'],)).fetchone()
    if prior is None or prior['incident_id'] != row['incident_id'] or prior['status'] != 'failed': return False
    previous = metadata(prior)
    if (previous is None or previous['root'] != chain['root']
        or previous['index'] + 1 != chain['index']): return False
    try: receipt = json.loads(prior['result_json'] or '{}')
    except (ValueError, TypeError): return False
    return safe_missed(receipt)


class IncidentCallSender:
    """Use the existing UserIO account-bound NoticePlace endpoint, once."""
    def __init__(self, url, token, timeout=450, runner=urllib.request.urlopen):
        # UserIO's extended Telegram setup/teardown can take535s. The outer
        # HTTP wait must retain that receipt instead of timing out at450s.
        timeout = max(timeout, 550) if urllib.parse.urlsplit(url).path.endswith('/calls/telegram/noticeplace') else timeout
        self.url, self.token, self.timeout, self.runner = url, token, timeout, runner

    def send(self, payload, delivery_key=None):
        incident = payload['incident']
        body = {k: str(incident.get(k) or '') for k in ('title','body','project','severity')}
        body['incident_id'] = str(incident['id'])
        if delivery_key is not None:
            body['delivery_key'] = delivery_key
        request = urllib.request.Request(self.url, method='POST',
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        try:
            with self.runner(request, timeout=self.timeout) as response:
                raw = response.read(65537)
                if len(raw) > 65536: raise RuntimeError('call receipt exceeds limit')
                result = json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code in {401,403,404}:
                return {'answered':False,'ended':True,'safe_to_fallback':True,'status':'unavailable'}
            return _http_error_receipt(error)
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ConnectionRefusedError): raise
            return {'answered':False,'ended':True,'safe_to_fallback':True,'status':'unavailable'}
        if not isinstance(result, dict): raise RuntimeError('invalid call receipt')
        if result.get('ok') is True and result.get('answered') is True:
            actor = result.get('actor') or result.get('target')
            if not actor: raise RuntimeError('answered call has no recipient')
            result['actor'] = str(result.get('channel') or 'call') + ':' + str(actor)
            return receipt_projection(result)
        if result.get('ok') is True and safe_missed(result): return receipt_projection(result)
        return {'answered':None, 'uncertain':True, 'status':'unknown',
                'error':'call_outcome_unknown'}
