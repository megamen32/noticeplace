Граф задач: Notice: доверенный ответ продолжает ту же сессию ExManager
Ревизия плана: eea03ab3ed0b
Это снимок плана, а не панель управления: фактическое состояние работы приходит из API владельцев, а не из пересказа агента в графе.
Цель: Замкнуть L06-зависимость между ответом в Board и существующим Notice task-decision completion без подмены автора или дублирующего запуска.
Бизнес-результат: До: Notice хранит task decision, но ExManager Board не может безопасно принять ответ и продолжить ту же typed OpenCode задачу. После: Board CAS answer проходит source-bound проверку автора/актуального состояния и связывается ровно с одним durable continuation; UNKNOWN остаётся query-first.
Декомпозиция: 1 лист · 1 параллельно · 1 волна · слотов: 1
Прогресс (по листьям графа): 0/1 готово (0.0%) · в работе 1 · заблокировано 0 · ждёт листа 0 · ждёт исполнителя 0 · осталось 1
Смета плана: $0.0032 (provisional) за 1 из 1 листьев
frontier $0.0032 (provisional) — 100.0% всех долларов плана
остатку к запуску $0.0032 (provisional)
бюджет не задан
листья объявили $0.0032
снимок 653e40f07acd от 2026-10-28T17:39:08Z · возраст 2 мин
Листьев в графе: 1 · слотов: 1 · волн: 1
Параллельность: сейчас 1 из 1 слотов, пик по плану 1
Исполнители: занято 1 из 1 слотов · ждёт листа: 0 · ждёт исполнителя: 0 · готово к запуску: 0
Прогноз: осталось 15 мин (от 15 мин), финиш 2026-10-28T20:56:00+03:00. отсчёт от последнего события 2026-10-28T20:41+03:00 · простой 0 мин
Критический путь: Связать Board CAS с одним Notice continuation
Листья плана (1):
  - L01 · 🔄 В работе · (Связать Board CAS с одним Notice continuation) · оценка 15 мин · усилие не измерено · волна 1 · зависит: — · стоимость $0.0032 · модель 302ai/gpt-6-luna · класс frontier · токенов 18 000 · цена provisional · лист объявил $0.0032 · расхождение 0.0%
      Описание задачи: В существующем task-decision verifier/CAS/completion join принять только проверенный source-bound Board answer, сохранить актуальные stop/hold и UNKNOWN no-replay fences, связать с одним typed OpenCode continuation.
      Приёмка: Фокусные тесты показывают отказ для forged actor/stale CAS/STOP, одну durable continuation при duplicate-safe answer и query-first при UNKNOWN; согласованный ExCode request/receipt проходит изолированную QA без production доставки.
      Затрагиваемые файлы: `notification_center/task_decision_service.py`, `notification_center/task_decisions.py`, `tests/test_task_decisions.py`, `tests/test_task_decision_service.py`