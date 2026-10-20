# Personal AskHuman phone questions

## Ownership and call path

Notice Place is the only external entry point for agents and services: its
authenticated API and MCP own actor/recipient authorization, consent, quiet
hours, deduplication, pending-question state, Telegram fallback, and
idempotent answer delivery to the exact bound native session. Callers never
receive direct access to UserIO, AgentCall, Asterisk, or an Android device.

For voice delivery, Notice Place delegates to the existing authenticated
UserIO Voice/AgentCall bridge. UserIO owns call preparation, TTS/STT and the
voice lifecycle; DTMF support for the question protocol is not verified in the
installed bridge source. Its current source originates through the existing
Asterisk server. UserIO's phone setup describes a GSM2SIP handset as one
possible SIP leg, so Android may be an internal carrier endpoint rather than
a competing Notice Place adapter. The actual physical leg for an individual
call remains unknown unless a receipt is correlated across AgentCall and
Asterisk. The running UserIO container and its bridge are not Notice Place
services and must not be restarted or modified by this feature.

The MCP's legacy direct `call` operation is one-way TTS followed by hangup. Its
phone route selects only the authenticated UserIO Voice/AgentCall adapter and
requires a live readiness result; it never falls through to a direct Android
adapter. The currently installed AgentCall request starts a spoken greeting
and then an interactive voice session; it does not implement bounded one-way
TTS plus hangup. Notice Place therefore rejects this direct operation before
origination until the existing voice bridge exposes that capability. Android
can still be a private SIP/GSM2SIP carrier leg behind the existing runtime.
The direct call does not represent an interactive AskHuman question and cannot
resolve a request or resume a session. Interactive questions require the
separate `noticeplace.question.v1` call-bound confirmation contract below.

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
the existing authorized gateway supplies those guarantees. The user reports
receiving a call, but without a correlated call receipt this does not identify
its physical leg or prove an answered AskHuman question.

Focused synthetic verification:

```sh
python3 -m pytest tests/test_personal_question_calls.py -q
```

The live user journey still requires an approved in-window answered call, a
confirmed answer on the same request, and the exact native session's resume.
