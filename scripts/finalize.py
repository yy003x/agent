#!/usr/bin/env python3
"""Thin wrapper for the agent-memory finalizer app."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "apps" / "agent-memory" / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_memory.finalize import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
