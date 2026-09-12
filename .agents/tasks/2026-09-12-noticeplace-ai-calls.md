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
- Deployed release `20260912T151829Z-973cd06394cd-2827416`; both NoticePlace
  services are active and the effective runtime sees the AgentCall socket,
  zero-second critical/emergency delays, and Moscow quiet hours 00:00-12:00.
- Live synthetic critical incident `inc_acd4e2d5bccb4d0ab982dad5effb589c`
  reached Telegram, scheduled delivery `dlv_71072e79c6674c09aa64e9204ea2709b`,
  pre-synthesized before dialing, and produced answered AgentCall
  `9900c6a8-1930-4146-9534-2878c4539579`. CDR billsec was 107; UserIO captured
  the prepared incident greeting and two-way transcript.
- Canary exposed a target-identity blocker: the recipient said the configured
  `+79068443132` is wrong and dictated a different number. The spoken change is
  not treated as authenticated operator configuration. The exact call was hung
  up and `automatic_calls_enabled` was returned to `false`; Telegram/Matrix
  delivery remains active. Live automatic calling must stay paused until the
  owner confirms the target in the authenticated task chat.
- Status: implementation deployed and proven; live automatic calls safely
  paused pending target confirmation. Active time: about 34 wall-clock minutes
  from 17:49:31 MSK; continuously measured active time was not separately
  instrumented.
