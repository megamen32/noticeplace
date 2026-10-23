Граф задач: Task
Ревизия плана: d9ffa59ea5c4
Это снимок плана, а не панель управления: фактическое состояние работы приходит из API владельцев, а не из пересказа агента в графе.
Цель: довести до проверенного выпуска надёжный русский голосовой помощник и корректную эскалацию Matrix → Telegram → телефон; использовать существующие Matrix fixtures для воспроизводимых проверок и сохранить хорошие версии.
Бизнес-результат: владелец ведёт связный разговор без перебиваний и слышит русские названия; входящий звонок включает секретаря. Ответ в Matrix/Telegram прекращает цепочку, телефон звонит после подтверждённых пропусков; версии можно повторить и откатить.
Прогресс (по листьям графа): 5/11 готово (45.5%) · в работе 1 · заблокировано 1 · осталось 6
Листьев в графе: 11 · слотов: 2 · волн: 4
Параллельность: сейчас 1 из 2 слотов, пик по плану 2
Прогноз: известная активная часть 1 ч (от 40 мин), финиш неизвестен из-за внешнего ожидания. отсчёт от последнего события 04:05 · простой 1 ч 06 мин
Критический путь: завершить раздельную приемку MatrixRTC guest и Secretary SDK → на свежем запросе подтвердить, что ответ в Matrix/Telegram останавливает цепочку без телефонного вызова → на отдельном свежем запросе подтвердить: достоверный пропуск Matrix и Telegram даёт ровно один телефонный вызов → сохранить проверенные версии и архив с воспроизводимым откатом
Блокеры:
  - Почему ждём: owner credential, user-level send capability, owner `/whoami`, приватная owner↔B комната и MatrixRTC audio подтверждены; установленный AgentCall wrapper SHA `c44c620b5758e7e45a7226552c577369bd1379469e6e642fac5f1a968c6a670c` завершил ход ошибкой `NativeTurnUnsettled`. Нужна установка ранее проверенного AgentCall SDK fix владельцем voice runtime, без дублирования его правок.
    Кто делает: Worker bugfix
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
Листья плана (11):
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
  - L06 · 🔄 В работе · (завершить раздельную приемку MatrixRTC guest и Secretary SDK) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Two test accounts establish the call; both-side speech/audio records are present and the per-user model is used.
  - L07 · ⛔ Заблокировано · (провести полностью завершённый Secretary SDK диалог через существующую owner Matrix identity) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: owner credential, user-level send capability, owner `/whoami`, приватная owner↔B комната и MatrixRTC audio подтверждены; установленный AgentCall wrapper SHA `c44c620b5758e7e45a7226552c577369bd1379469e6e642fac5f1a968c6a670c` завершил ход ошибкой `NativeTurnUnsettled`. Нужна установка ранее проверенного AgentCall SDK fix владельцем voice runtime, без дублирования его правок.
      Приёмка: The specified owner recording conversation passes the unchanged utterance-boundary canary.
  - L08 · ⋯ В очереди · (после установки AgentCall SDK fix провести один Secretary SDK разговор owner↔QA B и проверить границы реплик) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L07
      Приёмка: A confirmed Matrix or Telegram answer creates no later phone delivery.
  - L09 · ⋯ В очереди · (на свежем запросе подтвердить, что ответ в Matrix/Telegram останавливает цепочку без телефонного вызова) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L06
      Приёмка: A fresh chain with definitive missed Matrix and Telegram legs starts exactly one phone call; no old delivery is replayed.
  - L10 · ⋯ В очереди · (на отдельном свежем запросе подтвердить: достоверный пропуск Matrix и Telegram даёт ровно один телефонный вызов) · оценка 10 мин–15 мин · усилие не измерено · волна 3 · зависит: L09
      Приёмка: Each changed repository has a pinned good release, archive reference and rollback path.
  - L11 · ⋯ В очереди · (сохранить проверенные версии и архив с воспроизводимым откатом) · оценка 10 мин–15 мин · усилие не измерено · волна 4 · зависит: L10