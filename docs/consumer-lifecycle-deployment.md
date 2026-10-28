# NOTICEPLACE_CONSUMER_V2 deployment map

`docs/consumer-lifecycle-registry.json` records known consumers, evidence level,
source paths, installed executor paths and remaining mapping gaps. Its marker
means registry membership; `source_marker_present` and `migration_status` are
separate facts. No entry currently claims a deployed v2 source checker.
Server88 discovery had timeouts and partial/truncated results: coverage is not
complete. A systemd oneshot being inactive after exit0 is not a disabled timer.

## Shared source client

Install the published NoticePlace package with
`notification_center.consumer_lifecycle.SourceVerifier`; existing adapters import
this stdlib-only module from the canonical installed package. Do not copy an SDK
into each repository. Producers on remote hosts need that package deployed there;
Python import resolution must be checked before changing a timer.

Constructor: `SourceVerifier(base_url, verifier_token, project, source_id,
host_id, state_dir)`. The token is verifier-only, with a distinct `principal_id`,
`incident_role: verifier` and `verification_sources: [exact_source_id]`; no Herder
launch credential. Each source uses its own service-owned mode0700 state directory.

`tick(probe)` pulls due checks; calls `probe(group)` on a fresh real measurement;
then submits `{request_id,generation,observed_state,evidence_refs}` and saves the
receipt. `probe` must match exact host, signal and problem type, preserve consumer
identity from the server's group, and return `unknown` for missing/unsupported
metrics. A CPU/load alias may share the canonical CPU check; disk is distinct.
No agent statement, stale sample or process-alive check proves recovery.

For an existing episode, run source checks before ordinary events from the same
observation, since an ordinary degraded event advances generation and invalidates
an outstanding request. An unknown POST admission fences dependent recovery or
continuation until exact receipt reconciliation. Exact receipt readback is prepared separately in L09. It must not suppress independent
new-fault registration. Poll failures are not healthy observations. Adapters own
that distinction; this client never sends ordinary events or launches repair.

## Atomic cutover order (preparation only)

1. L01 API and L02 authoritative executor owner must be published and installed.
2. L09 prepares separate source verifier and executor principals while preserving
   the nine production automatic-diagnosis flags and all QA flags.
3. L07 installs infrastructure adapters; coordinate the foreign dirty fleet-health
   source with its livevusa owner. L08 installs product adapters through their own
   canonical deploy routes. video_watching is on DEV: do not switch branches.
4. Validate installed helper imports and exact source scopes on every affected
   host before enabling polling. Read back installed hashes and timer commands.
5. L06 uses muted QA only: work-finished -> pull -> fresh source measurement ->
   check-result. Healthy closes; degraded centrally schedules exactly one bound
   continuation; unknown delays another source measurement without a repair job.
6. Update registry entries using installed readback and real source evidence.

Notification-only inbox events and security ban/unban notifications retain their
protocol and do not become repair watchdogs. UserIO remains transport owner.
No runtime, configuration, token map or callback file was modified by L04.
