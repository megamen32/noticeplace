# Personal AskHuman phone questions

Bound ordinary AskHuman `question` and `choice` requests are reserved once in
`question_phone_calls`. Notice Place keeps the existing Telegram card as the
actionable fallback. Call status exposed on the request is limited to `state`
and `reason`; it contains no caller number, speech transcript, provider output,
or call target.

Calling is opt-in. Preferences are stored in the existing protected
`runtime_settings` database under `question-phone:<recipient>`, so they survive
worker restarts and are shared by Notice Place replicas. The policy must include
an explicit `approval_ref`, enabled state, `Europe/Moscow`, approved weekdays
and exact opening/closing times, `ru-RU`, one already allowed actor, and the
fixed `agentcall.configured_owner` reference. No recipient number is stored in
these preferences. Incomplete preferences leave the question pending in
Telegram. The owner schedule lives in the existing SSO and CSRF protected
`/admin/#question-phone` form. It shows Moscow weekdays 13:00–20:00 as a disabled
draft; enabling it requires selecting an already allowlisted Telegram actor and
checking the explicit consent box. Changes are audited without a phone number.

The dispatcher runs in the existing call worker. It verifies the pending
Agent Herder request and exact native session, checks HumanStop and request
state, enforces the window before reservation and immediately before transport,
and atomically reserves one attempt. An uncertain send or a process restart
does not redial. Ordinary transcripts do not resolve a request. Only an
authenticated native `noticeplace.question.v1` receipt correlated to that
attempt and carrying an explicit confirmed/read-back speech or DTMF answer can
invoke the existing `complete_human_request` path. That path resolves the same
request, edits the same Telegram card, and uses the existing opaque Herder
completion receipt to continue the originating session once.

The installed AgentCall prepare endpoint currently lacks the question protocol,
read-back/confirmation receipt, and dial-time deadline enforcement. Notice
Place's existing mesh requester therefore advertises `question_ready=false`
and rejects interactive requests before originating a call. The personal
preference remains disabled until the owner approves the stored schedule and
an existing authorized gateway supplies those guarantees. The failed Android
MCP test and an Asterisk service being active do not prove an answered call.

Focused synthetic verification:

```sh
python3 -m pytest tests/test_personal_question_calls.py -q
```

The live user journey still requires an approved in-window answered call, a
confirmed answer on the same request, and the exact native session's resume.
