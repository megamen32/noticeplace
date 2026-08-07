# Telegram route test drift

Status: todo

Symptom: full Notify pytest has one unrelated failure in `tests/test_telegram_controls.py` because the test expects the legacy `notice` route to override the default while current active-mode routing intentionally falls back to the default for inactive `notice`.

Smallest evidence: `python -m pytest -q` reports 112 passed, 1 failed; the failure is `test_severity_route_overrides_default_chat_and_optionally_sets_topic`.

Blocker: not selected for the runtime-settings task; preserve for a separate routing-policy update.
