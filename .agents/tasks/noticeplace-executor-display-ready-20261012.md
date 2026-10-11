# Actual executor identity source ready — 2026-10-12

Owner: NoticePlace Codex 01a12c47-a796-7d52-b59c-ad551e0a8752.
Owned branch: codex/noticeplace-repair-first; base installed2c54bd8.
Root owns integration/install/Telegram acceptance. Canonical ZCode WIP untouched.

## Confirmed defect and result

Generic producer appended actual execution identity at the end of a long summary.
The 800-character card body dropped it; project=hermes appeared as executor and the
native URL/button was absent. Producer/core now share optional typed
execution_identity, persisted in existing events.payload_json and projected apart
from body. Metadata only displays known harness/actual model/CWD and exact native
URL; it creates no native binding, readback, job or launch/resume authority. Real
bound native receipts/progress have priority. Technical project is separate;
legacy metadata-free cards remain honest unknown/no link, even if prose names an
executor. Event auth/forbidden authority fields stay unchanged. No plugin/schema
version change/new topic/direct Telegram/new live incident/history replay.

The exact optional field contract was sent once to Herder's current native turn.
Herder owns the producer; Notice owns validator/projector/renderer and incoming
consumer regression. Root's installed2c54 and old historical cards remain untouched
by this source executor. No claim of live Telegram acceptance or producer runtime
installation is made here.

## Finite proof

Meaningful RED: actual HTTP->DB->card worker lost executor/link on long generic
summary; legacy inferred context and invalid metadata rejection also failed.
The initial bound-receipt fixture used a non-health event and was corrected to
exercise the existing supported health admission path, rather than relax validation.

GREEN: 80 focused cases in47.68s, combined setup/tests/plugin/compile/diff49.5s.
Final encoding delta: 5/5 cases in3.01s, setup/compile/diff4.595s. JavaScript
encodeURIComponent punctuation matches the producer contract; optional absent URL
never creates a guessed link. Combined executable checks54.095s (<180s), 2GiB/two
CPU slots; release peak RSS69,724KiB. Immutable plugin0.1.0 validation PASS.
Logs/summaries kept in ignored .tmp/execution-identity-{red,green,release,delta}.log
and release/delta-summary.json. No broad/nightly retry.

Fast unit validation/render: typed shape, exact URL, bounds, unknown/legacy,
authority and native receipt priority; expected<1s/max3s. Focused integration:
real incoming HTTP/project auth + DB reopen + real delivery worker/fake Telegram
transport, one message42 followed by edit42; expected2s/max8s. Release deadline180s.

## Frozen source SHA256

- notification_center/core.py: f3b28e320a728a4381ef32b00a1827aeb3ed9c597c6982ddfcaaeec0cdeee471
- notification_center/incident_card.py: 98abeb5506034f0f9725b6708862c5b0dab4fb3e323c032c6966f7c07308aa86
- tests/test_execution_identity.py: 0d15f1c255aea73c0bc5d1ce4c8f441f566f1fc7ccefb89d5c7398ee0d8501e3
