# TODO: stale Telegram notice-route regression

- Symptom: `tests/test_telegram_controls.py::TelegramControlPolicyTests::test_severity_route_overrides_default_chat_and_optionally_sets_topic` expects a `notice` severity route, while the current `telegram_destination` contract maps only `emergency`, `important`, and `log` (other severities fall back to `log`).
- Evidence: `python3 -m pytest -q tests` -> `202 passed, 1 failed`; the failure is isolated to that test and is unrelated to the cancelled Health plan-slot reschedule change.
- Blocker: requires a separate product decision whether `notice` is a supported Telegram mode; not selected for the Health incident task.
