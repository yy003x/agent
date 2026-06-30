#!/usr/bin/env python3
"""Thin compatibility wrapper for the content-runtime app."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = ROOT / "apps" / "content-runtime" / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_content_runtime.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
