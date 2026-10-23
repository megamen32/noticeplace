Граф задач: Task
Ревизия плана: 0b8fa9c3907b
Это снимок плана, а не панель управления: фактическое состояние работы приходит из API владельцев, а не из пересказа агента в графе.
Цель: довести до проверенного выпуска надёжный русский голосовой помощник и корректную эскалацию Matrix → Telegram → телефон; новые голосовые проверки выполнять только на существующих QA Matrix-ботах A/B и сохранять проверенные версии.
Бизнес-результат: связный русский разговор без перебивания и устаревшего ответа, входящий режим секретаря, остановка цепочки после ответа и корректное поведение после достоверных пропусков; версии можно восстановить.
Прогресс (по листьям графа): 6/15 готово (40.0%) · в работе 2 · заблокировано 4 · осталось 9
Листьев в графе: 15 · слотов: 2 · волн: 4
Параллельность: сейчас 2 из 2 слотов, пик по плану 2
Прогноз: известная активная часть 1 ч 15 мин (от 50 мин), финиш неизвестен из-за внешнего ожидания. отсчёт от последнего события 04:05 · простой 8 ч 23 мин
Критический путь: reconcile исход запроса `owner-matrix-secretary-after-sdk-route-20261023-01` без повтора → проверить или восстановить стабильный tunnel `127.0.0.1:18086` к SDK app `192.168.128.7:8080` → получить terminal SDK turn по Secretary маршруту на QA A/B → завершить общий release/rollback archive с точными receipts и digest
Блокеры:
  - Почему ждём: The prior effective URL pointed to Airlock Platform. Configuration now targets SDK app 192.168.128.7:8080; only userio-voice restarted; stable tunnel 127.0.0.1:18086 absent. No terminal SDK receipt/dialogue proof. Future tests only QA Matrix A/B.
    Кто делает: Lead runtime acceptance
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: Exact request_id owner-matrix-secretary-after-sdk-route-20261023-01 remains UNKNOWN: no matching row in Matrix call store, UserIO DB or backend/voice logs; dispatcher log unavailable, exact dispatch unproven. Do not replay.
    Кто делает: Lead receipt reconciliation
    Следующий шаг: Never replay request_id owner-matrix-secretary-after-sdk-route-20261023-01; a permitted fresh request is a separate canary.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: The direct Matrix receipt is answered=false and actor=null, with no dialogue. Do not repeat or count it as acceptance.
    Кто делает: Lead call receipt review
    Следующий шаг: The Notice Place source review is still in progress; contract/runtime alignment alone is not live canary proof.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: Direct owner phone request was rejected before admission because no phone gateway is active. No repeat; real phone/PSTN/GSM/SIP calls are prohibited for test work.
    Кто делает: Lead escalation acceptance
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
Листья плана (15):
  - L01 · ✅ Готово · (сверить факты по Notice Place, UserIO, AgentCall и BotFather, текущий WIP и права путей) · оценка 8 мин–12 мин · усилие 6 мин · по часам 6 мин · волна 1 · зависит: —
      Приёмка: A fresh evidence table names each current source head, dirty paths, active owner, permitted target and next shortest fix; no changes made.
  - L02 · ✅ Готово · (проверить разрешённые удалённые ветки и совпадение локальной main с origin/main) · оценка 8 мин–12 мин · усилие 4 мин · по часам 4 мин · волна 1 · зависит: —
      Приёмка: Scoped code review/tests pass, BotFather security change is published to its permitted target.
  - L03 · ✅ Готово · (сверить опубликованный MatrixRTC путь UserIO, его проверки и активные ограничения) · оценка 8 мин–12 мин · усилие 4 мин · по часам 4 мин · волна 1 · зависит: —
      Приёмка: Per-user MatrixRTC call uses the caller model without persisting secrets; audio/STT regression tests pass and source is published to the permitted target.
  - L04 · ✅ Готово · (сверить и использовать существующие Matrix test fixtures вместо выпуска новых аккаунтов) · оценка 10 мин–15 мин · усилие не измерено · по часам 26 мин · волна 1 · зависит: —
      Приёмка: An incoming call enters secretary mode and the mode transition is observable on the actual consumer path.
  - L05 · ✅ Готово · (подтвердить read-only selector probes SecretaryBrain/mode=secretary для owner и QA B) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: The original utterance completes before response; no stale response starts; Russian names/terms survive and remain speakable.
  - L06 · ✅ Готово · (создать и read back voice-only archive с подтверждённым rollback receipt) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Voice-only archive for image f199406/source 29a2c4d has verified manifest/receipt; hashes, root-only permissions and readback pass, live container unchanged. Not final release acceptance.
  - L07 · 🔄 В работе · (завершить раздельную приёмку MatrixRTC guest и Secretary SDK только на тестовых ботах A/B) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Separate guest and Secretary SDK behavior is proven on existing isolated QA Matrix A/B accounts; no owner identity or phone is used.
  - L08 · 🔄 В работе · (завершить Notice Place rollout/readback и phone-consumer review) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Notice Place b1944cd reads readiness HTTP 200 on server-100 and server-44 with verified classifier SHA; server-88 must also converge and phone-consumer review must complete.
  - L09 · ⛔ Заблокировано · (получить terminal SDK turn по Secretary маршруту на QA A/B) · оценка 10 мин–15 мин · усилие не измерено · волна 3 · зависит: L14 · блокер: The prior effective URL pointed to Airlock Platform. Configuration now targets SDK app 192.168.128.7:8080; only userio-voice restarted; stable tunnel 127.0.0.1:18086 absent. No terminal SDK receipt/dialogue proof. Future tests only QA Matrix A/B.
      Приёмка: A permitted QA A/B Matrix test reaches the SDK app, yields a terminal receipt and confirms Secretary behavior; no owner identity or phone.
  - L10 · ⛔ Заблокировано · (reconcile исход запроса `owner-matrix-secretary-after-sdk-route-20261023-01` без повтора) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: Exact request_id owner-matrix-secretary-after-sdk-route-20261023-01 remains UNKNOWN: no matching row in Matrix call store, UserIO DB or backend/voice logs; dispatcher log unavailable, exact dispatch unproven. Do not replay.
      Приёмка: Obtain authoritative terminal receipt for exact request_id; otherwise preserve UNKNOWN without replay.
  - L11 · ⛔ Заблокировано · (зафиксировать отрицательный результат отдельного direct Matrix вызова без повтора) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: The direct Matrix receipt is answered=false and actor=null, with no dialogue. Do not repeat or count it as acceptance.
      Приёмка: A direct Matrix dialogue receipt proves a real answer; current attempt was an explicit no-answer and must not be repeated.
  - L12 · ⛔ Заблокировано · (подтвердить phone escalation после достоверных Matrix/Telegram пропусков) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L08 · блокер: Direct owner phone request was rejected before admission because no phone gateway is active. No repeat; real phone/PSTN/GSM/SIP calls are prohibited for test work.
      Приёмка: Verify phone-escalation policy through a non-dialing test sink only; actual phone delivery remains unaccepted.
  - L13 · ⋯ В очереди · (на разрешённом тестовом Matrix A/B сценарии подтвердить, что ответ останавливает цепочку) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L07, L08
      Приёмка: A permitted isolated QA Matrix A/B scenario with a confirmed answer stops escalation; no phone leg is dialed.
  - L14 · ⋯ В очереди · (проверить или восстановить стабильный tunnel `127.0.0.1:18086` к SDK app `192.168.128.7:8080`) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L10
      Приёмка: Verify or restore stable local tunnel 127.0.0.1:18086 to SDK app 192.168.128.7:8080; confirm target before any separately authorized QA A/B test.
  - L15 · ⋯ В очереди · (завершить общий release/rollback archive с точными receipts и digest) · оценка 10 мин–15 мин · усилие не измерено · волна 4 · зависит: L06, L09, L10, L11, L12, L13, L14
      Приёмка: Record exact source/image revisions, immutable digests, complete receipts and rollback evidence across every changed service; voice-only archive alone is insufficient.