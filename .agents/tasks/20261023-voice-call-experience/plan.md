Граф задач: Task
Ревизия плана: bda6daf086ad
Это снимок плана, а не панель управления: фактическое состояние работы приходит из API владельцев, а не из пересказа агента в графе.
Цель: довести до проверенного выпуска надёжный русский голосовой помощник и корректную эскалацию Matrix → Telegram → телефон; автоматические QA-проверки выполнять на существующих ботах Matrix A/B, а отдельную owner/phone авторизацию использовать только в её точных пределах.
Бизнес-результат: связный русский разговор без перебивания и устаревшего ответа, входящий режим секретаря, остановка цепочки после ответа и корректное поведение после достоверных пропусков; версии можно восстановить.
Прогресс (по листьям графа): 6/15 готово (40.0%) · в работе 3 · заблокировано 4 · осталось 9
Листьев в графе: 15 · слотов: 2 · волн: 3
Параллельность: сейчас 3 из 2 слотов, пик по плану 2
Прогноз: известная активная часть 1 ч 15 мин (от 50 мин), финиш неизвестен из-за внешнего ожидания. отсчёт от последнего события 04:05 · простой 8 ч 29 мин
Критический путь: довести stable SDK transport к приложению → получить Secretary SDK dialogue acceptance после готовности транспорта → завершить общий release/rollback archive с точными receipts и digest
Блокеры:
  - Почему ждём: No terminal SDK receipt or dialogue proof yet. Root owns applying the external transport result and subsequent acceptance.
    Кто делает: root
    Следующий шаг: Does not depend on replaying the old UNKNOWN request; any fresh action must be deliberate and separately authorized.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: request остаётся UNKNOWN; старый GET после DNS repair вернул not found, dispatcher call receipt не подтверждён. Это только recorded operation и не prerequisite для build stable route
    Кто делает: Lead call receipt review
    Следующий шаг: The Notice Place source review is still in progress; contract/runtime alignment alone is not live canary proof.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: `answered=false`, `actor=null`, диалога нет; не повторять без материального изменения готовности/необходимости
    Кто делает: Lead escalation acceptance
    Следующий шаг: Estimates are active work; external phone-recipient wait is tracked at graph level.
    Нужен ли человек: Пока неизвестно. Действие человека не уточнено.
  - Почему ждём: авторизованный владельцем direct owner/phone request был один раз отклонён до набора из-за отсутствия активного gateway; без серии и слепых повторов
    Кто делает: Lead test
    Следующий шаг: Следующий шаг не указан.
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
  - L07 · 🔄 В работе · (завершить раздельную приёмку MatrixRTC guest и Secretary SDK на тестовых ботах A/B) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Separate guest and Secretary SDK behavior is proven on existing isolated QA Matrix A/B accounts; no owner identity or phone is used.
  - L08 · 🔄 В работе · (завершить Notice Place rollout/readback и phone-consumer review) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Notice Place b1944cd reads readiness HTTP 200 on server-100 and server-44 with verified classifier SHA; server-88 must also converge and phone-consumer review must complete.
  - L09 · 🔄 В работе · (довести stable SDK transport к приложению) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: —
      Приёмка: Deliver a stable transport path to the SDK app and return the effective URL/config; do not perform or claim dialogue acceptance. This work is independent of the older UNKNOWN request.
  - L10 · ⛔ Заблокировано · (получить Secretary SDK dialogue acceptance после готовности транспорта) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L09 · блокер: No terminal SDK receipt or dialogue proof yet. Root owns applying the external transport result and subsequent acceptance.
      Приёмка: After transport readiness, root applies returned URL/config and obtains an authoritative terminal SDK receipt plus dialogue evidence; no claim of acceptance before that evidence.
  - L11 · ⛔ Заблокировано · (сохранить результат запроса `owner-matrix-secretary-after-sdk-route-20261023-01` без повтора) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: request остаётся UNKNOWN; старый GET после DNS repair вернул not found, dispatcher call receipt не подтверждён. Это только recorded operation и не prerequisite для build stable route
      Приёмка: A direct Matrix dialogue receipt proves a real answer; current attempt was an explicit no-answer and must not be repeated.
  - L12 · ⛔ Заблокировано · (зафиксировать отдельный direct Matrix результат без повтора) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: `answered=false`, `actor=null`, диалога нет; не повторять без материального изменения готовности/необходимости
      Приёмка: Verify phone-escalation policy through a non-dialing test sink only; actual phone delivery remains unaccepted.
  - L13 · ⛔ Заблокировано · (подтвердить phone escalation после достоверных Matrix/Telegram пропусков) · оценка 10 мин–15 мин · усилие не измерено · волна 1 · зависит: — · блокер: авторизованный владельцем direct owner/phone request был один раз отклонён до набора из-за отсутствия активного gateway; без серии и слепых повторов
      Приёмка: A permitted isolated QA Matrix A/B scenario with a confirmed answer stops escalation; no phone leg is dialed.
  - L14 · ⋯ В очереди · (на разрешённом тестовом Matrix A/B сценарии подтвердить, что ответ останавливает цепочку) · оценка 10 мин–15 мин · усилие не измерено · волна 2 · зависит: L07, L08
      Приёмка: On isolated existing QA A/B Matrix accounts, prove a confirmed answer stops the escalation chain; keep this distinct from owner dialogue and direct phone authorization.
  - L15 · ⋯ В очереди · (завершить общий release/rollback archive с точными receipts и digest) · оценка 10 мин–15 мин · усилие не измерено · волна 3 · зависит: L07, L08, L09, L10, L11, L12, L13, L14
      Приёмка: Record exact source/image revisions, immutable digests, complete receipts and rollback evidence across every changed service; voice-only archive alone is insufficient.