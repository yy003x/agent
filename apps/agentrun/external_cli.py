#!/usr/bin/env python3
"""Runtime gateway backed by the in-repo AgentRun package."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .adapter import LOCAL_RUNTIME_ROOT, AgentRunAdapter, AgentRunSpec, agentrun_available
from .state import RuntimeErrorState

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs" / "agentrun"
DEFAULT_RUNTIME = os.environ.get("AGENT_WORKBENCH_DEFAULT_RUNTIME", "tmux")
TASK_RUNTIMES = {"cli", "api", "tmux"}
DIRECT_CHAT_RUNTIMES = {"cli", "api"}
INTERACTIVE_RUNTIMES = {"tmux"}
SUPPORTED_RUNTIMES = TASK_RUNTIMES | INTERACTIVE_RUNTIMES
CLI_PROFILE = os.environ.get("AGENT_WORKBENCH_CLI_PROFILE", "codex-cli")
API_PROFILE = os.environ.get("AGENT_WORKBENCH_API_PROFILE", "api-openai-gpt-4o-mini")
TMUX_PROFILE = os.environ.get("AGENT_WORKBENCH_TMUX_PROFILE", "tmux-codex")
CODEX_SANDBOX = os.environ.get("AGENT_WORKBENCH_CODEX_SANDBOX", "workspace-write")
CODEX_APPROVAL = os.environ.get("AGENT_WORKBENCH_CODEX_APPROVAL", "never")
CODEX_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CODEX_ARGS", "")
CODEX_BYPASS = os.environ.get("AGENT_WORKBENCH_CODEX_BYPASS", "").lower() in {"1", "true", "yes", "on"}
CODEX_NO_ALT_SCREEN = os.environ.get("AGENT_WORKBENCH_CODEX_NO_ALT_SCREEN", "1").lower() not in {"0", "false", "no", "off"}
CLAUDE_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CLAUDE_ARGS", "")
CLAUDE_PERMISSION_MODE = os.environ.get("AGENT_WORKBENCH_CLAUDE_PERMISSION_MODE", "dontAsk")
CLAUDE_SKIP_PERMISSIONS = os.environ.get("AGENT_WORKBENCH_CLAUDE_SKIP_PERMISSIONS", "").lower() in {"1", "true", "yes", "on"}
PROVIDER_TASK_KINDS = {"agentrun"}
PROVIDER_SESSION_KINDS = {"agentrun_session"}
PROVIDER_KINDS = PROVIDER_TASK_KINDS | PROVIDER_SESSION_KINDS


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
    return DEFAULT_RUNTIME if DEFAULT_RUNTIME in SUPPORTED_RUNTIMES else "tmux"


def effective_runtime_config(options: dict | None = None) -> dict:
    codex_bypass = _bool_option(options, "codex_bypass", CODEX_BYPASS)
    codex_no_alt_screen = _bool_option(options, "codex_no_alt_screen", CODEX_NO_ALT_SCREEN)
    codex_sandbox = _str_option(options, "codex_sandbox", CODEX_SANDBOX)
    codex_approval = _str_option(options, "codex_approval", CODEX_APPROVAL)
    codex_extra_args = _str_option(options, "codex_extra_args", CODEX_EXTRA_ARGS)
    cli_profile = _profile_option(options, "cli", purpose="runtime")
    api_profile = _profile_option(options, "api", purpose="runtime")
    tmux_profile = _profile_option(options, "tmux", purpose="chat")
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
    agentrun = {
        "enabled": True,
        "available": agentrun_available(),
        "runs_dir": str(RUNS_DIR),
        "root": str(LOCAL_RUNTIME_ROOT),
        "cli": "python -m agentrun.cli.main",
    }
    return {
        "codex": codex,
        "claude": claude,
        "provider_profiles": {
            "cli": {"transport": "cli", "profile": cli_profile},
            "api": {"transport": "api", "profile": api_profile},
            "tmux": {"transport": "tmux", "profile": tmux_profile},
        },
        "tmux_submit": {"owner": "agentrun", "profile": tmux_profile, "result_contract": "result.json"},
        "process": {"enabled": True, "owner": "agentrun task provider"},
        "api": {"enabled": True, "profile": api_profile},
        "agentrun": agentrun,
    }


def runtime_choices(*, only_valid: bool = True) -> dict:
    return AgentRunAdapter(RUNS_DIR).config_choices(only_valid=only_valid)


def validate_config(provider_type: str | None = None, name: str | None = None, profile_id: str | None = None) -> dict:
    return AgentRunAdapter(RUNS_DIR).validate_config(
        provider_type=provider_type,
        name=name,
        profile_id=profile_id,
    )


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
    if runtime not in DIRECT_CHAT_RUNTIMES:
        raise RuntimeErrorState(f"direct turn only supports task runtime: {runtime}")
    runtime_dir = work_dir / "agentrun"
    return AgentRunAdapter(runtime_dir).run(
        AgentRunSpec(
            runtime=runtime,
            prompt_text=_task_prompt(_prompt_text(prompt_path)),
            cwd=ROOT,
            runtime_dir=runtime_dir,
            result_file_name=str(result_path),
            run_id=session_id,
            timeout_seconds=timeout_seconds,
            provider_profile=_profile_for_runtime(runtime, runtime_options, purpose="chat"),
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
    if runtime not in INTERACTIVE_RUNTIMES:
        raise RuntimeErrorState(f"unsupported AgentRun session runtime: {runtime}")
    runtime_dir = work_dir / "agentrun"
    return AgentRunAdapter(runtime_dir).start_session(
        runtime=runtime,
        prompt_text=_prompt_text(prompt_path),
        cwd=ROOT,
        runtime_dir=runtime_dir,
        result_file_name=str(result_path),
        run_id=session_id,
        runtime_options=runtime_options,
        provider_profile=_profile_for_runtime(runtime, runtime_options, purpose="chat"),
    )


def send_to_runtime(runtime_meta: dict, text: str, submit: bool = True) -> None:
    if runtime_meta.get("provider_kind") not in PROVIDER_SESSION_KINDS:
        raise RuntimeErrorState("runtime metadata is not an AgentRun session")
    _adapter_for_meta(runtime_meta).send(runtime_meta["run_id"], text, submit=submit)


def runtime_meta_status(runtime_meta: dict) -> dict:
    if runtime_meta.get("provider_kind") not in PROVIDER_KINDS:
        return {"ok": False, "state": "unknown", "error": "unsupported runtime metadata"}
    return {"ok": True, **_adapter_for_meta(runtime_meta).status(runtime_meta["run_id"])}


def runtime_meta_logs(runtime_meta: dict, max_bytes: int = 40_000) -> dict:
    if runtime_meta.get("provider_kind") not in PROVIDER_KINDS:
        return {"ok": False, "text": "", "error": "unsupported runtime metadata"}
    return {"ok": True, **_adapter_for_meta(runtime_meta).logs(runtime_meta["run_id"], max_bytes=max_bytes)}


def stop_runtime_meta(runtime_meta: dict) -> None:
    if runtime_meta.get("provider_kind") in PROVIDER_KINDS:
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
    if runtime in TASK_RUNTIMES:
        return AgentRunAdapter(RUNS_DIR).run(
            AgentRunSpec(
                runtime=runtime,
                prompt_text=_task_prompt(prompt),
                cwd=ROOT,
                runtime_dir=RUNS_DIR,
                result_file_name="result.json",
                run_id=run_id,
                timeout_seconds=timeout_seconds,
                provider_profile=_profile_for_runtime(runtime, runtime_options, purpose="runtime"),
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
    return AgentRunAdapter(RUNS_DIR).start_session(
        runtime=runtime,
        prompt_text=prompt_contract,
        cwd=ROOT,
        runtime_dir=RUNS_DIR,
        result_file_name="result.json",
        run_id=run_id,
        timeout_seconds=30,
        runtime_options=runtime_options,
        provider_profile=_profile_for_runtime(runtime, runtime_options, purpose="runtime"),
    )


def status_run(run_id: str) -> dict:
    return AgentRunAdapter(RUNS_DIR).status(run_id)


def list_runs() -> list[dict]:
    return AgentRunAdapter(RUNS_DIR).list_local_runs()


def logs(run_id: str, max_bytes: int = 120_000) -> dict:
    return AgentRunAdapter(RUNS_DIR).logs(run_id, max_bytes=max_bytes)


def send(run_id: str, text: str) -> dict:
    AgentRunAdapter(RUNS_DIR).send(run_id, text)
    return status_run(run_id)


def stop(run_id: str) -> dict:
    return AgentRunAdapter(RUNS_DIR).stop(run_id)


def _prompt_text(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path.exists() else ""


def _task_prompt(prompt: str) -> str:
    return f"""{prompt.rstrip()}

