# Public landing: source, publish, rollback

The source of the approved interactive Notice Place homepage is `web/landing/`.
The page is a browser-local demonstration, not a view of production incidents.

## Ownership and route boundary

The landing owns only the exact nginx location `= /` on the HTTPS vhost
`notify.bezrabotnyi.com`. Its visible **Войти** link targets `/admin/`. That
route, including its existing redirect to `auth.bezrabotnyi.com` SSO, remains
unchanged, as do `/health`, `/v1/`, MCP and the existing proxy configuration.
No API, mesh, phone, delivery worker or database restart is needed. The
plain-HTTP redirect is preserved.

Nginx is managed from the canonical `ServersAdministartion/nginx-dev`
checkout. The tracked source file
`nginx-dev/state/files/snippets/noticeplace-landing.conf` is applied by
`nginxctl`; do not edit `/etc/nginx` directly. Its sync timer checks source
state once per minute and restores committed snippet contents.

Nginx serves an immutable file below
`/var/www/noticeplace-landing/releases/<commit>/index.html`. The exact-root handler pins the
same release as the script CSP hash, so nginx reload cannot mix an old CSP with
new JavaScript. The managed snippet selects the immutable release directly;
`/var/www/noticeplace-landing/current` is historical bookkeeping and does not
select the response.

The root response permits only the exact embedded JavaScript hash, never
`unsafe-inline` or `unsafe-eval` for scripts. `connect-src 'none'` prevents the
demo from contacting the backend. Inline styles support the existing animation;
images are local or data URIs. Framing and form submissions are blocked.
These headers apply only to the public root, not the operator console or API.

## Stage and publish

From the trusted repository as its ordinary owner:

```sh
python3 web/landing/build.py
python3 -m pytest -q tests/test_public_landing.py
git add web/landing deploy/landing.py docs/public-landing.md tests/test_public_landing.py
git commit -m 'Update public Notice Place landing'
git push origin main
git rev-parse HEAD
```

After pushing the NoticePlace artifact to `origin/main`, stage it and its
matching snippet artifact as root:

```sh
sudo python3 deploy/landing.py --revision <full-published-commit> --stage-only
```

Copy the emitted `noticeplace-landing.conf` to
`/home/roomhacker/ServersAdministartion/nginx-dev/state/files/snippets/noticeplace-landing.conf`.
From `/home/roomhacker/nginx-dev` (the stable symlink to that canonical checkout),
run `./nginxctl refresh` and `./nginxctl check`, commit and push the scoped
`nginx-dev/state` change to `origin/main`, then apply it with:

```sh
sudo ./nginxctl apply --path snippets/noticeplace-landing.conf
```

Expected unauthenticated responses after the managed apply:

| Route | Response |
| --- | --- |
| `/` | 200, exact committed HTML |
| `/admin/` | 302 to existing SSO |
| `/health` | 401 |
| `/v1/instructions` | 200 |

Also check the public hostname and exercise the page in the managed browser on
server-88 or server-44. A static HTTP hash alone does not prove JavaScript
runs under the deployed CSP.

## Rollback and evidence

`nginxctl apply --path` validates and rolls back the managed snippet if needed.
NoticePlace runtime processes are not restarted.

For a manual rollback, restore the previous committed snippet in
`nginx-dev/state/files/snippets/noticeplace-landing.conf`, run
`nginxctl refresh` and `nginxctl check`, push the scoped change, then apply it
with `nginxctl apply --path snippets/noticeplace-landing.conf`. Keep immutable
releases while a committed or rollback snippet references them.

The frontend/build resource budget and offline verification scope are recorded
in `web/landing/README.md`. Graphify is updated when its CLI is available; its
absence must not be presented as a successful graph update.
