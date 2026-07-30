# Independent vpn2 watchdog

This is the second notification failure domain. It runs on `vpn2`, probes the
primary center through its external HTTPS route, and sends a direct alert without
calling the primary event API.

The runtime is only POSIX `sh`, `curl`, and standard Linux userland. It does not
need Node, npm, Python, a database, or the primary notification-center package.

## Behavior

- The probe requires HTTP 200 and the complete `notify.health.v1` readiness
  contract: expected service identity, `status=ok`, storage ready, and dispatcher
  ready.
- The health request uses the dedicated `PRIMARY_HEALTH_TOKEN`. Redirects are
  not followed, so the configured public route itself must work.
- Three consecutive failures declare `DOWN` by default. Two consecutive healthy
  probes declare `RECOVERED`; this suppresses one-sample flapping.
- Direct delivery uses ordered failover from `WATCHDOG_CHANNELS`. With
  `telegram,matrix`, Matrix is attempted only when Telegram fails.
- A total delivery failure remains pending in the mode-0600 state file and is
  retried after `ALERT_RETRY_SECONDS`, including across service restarts.
- The process lock is a bounded lease. `WATCHDOG_LOCK_STALE_SECONDS` defaults
  to 120 seconds; a fresh lease blocks overlap, while an expired lease is
  reclaimed even if its PID is missing, malformed, or has been reused.
- Recovery is sent only if a DOWN alert was actually delivered. A brief outage
  that recovered before any alert transport succeeded does not create a confusing
  standalone recovery message.
- State never contains health, Telegram, or Matrix credentials.

## Install on vpn2

Run these as an administrator on the independent host:

```sh
install -d -o root -g root -m 0755 /opt/notification-center-watchdog
install -o root -g root -m 0755 \
  notification-center-watchdog.sh \
  /opt/notification-center-watchdog/notification-center-watchdog.sh
install -o root -g root -m 0644 README.md \
  /opt/notification-center-watchdog/README.md

id notify-watchdog >/dev/null 2>&1 ||
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin notify-watchdog

install -o root -g root -m 0600 \
  notification-center-watchdog.env.example \
  /etc/notification-center-watchdog.env
install -o root -g root -m 0644 \
  notification-center-watchdog.service \
  /etc/systemd/system/notification-center-watchdog.service
install -o root -g root -m 0644 \
  notification-center-watchdog.timer \
  /etc/systemd/system/notification-center-watchdog.timer
```

Edit `/etc/notification-center-watchdog.env` with watchdog-only credentials.
The health token must match the primary center's dedicated
`NOTIFY_CENTER_HEALTH_TOKEN`; do not reuse an event-ingest token.

Validate before enabling:

```sh
set -a
. /etc/notification-center-watchdog.env
set +a
/opt/notification-center-watchdog/notification-center-watchdog.sh --check-config
systemd-analyze verify /etc/systemd/system/notification-center-watchdog.service
systemd-analyze verify /etc/systemd/system/notification-center-watchdog.timer
systemctl daemon-reload
systemctl enable --now notification-center-watchdog.timer
systemctl list-timers notification-center-watchdog.timer
```

The config check validates required variables but performs no network requests
and prints no secret values.

## Acceptance drill

Use a planned maintenance window. Disable or firewall only the primary external
health route, leaving Telegram and Matrix egress from `vpn2` intact. Confirm:

1. no DOWN alert before `FAIL_THRESHOLD` consecutive timer runs;
2. exactly one DOWN alert after the threshold;
3. Matrix receives the alert if Telegram is deliberately made unavailable;
4. no recovery after only one healthy probe;
5. exactly one RECOVERED alert after `RECOVERY_THRESHOLD` healthy probes;
6. `/var/lib/notification-center-watchdog/state` is mode 0600 and contains no
   credentials.

The watchdog cannot detect the failure of `vpn2` itself. If that failure also
needs coverage, use a third-party dead-man monitor outside both hosts.
