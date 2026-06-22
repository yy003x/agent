#!/usr/bin/env python3
"""Workbench health checks.

Only reports availability and versions. It must not expose tokens, auth state,
or environment variable values.
"""
from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from runtime import MainRuntime

ROOT = Path(__file__).resolve().parents[2]
MAIN_RUNTIME = MainRuntime()


def _run_version(cmd: list[str], timeout: int = 4) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0] if text else f"exit={proc.returncode}"


def _command_check(command: str, label: str, version_args: list[str] | None = None) -> dict:
    path = shutil.which(command)
    if not path:
        return {"id": command, "label": label, "status": "missing", "detail": "not found"}
    detail = path
    if version_args is not None:
        detail = f"{path} | {_run_version([command, *version_args])}"
    return {"id": command, "label": label, "status": "ok", "detail": detail}


def _module_check(module: str, label: str, required: bool = False) -> dict:
    found = importlib.util.find_spec(module) is not None
    return {
        "id": module,
        "label": label,
        "status": "ok" if found else ("missing" if required else "warn"),
        "detail": "installed" if found else "not installed",
    }


def _path_check(path: Path, label: str, required: bool = True) -> dict:
    exists = path.exists()
    return {
        "id": str(path.relative_to(ROOT)),
        "label": label,
        "status": "ok" if exists else ("missing" if required else "warn"),
        "detail": str(path.relative_to(ROOT)) if exists else "not found",
    }


def _safe_runtime_detail(config: dict) -> str:
    payload = dict(config)
    if "extra_args" in payload:
        payload["extra_args"] = "<set>" if payload.get("extra_args") else ""
    return json.dumps(payload, ensure_ascii=False)


def collect_health() -> dict:
    runtime_cfg = MAIN_RUNTIME.effective_runtime_config()
    checks = [
        {
            "id": "python",
            "label": "Python",
            "status": "ok" if sys.version_info >= (3, 11) else "missing",
            "detail": f"{platform.python_version()} ({sys.executable})",
        },
        _command_check("tmux", "tmux", ["-V"]),
        _command_check("codex", "Codex CLI", ["--version"]),
        _command_check("claude", "Claude CLI", ["--version"]),
        {
            "id": "codex-runtime-config",
            "label": "Codex runtime 参数",
            "status": "ok",
            "detail": _safe_runtime_detail(runtime_cfg["codex"]),
        },
        {
            "id": "tmux-submit-config",
            "label": "tmux detector/自动发送",
            "status": "ok",
            "detail": json.dumps(runtime_cfg["tmux_submit"], ensure_ascii=False),
        },
        _command_check("ffmpeg", "ffmpeg", ["-version"]),
        _module_check("lancedb", "LanceDB", required=False),
        _module_check("sentence_transformers", "sentence-transformers", required=False),
        _module_check("jieba", "jieba", required=False),
        _module_check("apscheduler", "APScheduler", required=False),
        _module_check("PIL", "Pillow", required=False),
        _path_check(ROOT / "workspace" / "kb", "workspace/kb"),
        _path_check(ROOT / "outputs", "outputs"),
        _path_check(ROOT / "runs", "runs", required=False),
    ]
    counts = {"ok": 0, "warn": 0, "missing": 0}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    return {"checks": checks, "summary": counts}
