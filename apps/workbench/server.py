#!/usr/bin/env python3
"""Compatibility launcher for the split FastAPI + React workbench.

The implementation now lives in ``apps/api`` and ``apps/web``. This script is
kept so the old command still starts the new API service during migration.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_DIR = ROOT / ".venv"

try:
    import uvicorn
except ModuleNotFoundError:
    venv_python = VENV_DIR / "bin" / "python"
    if venv_python.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])
    raise


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    host = "127.0.0.1"
    port = 8765
    if argv and argv[0] in {"-h", "--help"}:
        print("Usage: python apps/workbench/server.py [port]")
        print("       python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765")
        return 0
    if argv:
        port = int(argv[0])
    sys.path.insert(0, str(ROOT))
    uvicorn.run("apps.api.main:app", host=host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
