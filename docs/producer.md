# Notify producer migration

`bin/notify-producer` is the narrow, shell-safe client for internal operational
sources. It sends one idempotent `notify.event.v1` request to the durable
center; it never knows Telegram or Matrix credentials.

## Source environment

Each source receives its own mode-`0600` environment file with a token scoped
to exactly one `project`. The center keeps the matching token map. Do not reuse
the health-probe credential as a producer token.

```sh
NOTIFY_CENTER_EVENT_URL=https://notify.bezrabotnyi.com/v1/events
NOTIFY_CENTER_TOKEN=producer-token-for-one-project
NOTIFY_PROJECT=fail2ban.server-100
NOTIFY_RECIPIENT=me
# Optional context shown to the operator and retained as bounded incident metadata.
NOTIFY_OPERATOR_NOTE=server-100 / ssh jail
```

`notify-producer` also accepts `--operator-note`. The field is optional and
must not contain secrets; it is included in the Telegram card as `Note:` and
stored with the incident. Ingress audit separately records the project/profile
and trusted source/proxy IP chain without storing Authorization headers.

## Fail2ban

Install `deploy/fail2ban/notify-center.conf` as the replacement definition for
the *notification* action and its environment template as
`/etc/fail2ban/notify-center.env` (root-owned, mode `0600`). Preserve each
jail's existing firewall action (for example `nftables-multiport`) and replace
only its paired `telegram-env` notification action; do not modify filters, jail
enablement, or ban actions. Validate with `fail2ban-client -d`, then make one
controlled ban/unban using a TEST-NET address before reloading every jail. The
action has a bounded timeout and deliberately ends with `|| true`: an alerting
outage must never prevent the local firewall ban.

A ban creates an incident keyed by `fail2ban:<project>:<jail>:<ip>`. The matching
unban resolves that incident with the same key. Rollback is one action-name
change back to `telegram-env`; retain the previous action file and environment
file until a real ban/unban drill succeeds.

## Independent paths

The `vpn2` notification-center watchdog and the ingress recovery watchdog
remain direct out-of-band delivery paths. They may use the same Telegram/Matrix
identities, but they must not POST to the primary center when the primary center
is the thing being monitored.
