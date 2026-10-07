# Telegram project topics

Operator-created topics in the Notice Place admin console can be keyed by the
exact producer project, for example `agent-herder`. Notifications for that
project then use its dedicated topic for health cards and ordinary messages.
Other projects keep their severity/Health routing. This uses the existing
`telegram_topics_json` runtime setting and does not accept destinations from
producer event payloads.

Active-mode restrictions still apply. A disabled or incomplete project topic
does not fall back to another topic. Existing explicit custom consumer targets
remain authoritative. Telegram topic edits apply live; deploying a code change
uses the normal immutable-release lifecycle.

Create `agent-herder` named `Agent Herder` in the existing Notice Place forum
through the protected admin editor. Leave the thread ID empty only for initial
creation; afterwards reuse the saved ID. Verify delivery with one ordinary
Russian informational event through Agent Herder's existing producer token,
and inspect the durable Telegram receipt for the expected chat/thread.
