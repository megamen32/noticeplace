"""One durable incident call episode: Matrix, Telegram, then cellular.

Continuation spends the same episode's call budget only after a proven missed
call. Unknown provider outcomes never grant another attempt or another channel.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

ORDER = ('matrix.call', 'telegram.call', 'android.phone.call')


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
    result = {k: value[k] for k in ('ok','answered','safe_to_fallback','ended','uncertain')
              if k in value and (type(value[k]) is bool or value[k] is None)}
    for key in ('actor','status','call_id','callId','channel','target','error'):
        if isinstance(value.get(key), str): result[key] = value[key][:256]
    return result


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
            if error.code not in {401,403,404}: raise
            return {'answered':False,'ended':True,'safe_to_fallback':True,'status':'unavailable'}
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
