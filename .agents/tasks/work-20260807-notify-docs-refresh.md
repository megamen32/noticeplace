# Notify documentation refresh

Status: work

## Assignment

Adviser/Worker must audit the current Notify repository and rewrite the user-facing documentation to match the implemented API, protected `/admin/` entrypoint, live runtime settings, unified Telegram topic editor, visual adapter builder, call escalation behavior, and current deployment/configuration boundaries. Preserve accurate examples, remove stale claims, and report changed files and verification. Do not change production configuration.

## Acceptance

- Documentation is grounded in current source/tests and current nginx topology.
- Stale landing-page, old route-editor, restart-required, or raw-policy claims are corrected.
- A concise changelog of documentation changes and unresolved ambiguities is returned.

## Worker evidence (2026-08-07)

### Audited sources and topology

- `notification_center/http_api.py`: `GET /` returns `303` with
  `Location: /admin/`; `GET /health` requires the dedicated health token;
  `GET /v1/incidents/{id}` uses the project bearer; `POST /v1/events`, the
  incident `ack|resolve|snooze` actions, and `POST /mcp` are distinct routes.
- `notification_center/admin_http.py` and `deploy/notification-center-admin-nginx.conf`:
  nginx performs the internal auth subrequest, redirects `/admin` to
  `/admin/`, overwrites `X-Notify-Admin: 1`, and proxies `/admin/` to
  `127.0.0.1:8092`; the admin handler rejects the trusted-header boundary and
  uses CSRF tokens for mutations.
- `notification_center/admin.py` and `notification_center/core.py`: runtime
  timing values use SQLite with startup environment fallback during migration;
  save/toggle/topic mutations do not restart the worker, while producer scope
  and legacy environment route changes retain atomic restart/rollback behavior.
  Seeded and custom topics share `save_topic`/`delete_topic`; generic policy
  steps require one root and valid predecessor references.
- `notification_center/core.py` and `notification_center/http_api.py`: bounded
  claim leases may be reclaimed after a crash, making external delivery
  at-least-once; Matrix, Android Telegram, and Android phone adapters are
  implemented optional paths and failed/unconfigured calls retry.

### Changed documentation

- `README.md`: added the redirect/auth boundary, current HTTP route map, and
  at-least-once adapter caveat.
- `docs/admin.md`: replaced the restart-for-every-mutation claim with explicit
  restart boundaries; documented live settings after migration fallback, topic
  CRUD, visual generic linked-step builder, call escalation, and delivery
  semantics.
- `docs/notification-center-mvp.md`: replaced the stale Tier-B/future Matrix
  claim with the current HTTP, health, MCP, live runtime, topic, builder, and
  call contracts; retained the health/vpn2 boundary.
- `docs/human-request.md`: clarified that HumanRequest call policy remains
  proposed while Notify's optional call adapters are implemented.

### Verification

- `pytest -q tests/test_admin_console.py tests/test_http_api.py tests/test_delivery_worker.py`
  → `32 passed in 12.28s`.
- Relative Markdown link scan across `README*.md` and `docs/*.md` → `OK`.
- `git diff --check` → `OK`.
- `python3 -m unittest tests.test_admin_console tests.test_http_api tests.test_delivery_worker`
  was attempted but cannot import `tests.test_*` because this repository's
  `tests/` directory is not a Python package; pytest is the successful focused
  test path.

### Remaining ambiguities

- The repository contains translated README/MCP/admin-adjacent documentation;
  this pass updated the English canonical docs only. Translation parity is
  still open and was not expanded into this bounded slice.
- The checked-in nginx fragment documents the `/admin/` location but does not
  name the production server block hostname; the docs therefore describe the
  verified proxy topology without asserting an unverified hostname.

## Worker result

Documentation slice complete. No source, test, deployment, or production file
was changed. Changed paths: `README.md`, `docs/admin.md`,
`docs/human-request.md`, `docs/notification-center-mvp.md`.
