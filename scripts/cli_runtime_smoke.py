#!/usr/bin/env python3
"""Thin entrypoint for validating the AgentRun CLI runtime."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = "看下当前项目实现了什么"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        args = [DEFAULT_PROMPT]
    runtime_smoke = ROOT / "scripts" / "runtime_smoke.py"
    os.execv(sys.executable, [sys.executable, str(runtime_smoke), "cli", *args])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
