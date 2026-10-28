# L10 video/winramp source lifecycle — source handoff

2026-10-28, worker video_consumers. No deployment, restart, live incident, phone,
owner notification, payment or other business mutation was performed by the worker.

## Checkout and delivery

Canonical source: `/home/roomhacker/PycharmProjects/video_watching`, branch `dev`,
base HEAD `070c8b8e0b5157a979a00e7310a40ff5d2dd79fa`, remote
`https://github.com/megamen32/video_watching.git`. Project AGENTS and
`docs/operations/deploy-playbook.md:192` require integrated work on dev, pushed
origin/dev, main fast-forward only for completed production-ready work. No branch
or worktree was created/switched. Foreign untracked `.xerjignore` preserved.
`.agents/TEST_ACCESS.md` absent; work stayed inside Lead-authorized isolated tests
and read-only runtime inspection.

## Actual finite consumers

| Consumer | Existing path | Fresh source verification |
|---|---|---|
| noticeplace_health CLI | alert_selfheal real caller, others raw health ingress | installed stdlib SDK before ordinary event; current explicit timestamp <=60s + matching type, otherwise unknown; v2 never legacy resolve |
| alert_selfheal | canonical checkout cron */5 | request callback reruns read-only DB views/comments/registration/proxy; no pace-state writes; existing comment-orchestrator HTTP probe; fleet monitor command without --send requires targets and no collector_error; unclassified/partial output unknown |
| vtop_day1_watch | canonical checkout cron 23,53 | callback rereads DB rows and numerator likes after request; missing row/likes unknown; no boost/order from callback |
| capacity_forecast | user rutube-capacity-forecast.timer active waiting, canonical checkout | callback reruns same complete forecast in dry_run with fresh connections/queries; observed decisions returned separately; missing/young/no usable measurement unknown; fixed dry-run VOID_VERDICT write |
| socproof_topup_watch | root crontab */30, canonical checkout script; deployed working directory /opt/rutube/current/server/UrlsSevr/telegramcontrol, protected runtime-db.env sourced | async request callbacks rerun _create_health, _provider_balance and _link_stall reads; no order create/extend/probe purchase; missing provider balance unknown |
| bot noticeplace_producer | rutubeserver deployed bundle | serialized fsynced submitting intent before POST; accepted/unknown never replay after cooldown; detection spool retained, second dated ingress suppressed; no independent healthy error-specific detector means unknown |
| rmdream_watch | canonical checkout cron 7,22,37,52 | notification-only recipient admin/kind notification, no health launch; excluded |
| socproof_health | sibling pure math | not a producer or runnable watcher; excluded |

Bot errors use dynamic source IDs (tg-bot, shorts-count, admin-scraper and others).
Their durable admission records preserve identity; watchdog reads these identities
and answers unknown rather than calling an old spool a healthy measurement.

## Protocol and installation dependencies

`NOTICEPLACE_CONSUMER_V2=1` is opt-in. SDK loaded from
`NOTICEPLACE_HELPER_DIR` or `/opt/noticeplace/notification_center`; missing required
SDK fails visibly. Existing producer credentials remain in their protected env files.
Verifier token priority: NOTICEPLACE_SOURCE_VERIFIER_TOKEN, NOTIFYCENTER_TOKEN,
NOTIFY_CENTER_TOKEN. Existing token must have source-verifier role and
verification_owned_sources=true; no launch credential belongs to a producer.
No secrets were printed or copied.

Source check sequence: server GET -> fresh callback -> durable SDK intent -> POST.
Ambiguous SDK POST only GET-reconciles exact receipt/request/generation; no blind
replay. CLI event opening and bot admission also stop at durable unknown. Lifecycle
owns closing/retrying; v2 recovery never calls old verification/resolve endpoints.

## Verified runtime map, read-only

Host hostname roomhacker-server-100. rutubeserver.service and rutubeweb.service
active. Working directories under `/opt/rutube/current/server/UrlsSevr`.
Current server symlink resolves `/opt/rutube/releases/server/1.84.0-887fb55cf8866452`;
manifest git_commit is `ee08d2bd98b217d3a3ecc2bf9a91077a145d8c04`, release_id
`srv-1.84.0__cli-1.84.1__ee08d2bd98b2`. Do not infer source identity from dirname.
Protected service env references: `/opt/rutube/state/server/runtime-db.env`,
`proxy5.env`, `llm-providers.env`, `/etc/comment-orchestrator/runtime-auth.env`.
Watchdog NP env reference `~/.config/notify/video-watching.env`; winramp raw-health
watchers reference `~/.config/notify/winramp.env`. No values inspected in output.

Important: cron/timer source points directly at canonical checkout, so source edits
may become visible on their next scheduled tick without a bundle deployment. Bot
code remains bundle-owned. Opt-in defaults off and was not changed by this worker;
SDK-dependent v2 is disabled until the SDK/grant/flag installation. Source edits
are nevertheless visible to scheduled checkout runners, so this report does not
claim that runtime behavior was wholly unchanged. Bot bundle/manifest stayed old.

## Evidence and remaining acceptance

110 focused tests passed in 2.16 seconds (6 existing peewee deprecation warnings).
Includes real SDK integration with fake HTTP transport, GET-before-probe order,
ambiguous POST no replay, current caller missing measurement unknown, stale healthy
never legacy-closes, bot accepted/unknown spool no second ingress, concurrent bot
admission one POST, failed durable intent zero POST. py_compile and diff --check pass.
No installed-runtime or real-consumer acceptance is claimed.

Lead must review and authorize scoped publish; own-project delivery target is dev.
Then install SDK/grants/opt-in and bundle bot via owning release pipeline, verify
actual scheduled consumer readback, using the verified root crontab socproof runner.
Bot error sources lacking an independent detector stay unknown; closing them based
on absence of a repeated chat error would be a false healthy claim.

## Scoped source commit

Lead approved exactly nine owned paths. Commit `ae1f80859d23ca4639f328f7512874aeaa1e0351`; optional automatic pre-push full suite was stopped only for its verified owned process group by explicit Lead choice; partial receipt preserved in evidence/L10-optional-prepush-aborted.{txt,json}. One Lead-authorized --no-verify push failed HTTP 408/sideband disconnect (exit 1); subsequent authoritative ls-remote confirms origin/dev remains 070c8b8e0b5157a979a00e7310a40ff5d2dd79fa, so this commit is not yet published. No UNKNOWN push replay. Foreign `.xerjignore` untouched. No NP plan/artifact paths included in video commit.

## Publication verified

`git -c http.version=HTTP/1.1 push --no-verify origin dev` succeeded exit 0. Independent `git ls-remote origin refs/heads/dev` equals local HEAD `ae1f80859d23ca4639f328f7512874aeaa1e0351`. Exactly nine owned paths committed. `git status --short` contains only foreign untracked `.xerjignore`; diff --check passes. No further tests were started. Source publication complete; runtime acceptance remains Lead-owned, V2 flag unchanged/default off, old bot bundle manifest ee08d2bd98b217d3a3ecc2bf9a91077a145d8c04.
