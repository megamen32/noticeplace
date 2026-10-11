"""One Russian incident projection and one keyboard for every delivery phase."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any, Mapping

from .health_workflow import sanitize_bounded_text
from .human_request_original import truncate_utf16

CARD_SETTINGS = {
    'telegram_card_edit_interval_seconds': ('15', 'Обновление карточки: минимальная пауза (сек.)'),
    'telegram_group_window_seconds': ('300', 'Объединять похожие уведомления за интервал (сек.)'),
    'telegram_group_similar_enabled': ('1', 'Объединять похожие уведомления'),
    'telegram_native_progress_enabled': ('1', 'Показывать состояние ИИ и последний ответ'),
    'telegram_all_notifications_enabled': ('1', 'Показывать также обычные уведомления'),
    'telegram_critical_sound_enabled': ('1', 'Звук нового критичного уведомления'),
    'telegram_routine_sound_enabled': ('0', 'Звук обычного уведомления'),
}

EXECUTOR_LABELS = {'opencode': 'OpenCode', 'codex': 'Codex', 'hermes': 'Hermes', 'zcode': 'ZCode',
                   'claude': 'Claude Code', 'qoder': 'Qoder', 'fast-agent': 'FastAgent', 'chatgpt': 'ChatGPT'}


def display_session_url(identity: Any) -> str:
    if not isinstance(identity, Mapping) or identity.get('harness') not in EXECUTOR_LABELS:
        return ''
    native_id = identity.get('session_id')
    if not isinstance(native_id, str) or not native_id:
        return ''
    expected = 'https://agent.bezrabotnyi.com/#/session/' + urllib.parse.quote(f"{identity['harness']}:{native_id}", safe="!'()*")
    return expected if identity.get('session_url') == expected else ''


def setting(center: Any, key: str) -> float:
    default = CARD_SETTINGS[key][0]
    try:
        value = float(center.get_runtime_setting(key, default)) if center else float(default)
        if not 0 <= value < 86401:
            raise ValueError()
    except (TypeError, ValueError):
        value = float(default)
    return max(15, value) if key == 'telegram_card_edit_interval_seconds' else value


def session_url(session: Any) -> str:
    url = str(session.get('session_url') or '') if isinstance(session, Mapping) else ''
    parsed = urllib.parse.urlsplit(url)
    return url if parsed.scheme == 'https' and parsed.hostname == 'agent.bezrabotnyi.com' else ''


def controls(codec: Any, incident: Mapping[str, Any], session: Any = None, *, execution_identity: Any = None) -> list[list[dict[str, str]]]:
    if codec is None:
        return []
    incident_id = str(incident['id'])
    rows = []
    if incident.get('severity') in {'critical', 'emergency'} and incident.get('state') != 'resolved':
        rows.append([{'text': '✅ Прочитал' if incident.get('state') == 'acknowledged' else 'Прочитал',
                      'callback_data': codec.encode('ack', incident_id)}])
    url = session_url(session)
    ai = ({'text': '✅ ИИ запущен', 'url': url} if url else
          {'text': 'Исправить через ИИ', 'callback_data': codec.encode('ai', incident_id)})
    muted = bool(incident.get('notifications_muted'))
    rows.extend([[ai], [{'text': 'Включить такие уведомления' if muted else 'Отключить такие уведомления',
                         'callback_data': codec.encode('unmute' if muted else 'mute', incident_id)}]])
    display_url = display_session_url(execution_identity) if not session else ''
    if display_url:
        rows.append([{'text': 'Сессия · ' + EXECUTOR_LABELS[execution_identity['harness']], 'url': display_url}])
    return rows


def question_rows(codec: Any, request: Mapping[str, Any]) -> list[list[dict[str, str]]]:
    if codec is None or request.get('mode') != 'choice' or request.get('state') not in {'pending', 'resolved'}:
        return []
    return [[{'text': (('✅ ' if request.get('state') == 'resolved' and choice['value'] == request.get('response_value') else '')
                       + str(choice['label']))[:80],
              'callback_data': codec.encode_human_choice(str(request['request_id']), str(index))}]
            for index, choice in enumerate(request.get('choices') or [])]


def render(payload: Mapping[str, Any], codec: Any = None) -> tuple[str, dict[str, Any]]:
    incident = payload['incident']
    outcome = payload.get('health_outcome') or {}
    session = payload.get('ai_session') or payload.get('health_session') or {}
    # A recorded native launch outranks producer display metadata. Display-only
    # identity never becomes ai_session or a launch/current-running assertion.
    identity = session or payload.get('execution_identity') or {}
    executor = EXECUTOR_LABELS.get(identity.get('harness')) if isinstance(identity, Mapping) else None
    state = incident.get('state')
    status = str(outcome.get('status') or '')
    members = payload.get('group_members') or []
    remaining = sum(member.get('state') != 'resolved' for member in members)
    if state == 'resolved':
        heading = 'Изменилась срочность проблемы' if status == 'phase_changed' else 'Состояние восстановилось'
        explanation = ('Сигнал ещё активен. Прежний этап закрыт; восстановление не подтверждено.' if status == 'phase_changed'
                       else 'Мониторинг подтвердил восстановление. Инцидент закрыт; действий от вас не требуется.')
    elif outcome.get('observed_state') == 'degraded':
        heading = 'Восстановление не подтверждено'
        explanation = 'ИИ завершил этап, но исходный сервис всё ещё в состоянии деградации. Инцидент остаётся открытым; требуется продолжить проверку и исправление.'
    elif status in {'failed', 'interrupted', 'source_blocked', 'human_stopped'}:
        heading = 'Работа остановилась' if status == 'failed' else 'Требуется проверка выполнения'
        explanation = 'Восстановление не подтверждено. Проверьте исходную сессию ИИ; инцидент остаётся открытым.'
    elif session_url(session):
        heading = {'health-diagnosis': 'ИИ запущен для диагностики', 'health-orchestrator': 'ИИ запущен для разбора причины',
                   'health-remediation': 'ИИ запущен для исправления'}.get(session.get('stage'), 'ИИ запущен для работы над инцидентом')
        explanation = 'Восстановление ещё не подтверждено. Решение потребуется только при вопросе помощника.'
    else:
        heading = {'emergency': 'Аварийная ситуация', 'critical': 'Требуется внимание',
                   'important': 'Важное уведомление'}.get(incident.get('severity'), 'Уведомление')
        explanation = 'Инцидент принят. Проверка и исправление выполняются по правилам проекта.'
    if len(members) > 1 and 0 < remaining < len(members):
        heading = 'Часть сервисов восстановилась'
        explanation = (f'В группе остаётся открытых инцидентов: {remaining}. '
                       'Проверка и исправление продолжаются; восстановление всей группы ещё не подтверждено.')
    elif len(members) > 1 and remaining == 0:
        heading = 'Все инциденты группы закрыты'
        explanation = 'Состояние каждого сервиса и история проверки доступны по отдельным ссылкам ниже.'
    title = sanitize_bounded_text(incident['title'], 500)
    if not re.search('[А-Яа-яЁё]', title):
        title = {'load': 'Повышена нагрузка на сервер', 'cpu': 'Повышена нагрузка на процессор',
                 'ram': 'На сервере не хватает памяти', 'disk': 'На сервере заканчивается место',
                 'https': 'Сайт не отвечает на проверку'}.get((payload.get('health_context') or {}).get('signal_type'),
                                                           'Мониторинг сообщил о проблеме сервиса')
    text = heading + ('\n\nИсполнитель: ' + executor if executor else '\n\nИсполнитель не указан.')
    if executor:
        model = sanitize_bounded_text(identity.get('model'), 256)
        cwd = sanitize_bounded_text(identity.get('cwd'), 220)
        if model:
            text += '\nМодель: ' + model
        if cwd:
            text += '\nРабочий каталог: ' + cwd
    text += '\n\n' + title
    body = sanitize_bounded_text(incident.get('body'), 800)
    if body and re.search('[А-Яа-яЁё]', body):
        text += '\n\n' + body
    text += '\n\n' + explanation
    text += '\n\nПроект уведомления: ' + sanitize_bounded_text(incident['project'], 96)
    if state == 'acknowledged':
        text += '\nВы прочитали уведомление. Проверка и исправление продолжаются.'
    progress = payload.get('native_progress')
    if isinstance(progress, Mapping) and state != 'resolved':
        if progress.get('needs_permission'):
            text += '\n\nСессия ожидает разрешения в Agent Herder.'
        elif progress.get('status') in {'running', 'busy', 'working'}:
            text += '\n\nСессия работает.'
        elif progress.get('status') in {'idle', 'completed'}:
            text += '\n\nСессия ожидает следующего шага.'
        answer = sanitize_bounded_text(progress.get('assistant_text'), 500)
        if answer:
            text += '\n\nПоследний доступный ответ'
            if progress.get('assistant_timestamp'):
                text += ' (' + sanitize_bounded_text(progress['assistant_timestamp'], 40) + ')'
            text += ':\n' + answer
    url = session_url(session) if session else display_session_url(identity)
    if url:
        text += '\n\nСсылка на сессию: ' + url
    rows = []
    question = payload.get('human_question')
    questions = {str(q['request_id']): q for q in payload.get('human_questions') or [] if isinstance(q, Mapping)}
    if isinstance(question, Mapping):
        questions[str(question['request_id'])] = question
    for question in list(questions.values())[:8]:
        question_state = next((m.get('state') for m in members if m.get('id') == question.get('incident_id')), state)
        text += '\n\nВопрос помощника: ' + sanitize_bounded_text(question.get('message'), 400)
        if question.get('state') == 'resolved':
            answer = next((c['label'] for c in question.get('choices') or [] if c['value'] == question.get('response_value')),
                          sanitize_bounded_text(question.get('response_value'), 160))
            text += '\n✅ Ответ: ' + answer
        elif question_state == 'resolved' or question.get('state') in {'cancelled','expired'}:
            text += '\nВопрос закрыт; новый ответ не требуется.'
        elif question.get('mode') == 'question':
            text += '\nОтветьте на эту карточку сообщением или по телефону.'
        if question_state != 'resolved' or question.get('state') == 'resolved':
            rows.extend(question_rows(codec, question))
    if not questions and isinstance(payload.get('choices'), list) and (state != 'resolved' or payload.get('choice_selection')):
        selection = payload.get('choice_selection') or {}
        for index, choice in enumerate(payload['choices'][:4]):
            if codec:
                selected = str(selection.get('choice_id') or '') == str(choice.get('choice_id') or '')
                rows.append([{'text': (('✅ ' if selected else '') + str(choice.get('label') or ''))[:80],
                              'callback_data': codec.encode('choice', str(incident['id']), choice_id=str(index))}])
    if len(members) > 1:
        text += f'\n\nПохожих инцидентов: {len(members)}. Каждый проверяется отдельно.'
        for member in members[:8]:
            label = sanitize_bounded_text(member.get('host_id') or member.get('title'), 80)
            member_status = ' — восстановлен' if member.get('state') == 'resolved' else (' — ✅ прочитано; проверяется' if member.get('state') == 'acknowledged' else ' — проверяется')
            text += '\n• ' + label + member_status
            member_url = session_url(member.get('session'))
            text += '\n' + (member_url or 'https://notify.bezrabotnyi.com/admin/?history=' + urllib.parse.quote(str(member['id'])))
    control_rows = controls(codec, incident, session, execution_identity=identity)
    if codec is not None and len(members) > 1:
        attention = next((m for m in members if m.get('state') in {'open','snoozed'} and m.get('severity') in {'critical','emergency'}), None)
        if attention:
            # One explicit member owns this ACK. It never acknowledges/resolves
            # siblings; the next render selects the next unread critical member.
            control_rows = [row for row in control_rows if not any(b.get('callback_data','').startswith('n:ack:') for b in row)]
            host = sanitize_bounded_text(attention.get('host_id'), 50)
            control_rows.insert(0,[{'text': 'Прочитал' + (' · ' + host if host else ''),
                                   'callback_data': codec.encode('ack',str(attention['id']))}])
    rows.extend(control_rows)
    return truncate_utf16(text, 4000), {'inline_keyboard': rows}


def projection_key(text: str, keyboard: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps([text, keyboard], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
