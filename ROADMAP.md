# NoticePlace roadmap

## Proposed

- [ ] Executor progress недоступен, когда планы переattach'ились после выбора
  (и `plan_id=repair` из промпта исполнителя отклоняется сервером). Здоровье
  инцидента: `POST /v1/incidents/{id}/health/progress` принимает только
  одновременно (а) `plan_id` из ПОСЛЕДНЕГО набора `health.plans_attached` и
  (б) ВЫБРАННЫЙ план. Если набор планов приходит повторно (повторная диагностика
  после первой), эти два условия расходятся навсегда: с выбранным планом →
  `unknown health plan`, из нового набора → `health progress requires the
  selected plan`. Ни один `plan_id` не принимается, исполнитель не может
  оставить след ремонта в карточке (остаётся только `work-finished`).
  Смежный факт: `notification_center/agent_job_helper.py` предписывает
  исполнителю `plan_id=repair`, а сервер принимает `repair` только когда планы
  НЕ присоединены (`repair_first` требует пустого набора кандидатов) — на
  любом инциденте с планами предписанный `plan_id` отклоняется как
  `unknown health plan`. Воспроизведено на video-watching
  `inc_5914024873024b4790395e2903a5db80` (`proxy5.cabinet.auth`); progress по
  соседнему `inc_84d01e2d34b5428cb5c9b1e90b528e65` прошёл, то есть дефект
  проявляется не всегда. Направление правки (за владельцем Notice Place):
  не терять выбранный план при новом наборе либо принимать progress по плану
  инцидента, а не только по последнему набору; и выровнять предписание
  `plan_id=repair` с проверкой сервера.

- [x] Event history and parent-child incident audit on the admin site: show
  which producer/client reached which event type, source/proxy IP metadata,
  parent and child incidents, every notification attempt, and the resulting
  outcome.
