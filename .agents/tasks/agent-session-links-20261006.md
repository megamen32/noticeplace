# Agent Herder session links in Notice Place

Status: session links deployed and delivered; full chain verification in progress.

The user receives phone explanations that imply work has started, but cannot
open the agent session from Notice Place. Confirmed gaps:

- Only remediation sessions were announced. Diagnosis and plan-orchestrator
  sessions had no immediate callback.
- Session-link delivery shared the incident's alert queue, so ACK could cancel
  it or prevent it from being claimed after work began.
- Link registration allowed only four harness names although Agent Herder also
  registers Claude, Qoder, Fast Agent and other adapters.
- The phone LLM has no agent-launch tools and must not promise that it started
  work. Its incident context now states that boundary explicitly.

The fix posts each accepted session immediately, shows stage/harness/model and
an open-session button, preserves informational links after ACK/resolution,
and leaves call cancellation and remediation-plan authority intact.

Current runtime audit: recent diagnosis webhook jobs fail without a session
receipt. A bounded live canary must distinguish failed launch from an actual
working session; a queue acknowledgement or link alone is not acceptance.
There are also eight historic `sending` diagnosis rows (two from August and
six from October 2–3), with no matching receipt found in the current Hub state.
They are preserved without relaunch: their exact external outcome is unknown.
Smallest next action for those old rows is to recover their old Hub receipts or
session identities before reconciliation; never redial or relaunch blindly.

Validation artifacts and private configuration remain under ignored `.tmp/`.
The reviewed Notice Place main-service budget remains 512 MiB/1 GiB RAM,
256 MiB swap, two CPUs and 256 tasks. Tests run serially with a timeout; the
live check may launch at most one read-only diagnosis/orchestrator chain.

First live cycle: accepted OpenCode diagnosis `ses_eeffe7b1dffeRNByTAk3Bym1uY`
and its Telegram session card were delivered. The final diagnosis JSON was
valid, but the details response exceeded 64 KiB because of adapter metadata.
The captured failure starts a new fix cycle: only details reads get a measured
1 MiB hard cap; latest-turn requests minimize transcript volume. No Agent
Herder changes: its concurrent dirty work and unpublished commits are preserved.
A second read-only chain is the final consumer canary for this cycle.

The real second chain completed with three plans and Telegram receipts for
its diagnosis/orchestrator cards, including the orchestrator card after ACK.
UI acceptance then exposed two remaining causes: legacy producer URL format
and selection clearing when a finished session is absent from the active list.
Notice now emits canonical encoded keys; Herder commit 0cffab5 preserves linked
selection and accepts legacy aliases. Actual user-reported message5607 maps to
inc_d5a3cd1ee5864096916519c8352bf114 and ses_eeff33290ffelR252khDht84ma;
its exact chat/transcript rendered in the browser after the frontend update.

The user requires a visible copyable URL and web-owned execution settings.
Cards now include URL in text and button. The launch policy contract is
GET/PUT /api/automation/launch-policy, version1, allowedHarnesses, preferredHarness,
models by harness. Notice reads it before each diagnosis/planner creation and
uses models[preferredHarness]. Disabled/missing policy or changed preference
stops the launch; no provider guessing. Continuation is independent.
Current explicit policy: Codex/ZCode, preferred Codex, gpt-5.6-sol / native
ZCode Individual GLM route. Waiting for the owning Herder session's API rollout
and combined build/restart before one final native read-only consumer chain.
