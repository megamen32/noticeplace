# L16 — external-site-monitor: source complete

Published `lol-nginx-admin` main/origin main:
`3b6a97927424c9b708d0f902f779531871fd1f1b`.
Code-map-only successor `bfe9a18827316bb267b9ad597a522e06752693a4`
is also published and verified as origin/main; functional input hashes below
are unchanged. The final checkout is clean.
Functional paths: `monitor/ingest.py`, `monitor/probe.sh`, the monitor README and
two monitor test files. No runtime restart, protected configuration write,
owner notification or direct API send was performed by this leaf.

## Changed behavior

- Marker `NOTICEPLACE_CONSUMER_V2` identifies the source adapter and probe.
- V2 checks due source challenges before new event ingress through the shared
  SourceVerifier SDK. Only matching project/source/host/problem/signal and an
  actual current HTTP measurement can prove healthy. HTTP 5xx is degraded;
  stale/future/missing data and curl/network failures are unknown. An old
  incident correlation remains checkable after local episode state resets.
- A separate telemetry source verifies actual receipt freshness/absence.
- V2 suppresses legacy recovery/resolve events. NoticePlace alone closes an
  incident after the authenticated independent source-check receipt.
- Both direct Telegram bypasses were removed (ingest delivery fallback and
  probe upload failure/recovery). Probe telemetry remains in its bounded spool.
- A fsynced delivery intent precedes POST; confirmed receipt follows success.
  Unknown response, invalid or corrupt delivery state never causes automatic
  replay. A local lock serializes transition submission.
- Existing sustained outage detection, probe retry/proxy ordering and target
  selection remain in place. V2 rejects future or duplicate/old runs before
  replacing current source state.

## Proof

RED: `python3 -m unittest discover -s monitor/tests -p test_external_site_monitor.py -k noticeplace_failure`
failed because NoticePlace failure invoked direct Telegram and hid the error.

Second RED: `python3 -m unittest discover -s monitor/tests -p test_noticeplace_lifecycle.py -k corrupt_delivery`
failed because a corrupt existing intent became a second POST.

GREEN: `python3 -m unittest discover -s monitor/tests` — **18 tests**, about two
seconds. `bash -n monitor/probe.sh`, `python3 -m py_compile monitor/ingest.py`
and `git diff --check` passed. Tests use mock transports and local fixtures;
no real notification is an acceptance result. The owning checkout had no
`.agents/TEST_ACCESS.md`; existing authorization was restricted here to source
and isolated local tests.

## Installed boundary and L06 handoff

Actual server-100 unit readback on 2026-10-28:

- Probe ExecStart: `/opt/external-site-monitor/probe.sh`.
- Existing drop-in LOCAL_INGEST_SCRIPT:
  `/home/roomhacker/lol-nginx-admin/monitor/ingest.py`.
- Stale unit directly executes the same canonical ingest with `--check-stale`.
- VPN2 is a legacy fallback, not the active source. Do not enable it.

Therefore canonical ingest source is visible at the next normal execution;
V2 remains disabled until L06 installs SDK/grants/flag. The installed probe
binary still needs the reviewed source. Extract only `monitor/probe.sh` from
the published SHA into project `.tmp/`, then:

```sh
sudo install -m 0755 <frozen-probe-path> /opt/external-site-monitor/probe.sh
```

Preserve existing unit drop-ins, protected environment, target file and running
sweep. No restart is needed for the next normal execution. After the shared SDK
is installed at `/opt/noticeplace` and grants are consistent, set
`NOTICEPLACE_CONSUMER_V2=1` in `/etc/default/external-site-monitor`.

Protected credential references are unchanged: the monitor environment's
`NOTIFY_CENTER_TOKEN` or existing project-scoped credential in the canonical
NoticePlace env. The fallback lookup now selects the unique source verifier,
never the executor credential.

Source registration: project `fleet-health`, producer `external-site-monitor`,
plugin `lol-nginx-admin`; per-vhost source `external-site:<vhost>`, host `<vhost>`,
problem/signal `https`. Telemetry retains historical
`external-site:vpn2-telemetry`, host `vpn2-telemetry`, problem/signal `telemetry`.
Existing owned-source policy covers new events. Consumer coverage was sent the
requirement to enroll exact historical IDs from the actual target list for old
unbound episodes; this leaf did not edit the shared registration plan.

SHA256 of reviewed inputs:

- ingest.py: `9c5dcea9a9fac8f7ed916bafdede2085f483da81174e37ab691900de644c21c5`
- probe.sh: `7da55d9826a02dbe593655b3a739efbfa21ef7db24d0b8edfaa852473901bca9`

Source-only completion is not installed/runtime acceptance. L06 owns the
coordinated cutover and the single real isolated integration incident.