## AgentRun result 契约

如果当前 provider 要求写文件，请把最终结果写入环境变量 `AGENTRUN_RESULT_FILE` 指向的 JSON 文件。
格式如下：

```json
{{
  "schema_version": 1,
  "run_id": "从环境变量 AGENTRUN_RUN_ID 读取",
  "outcome": "succeeded|failed|blocked|partial|cancelled",
  "summary": "给用户看的中文回复或任务摘要",
  "artifacts": [],
  "errors": [],
  "validation": {{"commands": [], "passed": true}}
}}
```
"""


def _profile_option(options: dict | None = None, runtime: str = "cli", *, purpose: str = "runtime") -> str:
    key = "chat_profile" if purpose == "chat" else "runtime_profile"
    value = _str_option(options, key, "").strip()
    if value:
        return value
    defaults = {"cli": CLI_PROFILE, "api": API_PROFILE, "tmux": TMUX_PROFILE}
    return defaults.get(runtime, "")


def _profile_for_runtime(runtime: str, options: dict | None = None, *, purpose: str = "runtime") -> str | None:
    profile = _profile_option(options, runtime, purpose=purpose)
    return profile or None


def _adapter_for_meta(runtime_meta: dict) -> AgentRunAdapter:
    run_dir = Path(runtime_meta.get("run_dir", ""))
    if not run_dir:
        raise RuntimeErrorState("runtime metadata missing run_dir")
    return AgentRunAdapter(run_dir.parent)
