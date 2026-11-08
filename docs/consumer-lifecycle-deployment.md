# NOTICEPLACE_CONSUMER_V2 deployment map

`docs/consumer-lifecycle-registry.json` records known consumers, evidence level,
source paths, installed executor paths and remaining mapping gaps. Its marker
means registry membership; `source_marker_present` and `migration_status` are
separate facts. Some registry descriptions are historical preparation notes;
do not use them as current deployment receipts. The 8 November reconciliation
of 27 published rows, ten additional sources and 113 retained alias groups is
in the primary checkout at
`.agents/review-artifacts/20261108-consumer-coverage-final/coverage.json`.
It explicitly retains `coverage_complete=false` and distinguishes published
source, installed hashes, natural timer outcomes and fresh source closure.
See [the current review](quiet-operations-review-20261108.md) for release limits.
A systemd oneshot being inactive after exit0 is not a disabled timer.

## Shared source client

Install the published NoticePlace package with
`notification_center.consumer_lifecycle.SourceVerifier`; existing adapters import
this stdlib-only module from the canonical installed package. Do not copy an SDK
into each repository. Producers on remote hosts need that package deployed there;
Python import resolution must be checked before changing a timer.

Constructor: `SourceVerifier(base_url, verifier_token, project, source_id,
host_id, state_dir)`. The authenticated source has `incident_role: verifier`
and bounded source authority; its principal must differ from the executor's.
It may reuse the existing producer credential with an exact server-owned grant
as described in [the credential contract](consumer-lifecycle-credentials.md).
The executor callback never receives verifier authority. Each source uses its
own service-owned mode0700 state directory.

`tick(probe)` pulls due checks; calls `probe(group)` on a fresh real measurement;
then submits `{request_id,generation,observed_state,evidence_refs,observed_at}`
and saves the receipt. Return the actual measurement time in `observed_at`,
especially if one tick shares a sample across requests; poll time cannot make
an old sample fresh. `probe` must match exact host, signal and problem type, preserve consumer
identity from the server's group, and return `unknown` for missing/unsupported
metrics. A CPU/load alias may share the canonical CPU check; disk is distinct.
No agent statement, stale sample or process-alive check proves recovery.

For an existing episode, run source checks before ordinary events from the same
observation, since an ordinary degraded event advances generation and invalidates
an outstanding request. An unknown POST admission fences dependent recovery or
continuation until exact receipt reconciliation. On the next tick the client
reads GET incident.lifecycle.last_check_receipt and clears the fence only when
request ID, idempotency key, generation and observed state all match; otherwise
it leaves the intent pending without retrying POST. It must not suppress independent
new-fault registration. Poll failures are not healthy observations. Adapters own
that distinction; this client never sends ordinary events or launches repair.

## Cutover and acceptance

1. Publish and install the compatible API and authoritative executor adapter.
2. Prepare separate source verifier and executor principals while preserving
   unrelated production policy and all QA flags. Verify exact effective policy
   on every API replica, including automatic diagnosis when this source needs it.
3. Enable each adapter opt-in `NOTICEPLACE_CONSUMER_V2=1` in its exact
   owner environment after helper import/scope validation; adapters can reuse the
   existing producer token, so a new secret environment variable is optional.
   Install adapters through their owning project's canonical deploy route;
   preserve unrelated WIP and use frozen, published source inputs. Inspect the
   current branch/deployment contract instead of relying on an old branch name.
4. Validate installed helper imports and exact source scopes on every affected
   host before enabling polling. Read back installed hashes and timer commands.
5. Use isolated muted QA only: work-finished -> pull -> fresh source measurement ->
   check-result. Healthy closes; degraded centrally schedules exactly one bound
   continuation; unknown delays another source measurement without a repair job.
6. Update registry entries using installed readback and real source evidence.
   A normal tick with no eligible check proves execution, not a production
   recovery. Record that named limit; do not force an outage, replay an UNKNOWN
   operation or submit a guessed healthy result to fill an acceptance cell.

Notification-only inbox events and security ban/unban notifications retain their
protocol and do not become repair watchdogs. UserIO remains transport owner.
The historical L04 slice changed no runtime. Later installations have separate
receipts; that old statement does not describe the present fleet.
