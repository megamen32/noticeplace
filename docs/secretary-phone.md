# Ordinary Secretary phone messages

The existing AgentCall adapter recognizes `event_type=secretary.call` as an
ordinary assistant message. It introduces itself with «Я помощник Никиты»,
speaks the supplied body once, and discusses that message using the existing
validated `operator_note` (maximum 500 characters) as additional context.

Secretary context contains no incident severity or Agent Herder instructions,
and does not inherit an unrelated agent session. Existing incident, lead and
operator-test modes retain their prior behavior. Provider preparation/dialing,
quiet hours and durable call receipts remain the existing dispatcher path.

The typed Secretary host API is maintained by ServersAdministartion at
`infra/android-s21/gsm2sip/ai-bridge/SECRETARY-CALL-API.md`. Its initial recipient
is only the configured owner, with a dedicated one-step consumer and one
repeat. It persists message/body and context/operator_note before dispatch.
An exact replay reads the existing request, while UNKNOWN provider outcomes
must never be redialed.

Focused verification uses no actual phones or providers:

```sh
python3 -m unittest discover -s tests -p test_secretary_phone.py
python3 -m unittest discover -s tests -p test_agentcall_phone.py
```

Secretary tests cover the actual queue → worker → adapter → fake socket
request → durable exact call receipt path and unchanged incident modes.
Expected combined runtime is under three seconds; maximum ordinary release
run is 180 seconds. Source tests do not prove audible consumer acceptance;
root owns runtime activation and the final authorized audible call.
