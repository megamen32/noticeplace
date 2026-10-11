# NoticePlace phase 2 source ready — 2026-10-12

Owner: native Codex 01a12c47-a796-7d52-b59c-ad551e0a8752.
Tree: noticeplace-repair-first; branch codex/noticeplace-repair-first.
Base: phase1 6a43747001fbb3c5ed982d7844081c565b85d936, already installed by Root.
Delivery: scoped source commit/push only; Root owns integration, install and real consumer acceptance.

## Result

All phases select the first confirmed Telegram receipt, including diagnosis before
initial. A shared SQLite sending/uncertain fence prevents another creation;
unknown transport is not replayed. Ingress and native repairs continue independently.
Recovery during first transport preserves the actual receipt. Late plans and
callbacks cannot reopen a terminal incident or create another card.

The central Russian renderer preserves title, recovery, AI/mute/attention controls
and historical selected choice buttons. Linked questions and all progress paths
use the original message. Closed unanswered questions return no_accept with no
completion/outbox/native send. Partial group recovery shows remaining open members;
a closed parent/open critical child offers ACK targeting that exact child, then
next unread critical member, without acknowledging/resolving the others.

Bounded native readback uses stored harness/SID and details?limit=5&history=auto.
Only last assistant text parts plus safe status/activity/permission are projected;
no reasoning, tool traces, raw message.text or credentials. Changed latest state
flushes durably through existing pool/outbox after >=15s. Explicit human/recovery
reactions keep priority. Sends use explicit severity/phase sound policy; edits are
silent. Similar typed families share the existing original card with independent
records/jobs/audits/links. Existing AdminConfigStore/UI exposes live defaults.

Diagnosis and legacy orchestrator constructors replace persisted no-tools/planner
prose with one whole original-GSD repair contract, retaining bounded original source,
host/signal/fingerprint/correlation/evidence endpoints. The delivery and GPTAdmin
projections now preserve evidence_refs. Plain GSD terminal turns complete the bounded
observer without JSON polling. Running observer deadline returns awaiting_executor,
not native failure, and core leaves health open; actual native failure still fails.
No legacy diagnosis result launches another planner or mandatory plan menu.

Arming passes noticeIncident:{incidentId,project} for the original incident together
with receipt-authoritative harness/SID/CWD. The exact callback JSON/progress contract
was handed to the Herder owner; Herder persists that binding and owns original-card
completion. Protected project tokens/scopes were not expanded or moved. Root resumed
the actual autosell session; this source executor did not replay repair/resume.

## Finite verification

- Main curated 17-file run: 287 passed, 1 failed in 129.23s. The failure was an old
  test expectation hiding an ordinary notification until diagnosis, contradicting
  the newly explicit all-notifications default. That expectation was corrected.
- Final affected regression run: 22/22 passed in 8.15s; includes the corrected
  visibility test, ACK preserving repair, persisted legacy prompt, plain/running
  observer, event->GPTAdmin->prompt->receipt->core, and complete card-delivery tests.
- Executable release runs: 130.609s + 9.625s = 140.234s. Each run finite; 180s/20s
  deadlines, 2GiB address-space bound, two CPU slots. Main peak RSS 86,476KiB;
  delta peak 67,784KiB. Source hashes remained identical after the main run.
- Portable immutable plugin 0.1.0 validation PASS; owned modules/bin compile PASS;
  git diff --check PASS. No broad baseline or nightly run.
- Proof logs and summaries retained in ignored .tmp/phase2-release-final.log,
  phase2-release-summary.json, phase2-release-delta.log and delta-summary.json.
  The main failed result is retained, not relabelled GREEN.

Fast unit/reference/render: detect canonical reference/keyboard/projection defects,
expected <1s/max5s per scenario. Focused integration transport/DB: detect duplicate
creation, unknown replay, terminal races and member-specific ACK, expected 2s/max10s.
Focused SDK/HTTP/button/reply: detect linked-question/progress context loss and late
answer resurrection, expected 3s/max10s. Release window max180s; slow nightly unused.

## Root acceptance remaining

Install/review this frozen branch commit together with the Herder binding/outbox
change and Root's separate foreign canonical queue:true coupling. Verify actual
Debug incident admission -> progress -> choice/free answer -> independent recovery
on one original message, selected buttons retained, concrete member ACK and settings.
Phone/Telegram live acceptance remains Root-owned and is not claimed by these tests.
Existing cards/history were not deleted or migrated. No live service/Telegram/phone
mutation here. Plugin instructions unchanged; version0.1.0 not overwritten. Canonical
ZCode foreign WIP was untouched.

## Frozen source SHA256

- notification_center/core.py: 9c98149568c1fa7789ee303366a1039abec11f2257f1f18db25fe144e34ab399
- notification_center/http_api.py: f43b0bfd9895e1073e1d5b6ad0efc513f7d5c56245ff0e5b2ecad09ee8198035
- notification_center/incident_card.py: 77e7491e5da5320cd658fe688cad6775b90a7eb3db36bee2b4ff409865633307
- notification_center/incident_progress.py: a65135879b22d8e821285dc2feaa79ba90a9e6fc8531d05048a3ec9b42c89608
- notification_center/agent_job_helper.py: 5729102e6fb1e129b7ec9f8edbd651470b7941913085b2109a24f4b47c976066
- notification_center/gptadmin_agent.py: 5e82957e9b9442ed68c9980ae6f3bc00ba1a1711fa4ea1add763d59b04a68e96
- bin/notify-center: 083ab1218182c38d77a9e2387f0e685159f2f7355e70833dd59d8835c7fcafbb
