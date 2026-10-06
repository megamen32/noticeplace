# Manual AI: selected checkmark followed by an in-place session URL

User acceptance: press the real AI button through Telegram Helper as Никита
Розанов / Careviolan; first observe the selected checkmark and removed controls,
then the same message gains a copyable canonical session URL and open button.

Root cause: the callback answered only a transient toast and did not preserve
the clicked message identity. Session registration rejected ordinary incidents
even when the operator had explicitly requested diagnosis. Terminal fallback
also lost harness/model provenance without a health execution selection.

Repair: save the clicked card in the durable diagnosis delivery; show the
selected marker before session delivery admission; permit session registration
for operator-requested ordinary incidents; edit that card when an accepted
session arrives. Preserve Telegram entities, URL entity offsets in UTF-16,
ACK-surviving informational delivery, and the URL after plan-card migration.
Repeated clicks cannot schedule a second running diagnosis or replace its link.
Existing producer/health/plan authority is retained.

Scope excludes other chats' Herder edits and runtime consumers/tokens. Preserve
AutoSeller's consumer_e9c5791a0d834f828f743444d3d66286 and its private interest
token during the managed Notice Place upgrade. No direct Telegram sends or
phone calls are part of this acceptance.

Budget: retain deployed Notice Place limits (512 MiB soft / 1 GiB hard RAM,
256 MiB swap, two CPUs, 256 tasks). Serial focused tests use 512 MiB/1 GiB,
128 MiB swap, one CPU, 64 tasks, IOWeight 20, timeout 60 seconds per subset;
fixtures/artifacts remain small (<20 MiB). Live acceptance launches at most
one existing read-only diagnosis/planner chain under Agent Herder's own budget.

Validation: the callback regression failed before the repair. 63 interaction,
manual-card, health, and outcome tests plus 104 worker/adapter/choice/control
tests pass. The real sender tests assert editMessageText to the original ID,
preserved entities, matching text/button URLs, migration retention, terminal
receipt fallback after ACK, and rejection without a manual request.
Managed rollout and Telegram Helper acceptance are pending.

Current deployment dependency: parallel human-stop producer gate is published
in complete source9d5e3d6; its Herder API remains NOT READY.
Its owner explicitly recorded API NOT READY / deployment prohibited. Preserve
that slice and existing live release; coordinate with Codex session
01a10b3f-b648-74d0-8265-023d1ae85312 for scoped publication and API-ready handoff,
then perform one managed Notice Place upgrade and the real manual-click canary.
No separate branch/worktree or dirty-source deployment will bypass that gate.
