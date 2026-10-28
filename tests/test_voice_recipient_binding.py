"""A voice ACK may consume only a confirmed notice to its authenticated caller."""
import base64
import hashlib
import hmac
import json
import io
import time

import pytest

from notification_center.core import NotificationCenter, ValidationError


KEY = 'existing-userio-notice-service-key'
EXPIRES = int(time.time()) + 300


def proof(**changes):
    fields = dict(v=1, call_id='d89d60d5-a408-47a8-a8de-c6eadc91ea44',
                  receipt_id='incoming-receipt', caller='@recipient:test',
                  account_ref='matrix:assistant', room_id='!voice:test',
                  expires_at=EXPIRES)
    fields.update(changes)
    raw = json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + hmac.new(KEY.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def center(tmp_path):
    return NotificationCenter(tmp_path/'notify.db', {}, default_quiet_hours=[])


def delivered(center, *, recipient='@recipient:test', status='sent', suffix='one', account='matrix:assistant'):
    consumer = center.create_consumer(project='test', name='recipient route', policy=[{
        'id':'message', 'platform':'matrix', 'action':'message',
        'target':{'room_id':'!notice:test', 'account_ref':account, 'recipient':recipient},
        'retry_interval_seconds':30, 'max_repeats':2}])
    event = center.create_event(consumer['intake_token'], suffix, {
        'schema':'notify.event.v1', 'project':'test', 'recipient':'me', 'kind':'incident',
        'severity':'notice', 'title':'Exact notice', 'body':'Body', 'dedup_key':suffix,
        'correlation_id':suffix})
    row = center.claim_due_deliveries(limit=1)[0]
    center.complete_delivery(row['id'], status, result={
        'event_id':'$'+suffix, 'account_ref':account, 'room_id':'!notice:test'})
    return event['incident_id'], row['id']


def test_normal_recipient_received_ack_cancels_only_bound_repeat(center):
    from notification_center.voice_recipient import VoiceRecipientContext
    incident_id, delivery_id = delivered(center)
    api = VoiceRecipientContext(center, KEY)
    binding = api.apply({'proof':proof(), 'action':'resolve'})
    assert binding['incident']['id'] == incident_id
    assert binding['delivery_id'] == delivery_id
    result = api.apply({'proof':proof(), 'action':'ack', 'binding_id':binding['binding_id'],
                        'utterance':'Получил тестовое уведомление'})
    assert result['incident']['state'] == 'acknowledged'
    queued = center._connection.execute("SELECT COUNT(*) FROM deliveries WHERE incident_id=? AND status IN ('queued','claimed','sending')", (incident_id,)).fetchone()[0]
    assert queued == 0
    assert api.apply({'proof':proof(), 'action':'ack', 'binding_id':binding['binding_id'],
                      'utterance':'Получил уведомление'})['incident']['state'] == 'acknowledged'


@pytest.mark.parametrize('changes', [{'caller':'@foreign:test'}, {'account_ref':'matrix:foreign'},
                                    {'expires_at':1}])
def test_foreign_or_expired_cannot_bind(center, changes):
    from notification_center.voice_recipient import VoiceRecipientContext
    incident_id, _ = delivered(center)
    with pytest.raises(ValidationError):
        VoiceRecipientContext(center, KEY).apply({'proof':proof(**changes), 'action':'resolve'})
    assert center.get_incident(incident_id)['state'] == 'open'


@pytest.mark.parametrize('status', ['failed', 'uncertain'])
def test_unconfirmed_notification_cannot_bind(center, status):
    from notification_center.voice_recipient import VoiceRecipientContext
    delivered(center, status=status)
    with pytest.raises(ValidationError):
        VoiceRecipientContext(center, KEY).apply({'proof':proof(), 'action':'resolve'})


def test_ambiguous_and_unsigned_cannot_bind(center):
    from notification_center.voice_recipient import VoiceRecipientContext
    delivered(center)
    delivered(center, suffix='two')
    api = VoiceRecipientContext(center, KEY)
    with pytest.raises(ValidationError, match='multiple'):
        api.apply({'proof':proof(), 'action':'resolve'})
    with pytest.raises(ValidationError):
        api.apply({'proof':proof()[:-1]+'x', 'action':'resolve'})


@pytest.mark.parametrize('text', ['Не получил уведомление', 'Если получил уведомление',
                                  'Он сказал «получил уведомление»', 'Получил чужое уведомление'])
def test_negative_intent_does_not_ack(center, text):
    from notification_center.voice_recipient import VoiceRecipientContext
    incident_id, _ = delivered(center)
    api = VoiceRecipientContext(center, KEY)
    binding = api.apply({'proof':proof(), 'action':'resolve'})
    with pytest.raises(ValidationError):
        api.apply({'proof':proof(), 'binding_id':binding['binding_id'], 'action':'ack', 'utterance':text})
    assert center.get_incident(incident_id)['state'] == 'open'


def test_binding_does_not_follow_delivery_changes_or_foreign_binding_id(center):
    from notification_center.voice_recipient import VoiceRecipientContext
    incident_id, delivery_id = delivered(center)
    api = VoiceRecipientContext(center, KEY)
    binding = api.apply({'proof':proof(), 'action':'resolve'})
    with pytest.raises(ValidationError):
        api.apply({'proof':proof(),'action':'ack','binding_id':'foreign','utterance':'Получил уведомление'})
    with center._connection:
        center._connection.execute("UPDATE deliveries SET target_json=json_set(target_json,'$.recipient','@foreign:test') WHERE id=?", (delivery_id,))
    with pytest.raises(ValidationError):
        api.apply({'proof':proof(),'action':'ack','binding_id':binding['binding_id'],'utterance':'Получил уведомление'})
    assert center.get_incident(incident_id)['state'] == 'open'


def test_http_recipient_proof_uses_its_own_auth_before_project_bearer(center):
    from notification_center.http_api import build_handler
    handler=object.__new__(build_handler(center,'health-token','mcp-token'))
    handler.path='/v1/voice/recipient-context'
    handler.headers={'Content-Length':'2'}
    handler.rfile=io.BytesIO(b'{}')
    replies=[]
    handler._reply=lambda status,payload:replies.append((int(status),payload))
    handler.do_POST()
    assert replies[0][0]==400  # Proof is verified, rather than requiring a project bearer.
    assert 'voice receipt' in replies[0][1]['error']
