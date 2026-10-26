# Telegram project topics

Operator-created topics in the Notice Place admin console can be keyed by the
exact producer project, for example `agent-herder`. Notifications for that
project then use its dedicated topic for health cards and ordinary messages.
Other projects keep their severity/Health routing. This uses the existing
`telegram_topics_json` runtime setting and does not accept destinations from
producer event payloads.

HumanRequest destinations are configured separately in the admin console and
stored in `human_request_telegram_routes_json`; they never write to the event
topic registry. An explicit project destination can select
**chat only** with a numeric `chat_id` and no `message_thread_id`, or a forum
topic with a positive thread ID. It applies only to HumanRequest cards; it does
not alter the project's event route. If no explicit HumanRequest destination
exists, Notice uses the exact project's legacy forum topic when configured.
Invalid or disabled explicit destinations fail closed and do not fall back to
another project or the default chat. Producer requests still cannot choose or
override a destination.

Use the versioned operator command after deploying a release:

```bash
sudo /opt/noticeplace/bin/notice-human-request-route save \
  --project=qa-herder --chat-id=-1001234567890 --chat-only
```

The command validates the project/chat binding through `AdminConfigStore` and
records the change in the existing admin audit. Add `--message-thread-id=123`
instead of `--chat-only` for a forum route; use `--disabled` to fail closed for
one project. `delete --project=qa-herder` removes the explicit override and
restores exact-project legacy topic fallback.

Active-mode restrictions still apply. A disabled or incomplete project topic
does not fall back to another topic. Existing explicit custom consumer targets
remain authoritative. Telegram topic edits apply live; deploying a code change
uses the normal immutable-release lifecycle.

Create `agent-herder` named `Agent Herder` in the existing Notice Place forum
through the protected admin editor. Leave the thread ID empty only for initial
creation; afterwards reuse the saved ID. Verify delivery with one ordinary
Russian informational event through Agent Herder's existing producer token,
and inspect the durable Telegram receipt for the expected chat/thread.
