# Public landing: source, publish, rollback

The source of the approved interactive Notice Place homepage is `web/landing/`.
The page is a browser-local demonstration, not a view of production incidents.

## Ownership and route boundary

The landing owns only the exact nginx location `= /` on the HTTPS vhost
`notify.bezrabotnyi.com`. All other routes, including `/admin/`, `/health`,
`/v1/`, MCP and the existing proxy/SSO configuration, remain unchanged.
No API, mesh, phone, delivery worker or database restart is needed.
The plain-HTTP redirect is preserved.

`deploy/landing.py` installs one owned include at
`/etc/nginx/snippets/noticeplace-landing.conf`. It adds that include to the
unique existing NoticePlace TLS block without replacing the vhost. Existing
exact root locations, duplicate TLS hosts, unowned snippets and stale vhost
checksums cause a refusal instead of overwriting other work.

Nginx serves an immutable file below
`/var/www/noticeplace-landing/releases/<commit>/index.html`. The exact-root handler pins the
same release as the script CSP hash, so nginx reload cannot mix an old CSP with
new JavaScript. `/var/www/noticeplace-landing/current` and the release manifest
record the active publication; they do not select the response at request time.

The root response permits only the exact embedded JavaScript hash, never
`unsafe-inline` or `unsafe-eval` for scripts. `connect-src 'none'` prevents the
demo from contacting the backend. Inline styles support the existing animation;
images are local or data URIs. Framing and form submissions are blocked.
These headers apply only to the public root, not the operator console or API.

## Publish

From the trusted repository as its ordinary owner:

```sh
python3 web/landing/build.py
python3 -m pytest -q tests/test_public_landing.py
git add web/landing deploy/landing.py docs/public-landing.md tests/test_public_landing.py
git commit -m 'Update public Notice Place landing'
git push origin main
git rev-parse HEAD
```

Inspect the current nginx vhost and calculate its SHA-256 immediately before
publication. Then run as the operator with root privileges:

```sh
python3 deploy/landing.py \
  --revision <full-published-commit> \
  --expected-site-sha256 <reviewed-current-vhost-sha256>
```

The command defaults to `/etc/nginx/sites-enabled/notify.bezrabotnyi.com` and
resolves its existing symlink without replacing the symlink. It reads the
artifact from the requested Git commit, not dirty working files, and checks
that the commit is in the fetched/pushed `origin/main` history. It uses the
server-100 local TLS ingress on `127.0.0.1:8444` for canaries while preserving
the real public hostname and TLS certificate checks.

Before publication the script checks the existing protected route baseline.
After `nginx -t` and graceful reload it checks the exact served HTML hash and
rechecks the same routes. Expected unauthenticated responses:

| Route | Response |
| --- | --- |
| `/` | 200, exact committed HTML |
| `/admin/` | 302 to existing SSO |
| `/health` | 401 |
| `/v1/instructions` | 200 |

Also check the actual public hostname from outside the host and exercise the
local demo in the managed browser on server-88 or server-44. A static HTTP hash
check alone is not evidence that the JavaScript runs under the deployed CSP.

## Rollback and evidence

Before applying changes the deployer stores the existing vhost, existing
snippet when present, and a manifest under
`/var/backups/noticeplace-landing/<time>-<commit>/` (operator-only directory).
A failed nginx validation or canary automatically restores those snapshots,
validates the restored configuration and reloads nginx. NoticePlace runtime
processes are not restarted.

For a later manual rollback, review the backup manifest, restore `vhost.before`
to its recorded `site`, restore `snippet.before` when it existed (otherwise
remove only the owned new landing snippet), run `nginx -t`, then gracefully
reload nginx. Do not delete immutable releases still referenced by a live or
rollback snippet. The `current` symlink is bookkeeping only and should be
updated to match the restored snippet after verification.

The frontend/build resource budget and offline verification scope are recorded
in `web/landing/README.md`. Graphify is updated when its CLI is available; its
absence must not be presented as a successful graph update.
