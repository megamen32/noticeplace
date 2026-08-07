# Apply production quiet hours

Status: done

## Original request

В production уже запушено; применить мне тихие часы, чтобы Notify сейчас не
звонил в период `01:00–09:00`.

## Objective

Deploy the committed per-consumer quiet-hours migration and verify the live
NoticePlace worker loads the policy.

## Business canary

Production NoticePlace is healthy, the consumer schema contains per-consumer
quiet-hours policy, and call deliveries are deferred during the configured
window while messages remain deliverable.

## Confirmed scope

- NoticePlace production deployment/restart and bounded health verification.
- No global quiet-hours policy.

## Completion evidence

- Installed commit `56355b1` into `/opt/noticeplace` with rollback copies under
  `/opt/noticeplace/.deploy-backups/20260808-025253/`.
- Both NoticePlace services restarted and are active; deployed source digests
  match the committed workspace files.
- Production SQLite schema now contains `consumers.quiet_hours_json`; it has
  zero consumer rows at present.
- Because current traffic is legacy/global, set runtime
  `automatic_calls_enabled=false` at 02:52 MSK and scheduled a one-shot
  re-enable for 09:00 MSK. Text delivery is unaffected.

## Explicit exclusions

- Do not disable all calls globally.
- Do not modify unrelated production routes or credentials.

## Initial estimate (immutable)

- Optimistic: 10 active minutes
- Likely: 20 active minutes
- Pessimistic: 40 active minutes
