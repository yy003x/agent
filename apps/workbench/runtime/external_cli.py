#!/usr/bin/env python3
"""Runtime facade backed by the shared agent runtime."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .shared_runtime import SharedRuntimeAdapter, SharedRuntimeRunSpec, shared_runtime_available
from .state import RuntimeErrorState

ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = ROOT / "runs" / "shared-runtime"
DEFAULT_RUNTIME = os.environ.get("AGENT_WORKBENCH_DEFAULT_RUNTIME", "codex_cli")
INTERACTIVE_RUNTIMES = {"codex_cli", "claude_cli"}
SUPPORTED_RUNTIMES = INTERACTIVE_RUNTIMES | {"fake"}
CODEX_SANDBOX = os.environ.get("AGENT_WORKBENCH_CODEX_SANDBOX", "workspace-write")
CODEX_APPROVAL = os.environ.get("AGENT_WORKBENCH_CODEX_APPROVAL", "never")
CODEX_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CODEX_ARGS", "")
CODEX_BYPASS = os.environ.get("AGENT_WORKBENCH_CODEX_BYPASS", "").lower() in {"1", "true", "yes", "on"}
CODEX_NO_ALT_SCREEN = os.environ.get("AGENT_WORKBENCH_CODEX_NO_ALT_SCREEN", "1").lower() not in {"0", "false", "no", "off"}
CLAUDE_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CLAUDE_ARGS", "")
CLAUDE_PERMISSION_MODE = os.environ.get("AGENT_WORKBENCH_CLAUDE_PERMISSION_MODE", "dontAsk")
CLAUDE_SKIP_PERMISSIONS = os.environ.get("AGENT_WORKBENCH_CLAUDE_SKIP_PERMISSIONS", "").lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _bool_option(options: dict | None, key: str, default: bool) -> bool:
    if not options or key not in options:
        return default
    value = options[key]
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _str_option(options: dict | None, key: str, default: str) -> str:
    if not options or key not in options:
        return default
    return str(options[key])


def default_runtime() -> str:
    return DEFAULT_RUNTIME if DEFAULT_RUNTIME in INTERACTIVE_RUNTIMES else "codex_cli"


def effective_runtime_config(options: dict | None = None) -> dict:
    codex_bypass = _bool_option(options, "codex_bypass", CODEX_BYPASS)
    codex_no_alt_screen = _bool_option(options, "codex_no_alt_screen", CODEX_NO_ALT_SCREEN)
    codex_sandbox = _str_option(options, "codex_sandbox", CODEX_SANDBOX)
    codex_approval = _str_option(options, "codex_approval", CODEX_APPROVAL)
    codex_extra_args = _str_option(options, "codex_extra_args", CODEX_EXTRA_ARGS)
    claude_permission_mode = _str_option(options, "claude_permission_mode", CLAUDE_PERMISSION_MODE)
    claude_skip_permissions = _bool_option(options, "claude_skip_permissions", CLAUDE_SKIP_PERMISSIONS)
    claude_extra_args = _str_option(options, "claude_extra_args", CLAUDE_EXTRA_ARGS)
    codex = {
        "default_runtime": default_runtime(),
        "no_alt_screen": codex_no_alt_screen,
        "cwd": str(ROOT),
        "approval": "bypass" if codex_bypass else codex_approval,
        "sandbox": "disabled" if codex_bypass else codex_sandbox,
        "extra_args": codex_extra_args,
        "extra_args_set": bool(codex_extra_args.strip()),
    }
    claude = {
        "permission_mode": claude_permission_mode,
        "skip_permissions": claude_skip_permissions,
        "extra_args": claude_extra_args,
        "extra_args_set": bool(claude_extra_args.strip()),
    }
    shared = {
        "enabled": True,
        "available": shared_runtime_available(),
        "runs_dir": str(RUNS_DIR),
        "cli": os.environ.get("AGENT_SHARED_RUNTIME_CLI", "/Users/yang/agents/runtime/scripts/agent-runtime"),
    }
    return {
        "codex": codex,
        "claude": claude,
        "tmux_submit": {"owner": "shared-runtime", "result_contract": "result.json"},
        "process": {"enabled": False, "reason": "noninteractive providers hidden during shared runtime migration"},
        "llm_api": {"enabled": False, "reason": "real LLM API is not part of P6 validation"},
        "shared_runtime": shared,
    }


def run_chat_turn(
    session_id: str,
    runtime: str,
    prompt_path: Path,
    result_path: Path,
    work_dir: Path,
    command: str | None = None,
    timeout_seconds: int = 300,
    runtime_options: dict | None = None,
) -> dict:
    if runtime != "fake":
        raise RuntimeErrorState(f"direct turn only supports fake runtime during shared migration: {runtime}")
    runtime_dir = work_dir / "shared"
    return SharedRuntimeAdapter(runtime_dir).run(
        SharedRuntimeRunSpec(
            runtime="fake",
            prompt_text=_prompt_text(prompt_path),
            cwd=ROOT,
            runtime_dir=runtime_dir,
            result_file_name=str(result_path),
            run_id=session_id,
            timeout_seconds=timeout_seconds,
        )
    )


def start_chat_pane(
    session_id: str,
    runtime: str,
    prompt_path: Path,
    result_path: Path,
    work_dir: Path,
    command: str | None = None,
    runtime_options: dict | None = None,
) -> dict:
    if runtime not in INTERACTIVE_RUNTIMES and runtime != "fake":
        raise RuntimeErrorState(f"unsupported shared session runtime: {runtime}")
    runtime_dir = work_dir / "shared"
    return SharedRuntimeAdapter(runtime_dir).start_session(
        runtime=runtime,
        prompt_text=_prompt_text(prompt_path),
        cwd=ROOT,
        runtime_dir=runtime_dir,
        result_file_name=str(result_path),
        run_id=session_id,
        runtime_options=runtime_options,
    )


def send_to_runtime(runtime_meta: dict, text: str, submit: bool = True) -> None:
    if runtime_meta.get("provider_kind") != "shared_runtime_session":
        raise RuntimeErrorState("runtime metadata is not a shared runtime session")
    _adapter_for_meta(runtime_meta).send(runtime_meta["run_id"], text, submit=submit)


def runtime_meta_status(runtime_meta: dict) -> dict:
    if runtime_meta.get("provider_kind") not in {"shared_runtime", "shared_runtime_session"}:
        return {"ok": False, "state": "unknown", "error": "unsupported legacy runtime metadata"}
    return {"ok": True, **_adapter_for_meta(runtime_meta).status(runtime_meta["run_id"])}


def runtime_meta_logs(runtime_meta: dict, max_bytes: int = 40_000) -> dict:
    if runtime_meta.get("provider_kind") not in {"shared_runtime", "shared_runtime_session"}:
        return {"ok": False, "text": "", "error": "unsupported legacy runtime metadata"}
    return {"ok": True, **_adapter_for_meta(runtime_meta).logs(runtime_meta["run_id"], max_bytes=max_bytes)}


def stop_runtime_meta(runtime_meta: dict) -> None:
    if runtime_meta.get("provider_kind") in {"shared_runtime", "shared_runtime_session"}:
        _adapter_for_meta(runtime_meta).stop(runtime_meta["run_id"])


def is_pane_alive(pane_id: str) -> bool:
    if not pane_id or shutil.which("tmux") is None:
        return False
    proc = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"], text=True, capture_output=True, check=False)
    return proc.returncode == 0 and pane_id in proc.stdout.splitlines()


def start_run(
    runtime: str,
    prompt: str,
    command: str | None = None,
    timeout_seconds: int = 1800,
    runtime_options: dict | None = None,
) -> dict:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    if runtime == "fake":
        return SharedRuntimeAdapter(RUNS_DIR).run(
            SharedRuntimeRunSpec(
                runtime="fake",
                prompt_text=prompt,
                cwd=ROOT,
                runtime_dir=RUNS_DIR,
                result_file_name="result.json",
                run_id=run_id,
                timeout_seconds=timeout_seconds,
            )
        )
    if runtime not in INTERACTIVE_RUNTIMES:
        raise RuntimeErrorState(f"unsupported runtime: {runtime}")
    result_path = RUNS_DIR / run_id / "result.json"
    prompt_contract = f"""{prompt.rstrip()}

