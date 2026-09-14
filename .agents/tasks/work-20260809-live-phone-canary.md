# Live NoticePlace phone canary

Status: work

Original request: последний раз запустить первоначальный звонок, чтобы проверить реальную доставку.

Objective: place one real cellular call through the configured production S21 adapter.

Business canary: the adapter verifies the configured S21 is connected and voice service is registered, then Android accepts an `ACTION_CALL`; Notify delivery receipt is recorded separately when using the queued path.

Scope: one explicit user-authorized phone call. Exclude repeated calls, policy changes, quiet-hours changes, and credential changes.

Initial estimate: optimistic 2 minutes; likely 5 minutes; pessimistic 10 minutes.

## Result

- The first manual attempt was fail-closed before the call because the service user could not read the root-owned environment/source path.
- The authorized production adapter was then invoked with the root-owned `/etc/notification-center.env`; it resolved as `AndroidPhoneAdapter`, `can_phone_call=True`, and returned `call_command=accepted`.
- S21 telephony evidence immediately after invocation showed one cellular call in `state=DIALING`; the target was redacted from output.
- No repeated call, quiet-hours change, policy change, or credential change was made.

Status: complete
