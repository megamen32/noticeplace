#!/usr/bin/env python3
"""Canonical AskHuman command; legacy Notify remains an implementation alias."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp import notify_mcp


if __name__ == "__main__":
    notify_mcp.SERVER_NAME = "ask-human"
    notify_mcp.SERVER_VERSION = "1.3.0"
    notify_mcp.main()
