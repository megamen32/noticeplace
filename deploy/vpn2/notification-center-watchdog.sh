#!/bin/sh
set -eu
umask 077
now=${WATCHDOG_NOW_EPOCH:-$(date +%s)}
fail_threshold=${FAIL_THRESHOLD:-3}; recovery_threshold=${RECOVERY_THRESHOLD:-2}; retry_seconds=${ALERT_RETRY_SECONDS:-60}; channels=${WATCHDOG_CHANNELS:-telegram}
require_value() { if [ -z "$2" ]; then echo "$1 is required" >&2; return 1; fi; }
check_config() {
  require_value PRIMARY_HEALTH_URL "${PRIMARY_HEALTH_URL:-}"
  require_value PRIMARY_HEALTH_TOKEN "${PRIMARY_HEALTH_TOKEN:-}"
  case "$channels" in *telegram*) require_value TELEGRAM_BOT_TOKEN "${TELEGRAM_BOT_TOKEN:-}"; require_value TELEGRAM_CHAT_ID "${TELEGRAM_CHAT_ID:-}";; esac
  case "$channels" in *matrix*) require_value MATRIX_HOMESERVER "${MATRIX_HOMESERVER:-}"; require_value MATRIX_ROOM_ID_ENCODED "${MATRIX_ROOM_ID_ENCODED:-}"; require_value MATRIX_ACCESS_TOKEN "${MATRIX_ACCESS_TOKEN:-}";; esac
}
if [ "${1:-}" = "--check-config" ]; then check_config; exit 0; fi
check_config
state_file=${WATCHDOG_STATE_FILE:?WATCHDOG_STATE_FILE is required}; lock_dir=${WATCHDOG_LOCK_DIR:-"${state_file}.lock"}
if [ -d "$lock_dir" ]; then
  stale_pid=$(cat "$lock_dir/pid" 2>/dev/null || true)
  if [ -n "$stale_pid" ] && ! kill -0 "$stale_pid" 2>/dev/null; then rm -rf "$lock_dir"; echo 'removed stale watchdog lock' >&2; else exit 0; fi
fi
mkdir "$lock_dir"; printf '%s\n' "$$" > "$lock_dir/pid"
FAILURE_COUNT=0; SUCCESS_COUNT=0; DOWN=0; ALERT_SENT=0; PENDING_KIND=; LAST_ALERT_ATTEMPT=0
if [ -f "$state_file" ]; then while IFS='=' read -r key value; do case "$key:$value" in FAILURE_COUNT:*|SUCCESS_COUNT:*|DOWN:*|ALERT_SENT:*|LAST_ALERT_ATTEMPT:*) case "$value" in *[!0-9]*|'') ;; *) case "$key" in FAILURE_COUNT) FAILURE_COUNT=$value;; SUCCESS_COUNT) SUCCESS_COUNT=$value;; DOWN) DOWN=$value;; ALERT_SENT) ALERT_SENT=$value;; LAST_ALERT_ATTEMPT) LAST_ALERT_ATTEMPT=$value;; esac;; esac;; PENDING_KIND:DOWN|PENDING_KIND:RECOVERED|PENDING_KIND:) PENDING_KIND=$value;; esac; done < "$state_file"; fi
cleanup() { rm -f "${health_body:-}" "${tmp:-}" "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true; }
trap cleanup EXIT HUP INT TERM
save_state() { mkdir -p "$(dirname "$state_file")"; tmp="${state_file}.tmp.$$"; printf 'FAILURE_COUNT=%s\nSUCCESS_COUNT=%s\nDOWN=%s\nALERT_SENT=%s\nPENDING_KIND=%s\nLAST_ALERT_ATTEMPT=%s\n' "$FAILURE_COUNT" "$SUCCESS_COUNT" "$DOWN" "$ALERT_SENT" "$PENDING_KIND" "$LAST_ALERT_ATTEMPT" > "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$state_file"; }
health_body=$(mktemp)
if code=$(curl --config - 2>/dev/null <<EOF
url = "$PRIMARY_HEALTH_URL"
output = "$health_body"
header = "Authorization: Bearer $PRIMARY_HEALTH_TOKEN"
silent
show-error
fail-with-body
proto = "=https"
tlsv1.2
connect-timeout = 3
max-time = ${PROBE_MAX_TIME_SECONDS:-8}
retry = 0
write-out = "%{http_code}"
EOF
) && [ "$code" = 200 ] && grep -q '"schema"[[:space:]]*:[[:space:]]*"notify.health.v1"' "$health_body" && grep -q '"service"[[:space:]]*:[[:space:]]*"notification-center"' "$health_body" && grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_body" && grep -q '"storage_ready"[[:space:]]*:[[:space:]]*true' "$health_body" && grep -q '"dispatcher_ready"[[:space:]]*:[[:space:]]*true' "$health_body"; then healthy=1; else healthy=0; fi
send_channel() {
  channel=$1
  if [ "$channel" = telegram ]; then
    curl --config - >/dev/null 2>&1 <<EOF
url = "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
output = "/dev/null"
silent
show-error
fail
proto = "=https"
tlsv1.2
connect-timeout = 3
max-time = ${SEND_MAX_TIME_SECONDS:-10}
retry = 0
data-urlencode = "chat_id=${TELEGRAM_CHAT_ID}"
data-urlencode = "text=NOTIFICATION CENTER ${PENDING_KIND}"
EOF
  else
    curl --config - >/dev/null 2>&1 <<EOF
url = "${MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${MATRIX_ROOM_ID_ENCODED}/send/m.room.message/${now}-$$"
output = "/dev/null"
silent
show-error
fail
proto = "=https"
tlsv1.2
connect-timeout = 3
max-time = ${SEND_MAX_TIME_SECONDS:-10}
retry = 0
header = "Authorization: Bearer ${MATRIX_ACCESS_TOKEN}"
header = "Content-Type: application/json"
data = "{\"msgtype\":\"m.text\",\"body\":\"NOTIFICATION CENTER ${PENDING_KIND}\"}"
EOF
  fi
}
send_pending() { LAST_ALERT_ATTEMPT=$now; for channel in $(printf %s "$channels" | tr ',' ' '); do if send_channel "$channel"; then PENDING_KIND=; ALERT_SENT=1; return 0; fi; done; return 1; }
if [ "$healthy" = 1 ]; then FAILURE_COUNT=0; SUCCESS_COUNT=$((SUCCESS_COUNT + 1)); if [ "$DOWN" = 1 ] && [ "$SUCCESS_COUNT" -ge "$recovery_threshold" ]; then PENDING_KIND=RECOVERED; if [ $((now - LAST_ALERT_ATTEMPT)) -ge "$retry_seconds" ]; then send_pending || true; if [ -z "$PENDING_KIND" ]; then DOWN=0; ALERT_SENT=0; SUCCESS_COUNT=0; fi; fi; fi
else SUCCESS_COUNT=0; FAILURE_COUNT=$((FAILURE_COUNT + 1)); if [ "$FAILURE_COUNT" -ge "$fail_threshold" ]; then DOWN=1; if { [ "$ALERT_SENT" = 0 ] || [ -n "$PENDING_KIND" ]; } && [ $((now - LAST_ALERT_ATTEMPT)) -ge "$retry_seconds" ]; then PENDING_KIND=DOWN; send_pending || true; fi; fi
fi
save_state