## 输出要求

请把最终结果写入：
`{result_path}`

写入格式：

```json
{{
  "status": "success|partial|failed",
  "summary": "...",
  "assistant_message": "...",
  "outputs": [],
  "questions": [],
  "errors": []
}}
```
"""
    return SharedRuntimeAdapter(RUNS_DIR).start_session(
        runtime=runtime,
        prompt_text=prompt_contract,
        cwd=ROOT,
        runtime_dir=RUNS_DIR,
        result_file_name="result.json",
        run_id=run_id,
        timeout_seconds=30,
        runtime_options=runtime_options,
    )


def status_run(run_id: str) -> dict:
    return SharedRuntimeAdapter(RUNS_DIR).status(run_id)


def list_runs() -> list[dict]:
    return SharedRuntimeAdapter(RUNS_DIR).list_local_runs()


def logs(run_id: str, max_bytes: int = 120_000) -> dict:
    return SharedRuntimeAdapter(RUNS_DIR).logs(run_id, max_bytes=max_bytes)


def send(run_id: str, text: str) -> dict:
    SharedRuntimeAdapter(RUNS_DIR).send(run_id, text)
    return status_run(run_id)


def stop(run_id: str) -> dict:
    return SharedRuntimeAdapter(RUNS_DIR).stop(run_id)


def _prompt_text(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path.exists() else ""


def _adapter_for_meta(runtime_meta: dict) -> SharedRuntimeAdapter:
    run_dir = Path(runtime_meta.get("run_dir", ""))
    if not run_dir:
        raise RuntimeErrorState("runtime metadata missing run_dir")
    return SharedRuntimeAdapter(run_dir.parent)
