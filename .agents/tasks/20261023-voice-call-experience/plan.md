Граф задач: Task
Ревизия плана: 176fffb5692e
Это снимок плана, а не панель управления: фактическое состояние работы приходит из API владельцев, а не из пересказа агента в графе.
Цель: довести до проверенного выпуска надёжный русский голосовой помощник и корректную эскалацию Matrix → Telegram → телефон; использовать существующие Matrix fixtures для воспроизводимых проверок и сохранить хорошие версии.
Бизнес-результат: владелец ведёт связный разговор без перебиваний и слышит русские названия; входящий звонок включает секретаря. Ответ в Matrix/Telegram прекращает цепочку, телефон звонит после подтверждённых пропусков; версии можно повторить и откатить.
Прогресс (по листьям графа): 5/14 готово (35.7%) · в работе 2 · заблокировано 2 · осталось 9
Листьев в графе: 14 · слотов: 2 · волн: 4
Параллельность: сейчас 2 из 2 слотов, пик по плану 2
Прогноз: известная активная часть 1 ч 15 мин (от 50 мин), финиш неизвестен из-за внешнего ожидания. отсчёт от последнего события 04:05 · простой 2 ч 32 мин
Критический путь: reconcile исход запроса `owner-matrix-secretary-after-sdk-route-20261023-01` без повтора → проверить или восстановить стабильный tunnel `127.0.0.1:18086` к SDK app `192.168.128.7:8080` → после reconciliation UNKNOWN и проверки стабильного SDK tunnel провести один Secretary SDK разговор owner↔QA B → сохранить проверенные версии и архив с воспроизводимым откатом
Блокеры:
  - Почему ждём: прежний effective URL указывал на Airlock Platform, затем `/etc/agentcall-secretary-voice.json` переведён на SDK app `192.168.128.7:8080` и перезапущен только `userio-voice`; стабильного tunnel `127.0.0.1:18086` нет, новый terminal receipt не получен. Airlock не меняли.
    Кто делает: Lead runtime acceptance
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: запроса нет в Matrix call store/backend DB/voice logs, dispatcher log недоступен, обе MCP endpoints возвращают transport send error; неизвестно, был ли запрос передан. Оставить UNKNOWN и не replay.
    Кто делает: Lead receipt reconciliation
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
Листья плана (14):
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
  - L06 · 🔄 В работе · (завершить раздельную приёмку MatrixRTC guest и Secretary SDK) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Two test accounts establish the call; both-side speech/audio records are present and the per-user model is used.
  - L07 · 🔄 В работе · (проверить phone consumer Notice Place относительно контракта и подготовить безопасную приёмку) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Complete the Notice Place phone-consumer source review and confirm the reviewed runtime behavior matches the escalation contract; no live phone canary is implied.
  - L08 · ⛔ Заблокировано · (получить terminal SDK turn через исправленный Secretary SDK маршрут) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: прежний effective URL указывал на Airlock Platform, затем `/etc/agentcall-secretary-voice.json` переведён на SDK app `192.168.128.7:8080` и перезапущен только `userio-voice`; стабильного tunnel `127.0.0.1:18086` нет, новый terminal receipt не получен. Airlock не меняли.
      Приёмка: A single non-replayed owner↔QA B MatrixRTC Secretary SDK call produces a terminal SDK receipt, two-way Russian audio and correct product-name pronunciation.
  - L09 · ⛔ Заблокировано · (reconcile исход запроса `owner-matrix-secretary-after-sdk-route-20261023-01` без повтора) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: запроса нет в Matrix call store/backend DB/voice logs, dispatcher log недоступен, обе MCP endpoints возвращают transport send error; неизвестно, был ли запрос передан. Оставить UNKNOWN и не replay.
      Приёмка: Reconcile the exact request_id from an authoritative dispatcher/transport receipt; preserve UNKNOWN and never replay if the receipt cannot prove a terminal outcome.
  - L10 · ⋯ В очереди · (после reconciliation UNKNOWN и проверки стабильного SDK tunnel провести один Secretary SDK разговор owner↔QA B) · оценка 10 мин–15 мин · усилие не измерено · волна 3 · зависит: L09, L14
      Приёмка: One fresh owner↔QA B SDK canary after exact UNKNOWN reconciliation and stable target route; terminal receipt and expected Secretary behavior are observed.
  - L11 · ⋯ В очереди · (на свежем запросе подтвердить, что ответ в Matrix/Telegram останавливает цепочку без телефонного вызова) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L06, L07
      Приёмка: A fresh chain with a definitive Matrix or Telegram answer creates no later phone delivery.
  - L12 · ⋯ В очереди · (на отдельном свежем запросе подтвердить: достоверный пропуск Matrix и Telegram даёт ровно один телефонный вызов) · оценка 10 мин–15 мин · усилие не измерено · волна 3 · зависит: L11
      Приёмка: A fresh independent chain with definitive missed Matrix and Telegram legs starts exactly one phone call; no old delivery is replayed.
  - L13 · ⋯ В очереди · (сохранить проверенные версии и архив с воспроизводимым откатом) · оценка 10 мин–15 мин · усилие не измерено · волна 4 · зависит: L10, L12
      Приёмка: Record immutable source/image revisions, complete release receipts and verified rollback archives; reconcile Voice f199406, backend archives b54328f/baa2764, Notice Place runtime drift and missing BotFather image digest/rollback receipt.
  - L14 · ⋯ В очереди · (проверить или восстановить стабильный tunnel `127.0.0.1:18086` к SDK app `192.168.128.7:8080`) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L09
      Приёмка: Verify or restore a stable local tunnel from 127.0.0.1:18086 to SDK app 192.168.128.7:8080 without changing Airlock; verify the target before the next canary.