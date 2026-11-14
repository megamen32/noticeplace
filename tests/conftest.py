"""Speed-only defaults for the disposable test servers and databases."""

from __future__ import annotations

import os
import socketserver


# The test suite owns these temporary databases.  Keep the production SQLite
# durability default unchanged while removing repeated fixture fsync overhead.
os.environ.setdefault("NOTICEPLACE_TEST_FAST_DB", "1")

_ORIGINAL_SERVE_FOREVER = socketserver.BaseServer.serve_forever


def pytest_sessionstart(session):  # noqa: ARG001 - pytest hook signature
    """Use a short stop poll so HTTP fixture teardown is not 0.5s per test."""

    def fast_serve_forever(server, poll_interval=0.5):
        if poll_interval == 0.5:
            poll_interval = 0.01
        return _ORIGINAL_SERVE_FOREVER(server, poll_interval=poll_interval)

    socketserver.BaseServer.serve_forever = fast_serve_forever


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook signature
    socketserver.BaseServer.serve_forever = _ORIGINAL_SERVE_FOREVER
