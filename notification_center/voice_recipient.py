"""Recipient-bound voice capability over existing UserIO notice-service trust.

Only the UserIO backend signs an incoming call. The model never selects an
incident or supplies an identity; NoticePlace resolves its own frozen delivery.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid

from .core import ValidationError


def initialize(center):
    with center._lock, center._connection:
        center._connection.execute('''CREATE TABLE IF NOT EXISTS voice_recipient_bindings (
            call_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL UNIQUE,
            proof_hash TEXT NOT NULL, scope_json TEXT NOT NULL, expires_at REAL NOT NULL)''')


def explicit_ack(text):
    # Match the voice client's conservative guard. Quoting, negation and
    # hypothetical statements never authorize this action.
    value = str(text or '').casefold().replace('ё', 'е')
    if re.search(r'[?"«»“”]|\b(?:не|нет|если|когда|допустим)\b', value):
        return False
    value = ' '.join(re.sub(r'[.,!;:]', ' ', value).split())
    value = re.sub(r'^(?:да |пожалуйста )+', '', value)
    value = re.sub(r'(?: пожалуйста| спасибо)+$', '', value)
    return bool(re.fullmatch(
        r'(?:я )?(?:(?:получил|получила|получили|принял|приняла) '
        r'(?:(?:это|тестовое|ваше|данное) )*(?:уведомление|сообщение)|'
        r'(?:(?:это|тестовое|ваше|данное) )*(?:уведомление|сообщение) '
        r'(?:получил|получила|получено|принято)|'
        r'подтверждаю(?: (?:(?:этот|данный|тестовый) )?инцидент| получение(?: уведомления)?)?|'
        r'подтверди(?: (?:(?:этот|данный|тестовый) )?инцидент| получение(?: уведомления)?)?|'
        r'принято|останови эскалацию)', value))


class VoiceRecipientContext:
    def __init__(self, center, service_key):
        self.center, self.key = center, str(service_key or '')

    def _verify(self, proof):
        try:
            if not self.key or not isinstance(proof, str) or len(proof) > 4096:
                raise ValueError()
            encoded, signature = proof.split('.')
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            expected = hmac.new(self.key.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            fields = json.loads(raw)
            if (fields.get('v') != 1 or type(fields.get('expires_at')) is not int
                    or not time.time() < fields['expires_at'] <= time.time() + 600
                    or any(not isinstance(fields.get(k), str) or not 1 <= len(fields[k]) <= 256
                           for k in ('call_id','receipt_id','caller','account_ref','room_id'))
                    or not fields['caller'].startswith('@') or not fields['room_id'].startswith('!')
                    or not fields['account_ref'].startswith('matrix:')):
                raise ValueError()
            uuid.UUID(fields['call_id'])
            return fields, hashlib.sha256(raw).hexdigest()
        except (ValueError, TypeError, KeyError):
            raise ValidationError('invalid or expired server voice receipt') from None

    @staticmethod
    def _matches(row, fields):
        target = json.loads(row['target_json'] or '{}')
        result = json.loads(row['result_json'] or '{}')
        recipient = target.get('recipient') or target.get('target')
        return (row['status'] == 'sent' and recipient == fields['caller']
                and (not target.get('recipient') or not target.get('target') or target['recipient'] == target['target'])
                and target.get('account_ref') == fields['account_ref']
                and result.get('account_ref') == fields['account_ref']
                and result.get('room_id') == target.get('room_id')
                and isinstance(result.get('event_id'), str) and result['event_id'].startswith('$'))

    def apply(self, body):
        fields, proof_hash = self._verify(body.get('proof'))
        action = body.get('action')
        if action not in ('resolve','read','ack'):
            raise ValidationError('unsupported recipient voice action')
        if action == 'ack' and not explicit_ack(body.get('utterance')):
            raise ValidationError('an explicit current receipt statement is required')
        center = self.center
        with center._lock, center._connection:
            stored = center._connection.execute(
                'SELECT * FROM voice_recipient_bindings WHERE call_id=?', (fields['call_id'],)).fetchone()
            if stored is None:
                if action != 'resolve':
                    raise ValidationError('no current recipient binding')
                rows = center._connection.execute(
                    "SELECT d.*, i.project, i.correlation_id FROM deliveries d JOIN incidents i ON i.id=d.incident_id "
                    "WHERE d.channel='matrix.message' AND d.status='sent' AND i.state IN ('open','snoozed') "
                    "AND COALESCE(json_extract(d.target_json,'$.recipient'),json_extract(d.target_json,'$.target'))=? "
                    "AND json_extract(d.target_json,'$.account_ref')=? ORDER BY d.updated_at DESC LIMIT 201",
                    (fields['caller'],fields['account_ref'])).fetchall()
                matches = [row for row in rows if self._matches(row, fields)]
                incidents = {row['incident_id'] for row in matches}
                if len(incidents) != 1 or len(rows) == 201:
                    raise ValidationError('multiple recipient notices require clarification' if incidents else 'no confirmed recipient notice')
                row = matches[0]
                scope = {key:row[key] for key in ('incident_id','project','correlation_id')}
                scope['delivery_id'] = row['id']
                scope['receipt_id'] = fields['receipt_id']
                binding_id = secrets.token_hex(24)
                center._connection.execute('INSERT INTO voice_recipient_bindings VALUES (?,?,?,?,?)',
                    (fields['call_id'],binding_id,proof_hash,json.dumps(scope,sort_keys=True),fields['expires_at']))
            else:
                if stored['proof_hash'] != proof_hash or stored['expires_at'] <= time.time():
                    raise ValidationError('recipient binding changed or expired')
                binding_id = stored['binding_id']
                scope = json.loads(stored['scope_json'])
            if action != 'resolve' and body.get('binding_id') != binding_id:
                raise ValidationError('foreign recipient binding')
            row = center._connection.execute('SELECT * FROM deliveries WHERE id=?', (scope['delivery_id'],)).fetchone()
            card = center.get_incident(scope['incident_id'])
            if (row is None or not self._matches(row, fields) or not card
                    or any(card.get(k) != scope[k] for k in ('project','correlation_id'))):
                raise ValidationError('recipient delivery no longer matches')
            if action == 'ack' and card['state'] not in ('acknowledged','resolved'):
                card = center.acknowledge(scope['incident_id'], 'voice-recipient:'+fields['caller'])
            # Never expose the bearer proof, unrelated owner cards or repair sessions.
            return {'ok':True, 'binding_id':binding_id, 'call_id':fields['call_id'],
                    'delivery_id':scope['delivery_id'], 'expires_at':fields['expires_at'],
                    'incident':{key:card.get(key) for key in
                                ('id','project','correlation_id','state','title','body','severity','updated_at')}}
