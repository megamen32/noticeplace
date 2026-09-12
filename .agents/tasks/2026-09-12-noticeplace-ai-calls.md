# NoticePlace AI incident calls

Started at 2026-09-12T17:49:31+03:00 (`date -Iseconds`).

## Запрос и минимальный путь

- Результат: NoticePlace немедленно вызывает владельца через работающий
  S21/GSM2SIP AI-тракт для разрешённой важности, рассказывает содержание
  инцидента и сохраняет обычную доставку NoticePlace.
- Кратчайший реальный canary: отправить один безопасный синтетический инцидент
  разрешённого уровня, получить один реальный телефонный звонок, услышать
  заранее подготовленное начало и увидеть call lifecycle/transcript в UserIO.
- YAGNI slice: заменить только телефонный call adapter на Asterisk/AudioSocket,
  вызвать его без задержки для выбранного уровня, применять тихие часы
  00:00-12:00 Europe/Moscow, а `emergency` разрешить обходить запрет.
- Discard now: barge-in/перебивание, новый шумодав, входящие звонки, новая
  система severity, изменение Telegram/Matrix-маршрутов и массовый реальный
  инцидентный тест.

## Владение и canary delta

- Владелец события и severity policy: NoticePlace.
- Переиспользуемый транспорт: NoticePlace escalation job -> локальный Asterisk
  -> S21 GSM2SIP -> AudioSocket AI bridge -> UserIO `phone`.
- Canary delta: разрешённый NoticePlace event создаёт один AI-звонок сразу,
  вместо старой ADB phone escalation через 600 секунд.

## Оценка

- 20-40 активных минут; неопределённость — точная текущая семантика severity и
  формат call-adapter payload. Активное время контролируется по этому циклу с
  17:49:31 MSK.

## Статус

- Найден live runtime `/opt/noticeplace`, исходник
  `/home/roomhacker/agents-projects/noticeplace`, текущий commit `7de8a5b`.
- Live policy: `critical` повторяется в Telegram; Matrix call через 3600 с,
  `emergency` через 600 с; старый Android phone escalation настроен на 600 с.
- Реализован локальный `AgentCallPhoneAdapter`: он передаёт ограниченные
  title/body в `/run/agentcall/control.sock`, просит повторить заранее
  синтезированную фразу дважды и возвращает receipt/call id.
- `critical` планирует телефонный звонок с настраиваемой задержкой (live target
  будет `0`) и подавляется с 00:00 до 12:00 Europe/Moscow; `emergency` также
  планируется сразу и обходит тихие часы. Эти четыре числовые настройки
  доступны в существующем runtime-settings editor NoticePlace.
- Focused Red-Green: отсутствовали `AgentCallPhoneAdapter`,
  `phone_call_allowed` и новые admin settings; после минимальной реализации
  семь новых/изменённых проверок проходят. Срез из 101 релевантного теста
  проходит. Полный suite: 260 тестов, один существующий несвязанный fail в
  `test_agent_herder_choices` (`✓` в ожидании против `✅` в реализации).
