# R40 — инциденты через OpenCode / MiniMax по подписке

Владелец: рабочая сессия 01a11f86-283e-7831-8ee1-d97ab3389eba на server-100.

Цель: новые diagnosis/repair используют OpenCode, minimax-coding-plan/MiniMax-M3.1-Flash-Preview; явный выбор в UI и сохранение. Без fallback, копирования ключа и повторной обработки старых инцидентов.

Проверено: Admin main синхронизирован с origin; чужой WIP сохранён, ledger прочитан и не редактируется. Noticeplace main исходно чист. Herder содержит чужой delivery/runtime WIP. API моделей подтверждает точную подписочную модель, auth type=api credential_present=true без чтения значения в вывод.

Ошибка до исправления: live launch policy и diagnosis profile выбирают Codex; планировщик содержит строку omniroute/subagent; ремонт допускает ZCode/Codex. No-LLM lifecycle — отдельный модуль и не входит в изменения.

Координация: указанному publisher доставлен один steer о границах health-remediation.ts, launch-policy-settings.tsx и малом preflight в server.ts. API показывает этот ID в AutoFind; требуется сверка настоящего Herder publisher. Build/deploy Herder не подменяется.

Бюджет: targeted Python ≤512 MiB/1 CPU/2 min; Vitest ≤1 GiB/1 worker/2 min, UI/canary ≤2 GiB/2 CPU/5 min. На 07:21 UTC available около 21 GiB, PSI full avg10 0.43, OOM не доказан; давление само по себе работу не запрещает.

Следующий шаг: красные точные регрессии, исправление helper/profile/UI, scoped publish, deployment владельцем Herder и один безопасный deduplicated synthetic incident до завершённой диагностики.

## Публикация и проверки

Herder origin/main: e81eb8e — отдельный persisted incidentExecution/UI; 34a4264 — regression unavailable native model; d80e710 — только 9 строк health preflight в server.ts. Чужие staged/unstaged пути не опубликованы. 19 targeted tests и tsc --noEmit прошли. Build/deploy передан действующему publisher 01a11b54; 01a11b43 получил UI handoff ранее, единый выпуск согласуется у publisher.

Noticeplace: 98 targeted tests прошли (health workflow/helper/GPTAdmin/R40). До исправления live policy preferredHarness=codex, protected diagnosis profile codex/gpt-5.6-sol; in-memory исходная нормализация подтверждает допустимый Codex. После изменения точный подписочный route, отсутствие скрытого fallback и сохранение global routing проверены. Evidence: .tmp/r40/*.txt, before-route.json. Исторические incidents не переобрабатывались; исходные продуктовые сессии не менялись.

Следующий шаг: scoped publish Noticeplace, штатный upgrade; дождаться выпуска Herder, сохранить incidentExecution через действующий API и проверить UI + один synthetic incident до terminal diagnosis. До этого R40 не завершён.

## Исправление push → upgrade

Первый SSH push отказал; вызов upgrade ошибочно продолжился. Предыдущий release 7a3c2c12e6c7 восстановлен. После явного дополнительного восстановления проверен живой /health: storage_ready=true, dispatcher_ready=true; HTTP 503 вызван существующей исторической очередью reconciliation_required=1452, uncertain=1409. Эти доставки не переигрывались по прямому запрету пользователя. Не выдавать этот health за зелёный.

Чужие два коммита объединены через обычный merge d9796fd, опубликованный HTTPS (SSH 443 временно отказал); reset/force/WIP скрытие не применялись. 117 integrated tests прошли. Новый дефект взят в работу: deploy/noticeplace upgrade сверяет HEAD с свежим remote main и заданным NOTICEPLACE_PUBLISHED_SHA до любых действий; отказ связи/расхождение прекращает upgrade. 3 subprocess regression red до исправления, 7 deploy checks green после.
