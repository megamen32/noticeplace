# Agent Herder session links in Notice Place

Status: delivered and accepted through the native consumer and browser paths.

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

Final acceptance: incident inc_ee4ade42399b4a39bf91c1f7c8673512, Hub
72c67c6642dfd803a059b8ffb64522cd completed, three plans. Diagnosis
01a1103b-3efd-71f3-91cc-61621bd2565a and planner
01a1103b-803f-7d60-9748-bff6fe3cdc62 both actually ran Codex/gpt-5.6-sol.
Telegram receipts5638/5639 were read through the approved secondary account
Careviolan; both message bodies contain copyable canonical URLs, and the
planner button has the same URL. The primary Noonenowhereneverever could not
resolve this private group's entity even after a bounded group-list read;
the original active primary account was restored. No account binding, login,
join, message send or phone call was performed through Telegram Helper.

The native chain completed before ACK, so its two links were delivered before
ACK. The earlier real incident inc_f50168a90c4a44759503ee99cd15d027/message5604
separately proves delivery after ACK. Do not misreport native timing.

Web acceptance after final Herder code9a7299a: exact planner fragment, matching
health_orchestrator_100_38fcda58a68b title, Codex/gpt-5.6-sol, idle state and
actual plan JSON rendered. Policy form showed only Codex/ZCode checked, Codex
preferred and exact models. Saving unchanged returned 'Настройки сохранены'
and GET returned the identical policy. Screenshots and metadata-only proof:
.tmp/session-links-20261006/native-planner-exact-chat.png,
launch-policy-saved-controls.png and native-proof.json. Browser closed.

Optional S21 receiver UI probe found no reverse ADB listener at22221 and only
a Linux agent-device target. Guarded bootstrap returned Connection refused;
no phone session/app/runtime was changed. Lease acquired and released. The
user explicitly redirected verification to both Telegram Helper accounts and
confirmed phone proof is unnecessary; receiver verification succeeded there.

Source checks passed: 111 related tests before final small changes; 64 final
helper/health tests, plus the focused clickable-card check. Runtime cf476f4
matched exact managed source bytes before the one native canary. Final changes
are evidence-only; no further rebuild or restart is required. Historic eight
unknown-outcome deliveries remain recorded above, with no blind relaunch.
