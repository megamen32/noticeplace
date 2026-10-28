# Notice: один доверенный Board-ответ продолжает ту же задачу

## До и после

До: Notice task decision умеет создать `td-...` row и дедлайн, но OpenCode verifier требует фиктивный Codex turn; карточка не подтверждает фактический вопрос исполнителя, ответ и task decision жили бы в разных строках.

После: конкретный OpenCode executor регистрирует metadata-only Herder UUID target, затем сам выдаёт typed вопрос. ExManager CAS фиксирует подтверждённый source, task reference и ответ. Notice создаёт одну `human_requests` row с canonical UUID из регистрации и `task_decision_ref=td-...` как alias. UI, `GET /v1/human-requests/{UUID}/result` и outbox читают одну строку; ответ CAS связывается с одним existing Herder resume UUID. Duplicate возвращает ту же квитанцию, UNKNOWN читается query-first.

## Граница

Только `notification_center/task_decision_service.py`, `notification_center/task_decisions.py`, необходимый CAS/dispatcher участок `core.py`, минимальный task-decision HTTP boundary и focused tests. Чужие файлы/изменения, production runtime и реальные сообщения/люди вне области. Общий ExManager 24-leaf план не перепланируется.

## Точный межкомпонентный контракт для свежего ExCode исполнителя

OpenCode target берётся только с текущего `TaskExecutionReceipt`: `hostId`,
`harness`, `nativeSessionId`, `nativeInputMessageId`, `nativeDispatchJobId` и
`projectBinding{projectId, hostId, harness, cwd}`. Для OpenCode нет
`nativeTurnId`. Сам исполнитель заранее вызывает существующий Herder
`human_request_create`; его настоящий зарегистрированный UUID обязателен.

Чтобы подтвердить происхождение вопроса, тот же receipt содержит неизменяемый
`executorQuestion` с `sourceMessageId`, `sourceMessageHash`,
`sourceInputMessageId`, `sourceJobId`, `humanRequestId` (UUID из completed
metadata-only tool result), `registrationMessageId`, `registrationCallId`,
`registrationTarget{harness,sessionId,cwd}`, точный `contextRef`, `text`,
`choices`, `timeoutValue` и `questionHash`. Context ref равен
`exmanager-task:${card.id}:${attempt.operationId}`. `sourceMessageHash` — SHA-256
точного JSON текстового фрагмента вопроса; `questionHash` — SHA-256 sorted-key
compact UTF-8 JSON ровно по `{humanRequestId,sourceMessageId,sourceMessageHash,
sourceInputMessageId,sourceJobId,text,choices,timeoutValue}`. Источник — только
completed assistant JSON от текущего исполнителя в exact OpenCode session;
операторская форма не является доказательством.

Board answer handler после серверной проверки native actor и CAS пишет
`taskDecisionProposal{proposalRef, requestId, requestHash, purpose, version,
value, authenticatedActor, cardVersion, stopEpoch, sourceMessageId,
sourceJobId}`. `stopEpoch` равен последнему direct-control `requestHash`, иначе
`messageInputId`. Notice не принимает actor/value из клиентского тела. Узкий
`POST /v1/task-decisions/{td}/resolve` использует текущую project credential,
`Idempotency-Key` равный `proposalRef`, и только `{proposal_ref}`; Notice
заново загружает точную карточку, проверяет продвижение текущего Board CAS
version отдельно от исходной версии вопроса, request ref, source question,
proposal, автора и STOP до одного existing answer CAS.

Receipt различает `accepted`, `duplicate`, `unknown`, `conflict`; accepted и
duplicate возвращают одну canonical decision/proposal ref. Продолжение имеет
отдельные `state`, Herder UUID `request_id`, answer UUID `result_ref`,
`idempotency_key`, настоящий transport `receipt` и производный native
`input_id`. Это значение читается через существующий read-only
`human_request_get` MCP по owner loopback; `result_ref` не выдаётся за transport
receipt. При `UNKNOWN` повторно читается тот же Herder record, resume callback
не повторяется. `continuation.state=accepted` выдаётся только после того, как
`human_request_get` подтвердил `status=resumed`, точные UUID/result_ref/target,
idempotency key и совпадающую Herder receipt; иначе остаётся `queued` или
`unknown`.

## Приёмка

RED→fix→GREEN на typed OpenCode binding, отсутствующем/source-mismatched
executor question, hash/contextRef, proposal CAS version+1, поддельном actor,
STOP/hold, duplicate и UNKNOWN. Focused Notice regression set: 49 passed.
Изолированный Notice binds 192.168.192.1:19450, свою SQLite DB и QA-only
scoped bearer; ExCode read uses
http://192.168.192.1:19450, Notice verifier uses the already exposed
http://127.0.0.1:43330. QA recipient is a test identity only. No production
restart, public nginx route, phone or real-human notification.

## QA runtime wiring

Для Notice QA использовать тот же source SHA, что опубликован в checkpoint.
В существующий `python -c`/QA launcher передать:
`NOTIFY_CENTER_DB`, `NOTIFY_CENTER_TOKENS_JSON`, `NOTIFY_CENTER_HEALTH_TOKEN`,
`NOTIFY_MCP_TOKEN`, `EXMANAGER_KANBAN_URL`, `EXMANAGER_KANBAN_TOKEN`,
`NOTIFY_CENTER_RECIPIENT`, `ASK_HUMAN_ALLOWED_ACTORS`.
`BoardTaskDecisionVerifier.from_environment()` читает последние четыре
значения Board/actor. Создать `NotificationCenter(db, parsed_tokens,
task_decision_verifier=verifier)` и вызвать `run_http(center,
"192.168.192.1", 19450, health_token, mcp_token)`. `bin/notify-center` для
этого sandbox не запускать: он включает delivery workers и production
адаптеры. В ExCode protected QA env задать
`EXMANAGER_NOTICE_QUESTION_BASE_URL=http://192.168.192.1:19450` и отдельную
QA project credential из той же закрытой token map. Значения credential
хранятся только в защищённом QA env; в task artifact записаны только имена
переменных и endpoint references.

Source checkpoint `63f0b0f` опубликован в `main`. Итоговый focused Notice
regression set: 49 passed. Полноценный `19450` HTTP/browser canary ещё ждёт
protected QA env и запуска на QA fixture. Канонический Board verifier на
`127.0.0.1:43330` отвечает HTTP 200; production Notice и внешняя доставка для
этой проверки не использовались.
