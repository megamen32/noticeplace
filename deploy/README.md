# NoticePlace deployment

`deploy/noticeplace` is the canonical lifecycle owner for the two persistent
NoticePlace services. Run it from a downloaded checkout or from the current
managed release.

```bash
sudo ./deploy/noticeplace install
sudo ./deploy/noticeplace upgrade       # run from the newly downloaded checkout
sudo /opt/noticeplace/deploy/noticeplace status
sudo /opt/noticeplace/deploy/noticeplace rollback
```

`install` and `upgrade` create an immutable release below
`/opt/noticeplace-releases/`, atomically point `/opt/noticeplace` at it, install
the systemd units, and preserve existing `/etc/notification-center*.env` files
and `/var/lib/notification-center*` state. A legacy directory already located
at `/opt/noticeplace` is moved intact into the release directory before the
managed symlink is activated.

`upgrade` does not fetch source code. Download or check out the desired version,
then run its `deploy/noticeplace upgrade`; its committed `HEAD` becomes the new
release. This keeps unrelated working-tree changes out of production. Source
archives without Git metadata deploy their unpacked contents.

On a fresh host the example configuration contains placeholders, so installation
finishes without starting the services. Fill in the two root-owned `0600` files
and start the stack:

```bash
sudoedit /etc/notification-center.env
sudoedit /etc/notification-center-admin.env
sudo /opt/noticeplace/deploy/noticeplace start
```

The persistent services have separate jobs:

- `notification-center.service`, running as `notification-center`, owns the API,
  SQLite-backed queues, AskHuman requests, and external delivery;
- `notification-center-admin.service`, running as root, owns the protected
  operator console;
- `notification-center-admin-apply@.service` is not a daemon. It is a root-owned
  one-shot job used by the admin console to apply validated configuration and
  restart the main service.

AskHuman MCP is a per-agent local process, not another system service. It calls
the human-request API owned by `notification-center.service`. AskSecret belongs
to the separate SSS deployment and can hand NoticePlace only an opaque reference
or completion status.

## Removal

The normal removal is reversible:

```bash
sudo /opt/noticeplace/deploy/noticeplace uninstall
```

It stops and removes the runtime wiring but preserves configuration, SQLite
data, and managed releases. Permanent deletion is deliberately separate:

```bash
sudo ./deploy/noticeplace purge --yes
```

`purge` deletes the preserved configuration, data and releases. It removes the
`notification-center` system account only when this deploy command created it.

The host operator executing `deploy/noticeplace` owns deployment and rollback.
Systemd owns process supervision after installation. The repository owns the
unit templates and lifecycle contract. Host-specific nginx SSO inclusion and
external credentials remain operator-owned configuration.
