# Fixed S21 phone-call adapter

This optional fallback calls one preconfigured cellular number after Matrix
does not answer.  It does not use ADB once provisioned.

```text
NoticePlace -> GPTAdmin ShellMCP -> notify-phone-call -> Termux:API -> cellular call
```

The Notify process sends exactly this command to the dedicated S21 ShellMCP
target:

```text
exec /data/data/com.termux/files/home/.local/bin/notify-phone-call
```

It never forwards an incident title, body, recipient, shell argument, or phone
number.  The on-phone script reads its fixed recipient from a private file.

## Phone-side one-time setup

Install **Termux:API from the same source as the already installed Termux**
(F-Droid with F-Droid, GitHub with GitHub).  Then, in Termux:

```sh
pkg install termux-api
install -d -m 700 "$HOME/.config" "$HOME/.local/bin"
printf '%s\n' 'NOTIFY_PHONE_TARGET=+<E164-recipient>' >"$HOME/.config/notify-phone-call.env"
chmod 600 "$HOME/.config/notify-phone-call.env"
```

Create `~/.local/bin/notify-phone-call`:

```sh
#!/data/data/com.termux/files/usr/bin/sh
set -eu
. "$HOME/.config/notify-phone-call.env"
case "${NOTIFY_PHONE_TARGET:-}" in
  +[0-9]*) ;;
  *) echo 'NOTIFY_PHONE_TARGET must be E.164' >&2; exit 64 ;;
esac
exec termux-telephony-call "$NOTIFY_PHONE_TARGET"
```

```sh
chmod 700 "$HOME/.local/bin/notify-phone-call"
```

Grant the Termux:API phone permission when Android asks.  Do not put the phone
number in GPTAdmin, NoticePlace, a shell history, or a repository.

Install and register the normal outbound GPTAdmin ShellMCP agent from Termux
under a dedicated `s21-phone` target.  The agent must be reachable by the
already configured GPTAdmin hub, but no ADB connection is involved in runtime
delivery.

## NoticePlace configuration

Create a **dedicated GPTAdmin credential for the `s21-phone` target** and put
it in the existing root-owned `/etc/notification-center.env`:

```ini
GPTADMIN_ANDROID_PHONE_CALL_URL=https://gptadmin.bezrabotnyi.com/server/s21-phone/actions/tools/shell_exec
GPTADMIN_ANDROID_PHONE_CALL_TOKEN=<dedicated-token>
GPTADMIN_ANDROID_PHONE_CALL_TIMEOUT_SECONDS=20
```

Do not configure `ANDROID_ADB_SERIAL` or `ANDROID_TELEGRAM_TARGET` in that
same service: the process deliberately rejects mixing the direct-ADB and
fixed-ShellMCP transports.  Restart only after the target and token are
verified:

```sh
sudo systemctl restart notification-center
sudo systemctl is-active notification-center
```

`android.phone.call` remains the existing post-Matrix fallback.  A successful
HTTP response means GPTAdmin accepted the fixed command; it does not claim the
carrier completed the call.

## Android Remote Control MCP

Android Remote Control MCP is a separate broad S21 control surface.  Keep its
built-in bearer authentication on and do not enable a public tunnel.  It is
useful for UI, app, notification, and diagnostics work, but it is not trusted
as the phone-call policy boundary above.
