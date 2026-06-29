"""Shared helpers for workbench API services."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from apps.agentrun import MainRuntime

ROOT = Path(__file__).resolve().parents[3]
SESSIONS_DIR = ROOT / "runs" / "workbench" / "sessions"
CONFIG_PATH = ROOT / "runs" / "workbench" / "config.json"
CONTENT_RUNTIME = ROOT / "skills" / "content-generate" / "scripts" / "content_runtime.py"
MAIN_RUNTIME = MainRuntime()
CHAT_WAIT_SECONDS = float(os.environ.get("AGENT_WORKBENCH_CHAT_WAIT_SECONDS", "120"))
CHAT_RUNTIME = os.environ.get("AGENT_WORKBENCH_CHAT_RUNTIME", MAIN_RUNTIME.default_runtime())
ALLOWED_RUNTIMES = {"api", "cli", "tmux"}
USER_RUNTIMES = {"api", "cli", "tmux"}
NONINTERACTIVE_RUNTIMES = {"api", "cli"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def elapsed_seconds(value: str | None) -> int | None:
    parsed = parse_ts(value)
    if not parsed:
        return None
    return max(0, int((datetime.now(parsed.tzinfo) - parsed).total_seconds()))


def write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def session_dir(session_id: str) -> Path:
    if not re.fullmatch(r"chat-[a-z0-9]{12}", session_id):
        raise ValueError("invalid session id")
    return SESSIONS_DIR / session_id


def valid_runtime(value: str, default: str = "tmux") -> str:
    return value if value in ALLOWED_RUNTIMES else default


def valid_user_runtime(value: str, default: str = "tmux") -> str:
    return value if value in USER_RUNTIMES else default
