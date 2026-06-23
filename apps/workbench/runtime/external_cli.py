#!/usr/bin/env python3
"""External CLI worker runtime for the local workbench."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .llm_api_provider import LlmApiProvider, LlmApiRunSpec
from .process_cli_provider import ProcessCliProvider, ProcessCliRunSpec, read_json as provider_read_json, write_json as provider_write_json
from .shared_runtime import SharedRuntimeAdapter, SharedRuntimeRunSpec, shared_runtime_available
from .state import RuntimeErrorState
from .tmux_provider import TmuxProvider, TmuxProviderError, TmuxRunSpec

ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = ROOT / "runs" / "tmux"
PROCESS_RUNS_DIR = ROOT / "runs" / "process-runtime"
LLM_API_RUNS_DIR = ROOT / "runs" / "llm-api-runtime"
SHARED_RUNS_DIR = ROOT / "runs" / "shared-runtime"
SESSION_NAME = os.environ.get("AGENT_WORKBENCH_TMUX_SESSION", "book-agent-workbench")
DEFAULT_RUNTIME = os.environ.get("AGENT_WORKBENCH_DEFAULT_RUNTIME", "codex_cli")
INTERACTIVE_RUNTIMES = {"codex_cli", "claude_cli"}
PROCESS_RUNTIMES = {"codex_exec", "claude_print"}
NONINTERACTIVE_RUNTIMES = {"fake", "codex_exec", "claude_print", "llm_api"}
SUPPORTED_RUNTIMES = INTERACTIVE_RUNTIMES | PROCESS_RUNTIMES | {"llm_api", "fake"}
CODEX_SANDBOX = os.environ.get("AGENT_WORKBENCH_CODEX_SANDBOX", "workspace-write")
CODEX_APPROVAL = os.environ.get("AGENT_WORKBENCH_CODEX_APPROVAL", "never")
CODEX_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CODEX_ARGS", "")
CODEX_BYPASS = os.environ.get("AGENT_WORKBENCH_CODEX_BYPASS", "").lower() in {"1", "true", "yes", "on"}
CODEX_NO_ALT_SCREEN = os.environ.get("AGENT_WORKBENCH_CODEX_NO_ALT_SCREEN", "1").lower() not in {"0", "false", "no", "off"}
CLAUDE_EXTRA_ARGS = os.environ.get("AGENT_WORKBENCH_CLAUDE_ARGS", "")
CLAUDE_PERMISSION_MODE = os.environ.get("AGENT_WORKBENCH_CLAUDE_PERMISSION_MODE", "dontAsk")
CLAUDE_SKIP_PERMISSIONS = os.environ.get("AGENT_WORKBENCH_CLAUDE_SKIP_PERMISSIONS", "").lower() in {"1", "true", "yes", "on"}
LLM_API_BACKEND = os.environ.get("AGENT_WORKBENCH_LLM_API_BACKEND", "")
SHARED_RUNTIME = os.environ.get("AGENT_WORKBENCH_SHARED_RUNTIME", "").lower() in {"1", "true", "yes", "on"}
TMUX_STARTUP_DELAY_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_STARTUP_DELAY_S", "1.5"))
TMUX_SUBMIT_DELAY_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_SUBMIT_DELAY_S", "0.15"))
TMUX_SUBMIT_KEY = os.environ.get("AGENT_WORKBENCH_TMUX_SUBMIT_KEY", "C-m")
TMUX_POLL_INTERVAL_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_POLL_INTERVAL_S", "1"))
TMUX_SILENCE_THRESHOLD_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_SILENCE_THRESHOLD_S", "0.6"))
TMUX_PROMPT_IDLE_TIMEOUT_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_PROMPT_IDLE_TIMEOUT_S", "300"))
TMUX_PROMPT_READY_SETTLE_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_PROMPT_READY_SETTLE_S", "2"))
TMUX_PROMPT_READY_SETTLE_FAST_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_PROMPT_READY_SETTLE_FAST_S", "0.5"))
TMUX_PROMPT_STABLE_TIMEOUT_S = float(os.environ.get("AGENT_WORKBENCH_TMUX_PROMPT_STABLE_TIMEOUT_S", "10"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_event(run_dir: Path, event: dict) -> None:
    payload = {"ts": _now(), **event}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _run(cmd: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _run_input(cmd: list[str], text: str, timeout: int = 8) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        input=text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _run_bytes(cmd: list[str], data: bytes, timeout: int = 8) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _ensure_tmux() -> None:
    if not shutil.which("tmux"):
        raise RuntimeErrorState("tmux not found")
    proc = _run(["tmux", "has-session", "-t", SESSION_NAME])
    if proc.returncode != 0:
        proc = _run(["tmux", "new-session", "-d", "-s", SESSION_NAME, "-c", str(ROOT)])
        if proc.returncode != 0:
            raise RuntimeErrorState((proc.stderr or proc.stdout).strip() or "tmux new-session failed")


def _provider(runtime_dir: Path = RUNS_DIR) -> TmuxProvider:
    return TmuxProvider(runtime_dir)


def _provider_error(exc: Exception) -> RuntimeErrorState:
    return RuntimeErrorState(str(exc))


def default_runtime() -> str:
    return DEFAULT_RUNTIME if DEFAULT_RUNTIME in SUPPORTED_RUNTIMES - {"fake"} else "codex_cli"


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
    tmux_submit = {
        "startup_delay_seconds": TMUX_STARTUP_DELAY_S,
        "submit_delay_seconds": TMUX_SUBMIT_DELAY_S,
        "submit_key": TMUX_SUBMIT_KEY,
        "poll_interval_seconds": TMUX_POLL_INTERVAL_S,
        "silence_threshold_seconds": TMUX_SILENCE_THRESHOLD_S,
        "prompt_idle_timeout_seconds": TMUX_PROMPT_IDLE_TIMEOUT_S,
        "prompt_ready_settle_seconds": TMUX_PROMPT_READY_SETTLE_S,
        "prompt_ready_settle_fast_seconds": TMUX_PROMPT_READY_SETTLE_FAST_S,
        "prompt_stable_timeout_seconds": TMUX_PROMPT_STABLE_TIMEOUT_S,
    }
    process = {
        "codex_exec_enabled": True,
        "claude_print_enabled": True,
        "runs_dir": str(PROCESS_RUNS_DIR),
    }
    llm_api = {
        "backend": _str_option(options, "llm_api_backend_id", LLM_API_BACKEND),
        "runs_dir": str(LLM_API_RUNS_DIR),
    }
    shared = {
        "enabled": SHARED_RUNTIME,
        "available": shared_runtime_available(),
        "runs_dir": str(SHARED_RUNS_DIR),
    }
    return {"codex": codex, "claude": claude, "tmux_submit": tmux_submit, "process": process, "llm_api": llm_api, "shared_runtime": shared}


def _quote_args(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _runtime_args(runtime: str, command: str | None, options: dict | None = None) -> list[str]:
    if runtime == "codex_cli":
        cmd = command or "codex"
        if not shutil.which(cmd):
            raise RuntimeErrorState(f"{cmd} not found")
        codex_no_alt_screen = _bool_option(options, "codex_no_alt_screen", CODEX_NO_ALT_SCREEN)
        codex_bypass = _bool_option(options, "codex_bypass", CODEX_BYPASS)
        codex_sandbox = _str_option(options, "codex_sandbox", CODEX_SANDBOX)
        codex_approval = _str_option(options, "codex_approval", CODEX_APPROVAL)
        codex_extra_args = _str_option(options, "codex_extra_args", CODEX_EXTRA_ARGS)
        args = [cmd]
        if codex_no_alt_screen:
            args.append("--no-alt-screen")
        args.extend(["-C", str(ROOT)])
        if codex_bypass:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args.extend(["--sandbox", codex_sandbox, "--ask-for-approval", codex_approval])
        if codex_extra_args.strip():
            args.extend(shlex.split(codex_extra_args))
        return args
    if runtime == "claude_cli":
        cmd = command or "claude"
        if not shutil.which(cmd):
            raise RuntimeErrorState(f"{cmd} not found")
        claude_permission_mode = _str_option(options, "claude_permission_mode", CLAUDE_PERMISSION_MODE)
        claude_skip_permissions = _bool_option(options, "claude_skip_permissions", CLAUDE_SKIP_PERMISSIONS)
        claude_extra_args = _str_option(options, "claude_extra_args", CLAUDE_EXTRA_ARGS)
        args = [cmd, "--add-dir", str(ROOT)]
        if claude_permission_mode.strip():
            args.extend(["--permission-mode", claude_permission_mode])
        if claude_skip_permissions:
            args.append("--dangerously-skip-permissions")
        if claude_extra_args.strip():
            args.extend(shlex.split(claude_extra_args))
        return args
    raise RuntimeErrorState(f"unsupported runtime: {runtime}")


def _runtime_command(runtime: str, command: str | None, prompt_path: Path, result_path: Path,
                     options: dict | None = None) -> str:
    if runtime == "fake":
        summary = "fake runtime ok"
        return (
            "echo '[fake] running'; sleep 1; "
            f"printf '%s\\n' {shlex.quote(json.dumps({'status': 'success', 'summary': summary, 'outputs': [], 'questions': [], 'errors': []}, ensure_ascii=False))} "
            f"> {shlex.quote(str(result_path))}"
        )
    return _quote_args(_runtime_args(runtime, command, options))


def _default_command(runtime: str) -> str:
    if runtime == "codex_cli":
        return "codex"
    if runtime == "claude_cli":
        return "claude"
    if runtime == "codex_exec":
        return "codex"
    if runtime == "claude_print":
        return "claude"
    if runtime == "llm_api":
        return "llm_api"
    return runtime


def _process_args(runtime: str, command: str | None, options: dict | None = None) -> tuple[list[str], str]:
    if runtime == "codex_exec":
        cmd = command or "codex"
        if not shutil.which(cmd):
            raise RuntimeErrorState(f"{cmd} not found")
        codex_bypass = _bool_option(options, "codex_bypass", CODEX_BYPASS)
        codex_sandbox = _str_option(options, "codex_sandbox", CODEX_SANDBOX)
        codex_approval = _str_option(options, "codex_approval", CODEX_APPROVAL)
        codex_extra_args = _str_option(options, "codex_extra_args", CODEX_EXTRA_ARGS)
        args = [cmd, "exec", "--skip-git-repo-check", "--color", "never", "-C", str(ROOT)]
        if codex_bypass:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args.extend(["--sandbox", codex_sandbox, "--ask-for-approval", codex_approval])
        if codex_extra_args.strip():
            args.extend(shlex.split(codex_extra_args))
        args.append("-")
        return args, "text"
    if runtime == "claude_print":
        cmd = command or "claude"
        if not shutil.which(cmd):
            raise RuntimeErrorState(f"{cmd} not found")
        claude_permission_mode = _str_option(options, "claude_permission_mode", CLAUDE_PERMISSION_MODE)
        claude_extra_args = _str_option(options, "claude_extra_args", CLAUDE_EXTRA_ARGS)
        args = [cmd, "-p", "--output-format", "json", "--add-dir", str(ROOT)]
        if claude_permission_mode.strip():
            args.extend(["--permission-mode", claude_permission_mode])
        if claude_extra_args.strip():
            args.extend(shlex.split(claude_extra_args))
        return args, "claude_json"
    raise RuntimeErrorState(f"unsupported process runtime: {runtime}")


def _prompt_text(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path.exists() else ""


def _run_noninteractive(
    *,
    run_id: str,
    runtime: str,
    prompt_text: str,
    result_path: Path,
    runtime_dir: Path,
    command: str | None = None,
    timeout_seconds: int = 300,
    runtime_options: dict | None = None,
) -> dict:
    if runtime == "fake":
        return SharedRuntimeAdapter(runtime_dir).run(
            SharedRuntimeRunSpec(
                runtime="fake",
                prompt_text=prompt_text,
                cwd=ROOT,
                runtime_dir=runtime_dir,
                result_file_name=str(result_path),
                run_id=run_id,
                timeout_seconds=timeout_seconds,
            )
        )
    if runtime in PROCESS_RUNTIMES:
        argv, output_mode = _process_args(runtime, command, runtime_options)
        return ProcessCliProvider(runtime_dir).run(
            ProcessCliRunSpec(
                runtime=runtime,
                argv=argv,
                cwd=ROOT,
                runtime_dir=runtime_dir,
                prompt_text=prompt_text,
                result_file_name=str(result_path),
                run_id=run_id,
                timeout_seconds=timeout_seconds,
                output_mode=output_mode,
            )
        )
    if runtime == "llm_api":
        return LlmApiProvider(runtime_dir).run(
            LlmApiRunSpec(
                prompt_text=prompt_text,
                cwd=ROOT,
                runtime_dir=runtime_dir,
                result_file_name=str(result_path),
                run_id=run_id,
                timeout_seconds=timeout_seconds,
                backend_id=_str_option(runtime_options, "llm_api_backend_id", LLM_API_BACKEND) or None,
            )
        )
    if SHARED_RUNTIME:
        return SharedRuntimeAdapter(runtime_dir).run(
            SharedRuntimeRunSpec(
                runtime=runtime,
                prompt_text=prompt_text,
                cwd=ROOT,
                runtime_dir=runtime_dir,
                result_file_name=str(result_path),
                run_id=run_id,
                timeout_seconds=timeout_seconds,
            )
        )
    raise RuntimeErrorState(f"unsupported noninteractive runtime: {runtime}")


def run_chat_turn(session_id: str, runtime: str, prompt_path: Path, result_path: Path,
                  work_dir: Path, command: str | None = None,
                  timeout_seconds: int = 300,
                  runtime_options: dict | None = None) -> dict:
    prompt_text = _prompt_text(prompt_path)
    runtime_dir = work_dir / "provider"
    run_id = f"{session_id}-{uuid.uuid4().hex[:8]}"
    return _run_noninteractive(
        run_id=run_id,
        runtime=runtime,
        prompt_text=prompt_text,
        result_path=result_path,
        runtime_dir=runtime_dir,
        command=command,
        timeout_seconds=timeout_seconds,
        runtime_options=runtime_options,
    )


def is_pane_alive(pane_id: str) -> bool:
    return _pane_alive(pane_id)


def capture_pane(pane_id: str, max_lines: int = 2000) -> str:
    if not pane_id:
        return ""
    proc = _run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", f"-{max_lines}"], timeout=5)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def wait_for_pane_ready(pane_id: str, delay_seconds: float | None = None) -> None:
    if not pane_id or not _pane_alive(pane_id):
        raise RuntimeErrorState("pane is not running")
    time.sleep(TMUX_STARTUP_DELAY_S if delay_seconds is None else max(0.0, delay_seconds))


def send_text_to_pane(pane_id: str, text: str, submit: bool = True,
                      submit_key: str | None = None) -> None:
    """Paste UTF-8 bytes through a tmux buffer, then optionally submit.

    This avoids shell quoting and `tmux send-keys <text>` transformations, so the
    payload content is preserved exactly when it reaches the terminal input.
    """
    if not pane_id or not _pane_alive(pane_id):
        raise RuntimeErrorState("pane is not running")
    buffer_name = f"workbench-{uuid.uuid4().hex[:8]}"
    proc = _run_bytes(["tmux", "load-buffer", "-b", buffer_name, "-"], text.encode("utf-8"), timeout=5)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeErrorState(detail or "tmux load-buffer failed")
    proc = _run(["tmux", "paste-buffer", "-d", "-b", buffer_name, "-t", pane_id], timeout=5)
    _run(["tmux", "delete-buffer", "-b", buffer_name], timeout=5)
    if proc.returncode != 0:
        raise RuntimeErrorState((proc.stderr or proc.stdout).strip() or "tmux paste-buffer failed")
    if not submit:
        return
    time.sleep(TMUX_SUBMIT_DELAY_S)
    proc = _run(["tmux", "send-keys", "-t", pane_id, submit_key or TMUX_SUBMIT_KEY], timeout=5)
    if proc.returncode != 0:
        raise RuntimeErrorState((proc.stderr or proc.stdout).strip() or "tmux send-keys failed")


def send_to_runtime(runtime_meta: dict, text: str, submit: bool = True) -> None:
    provider_runtime_dir = runtime_meta.get("provider_runtime_dir")
    provider_run_id = runtime_meta.get("provider_run_id")
    if provider_runtime_dir and provider_run_id:
        try:
            _provider(Path(provider_runtime_dir)).send(provider_run_id, text, enter=submit)
            return
        except TmuxProviderError as exc:
            raise _provider_error(exc) from exc
    send_text_to_pane(runtime_meta.get("pane_id", ""), text, submit=submit)


def runtime_meta_status(runtime_meta: dict) -> dict:
    direct_kind = runtime_meta.get("provider_kind")
    direct_run_id = runtime_meta.get("run_id")
    direct_run_dir = runtime_meta.get("run_dir")
    if direct_kind and direct_run_id and direct_run_dir:
        runtime_dir = Path(direct_run_dir).parent
        if direct_kind == "process_cli":
            return {"ok": True, "kind": direct_kind, **ProcessCliProvider(runtime_dir).status(direct_run_id)}
        if direct_kind == "llm_api":
            return {"ok": True, "kind": direct_kind, **LlmApiProvider(runtime_dir).status(direct_run_id)}
        if direct_kind == "shared_runtime":
            return {"ok": True, "kind": direct_kind, **SharedRuntimeAdapter(runtime_dir).status(direct_run_id)}

    provider_runtime_dir = runtime_meta.get("provider_runtime_dir")
    provider_run_id = runtime_meta.get("provider_run_id")
    if provider_runtime_dir and provider_run_id:
        try:
            status = _provider(Path(provider_runtime_dir)).status(provider_run_id)
            return {"ok": True, "kind": "provider", **status}
        except TmuxProviderError as exc:
            return {"ok": False, "kind": "provider", "error": str(exc)}

    pane_id = runtime_meta.get("pane_id", "")
    return {
        "ok": bool(pane_id and _pane_alive(pane_id)),
        "kind": "pane",
        "pane_id": pane_id,
        "runtime": runtime_meta.get("runtime", "unknown"),
        "output_log": runtime_meta.get("output_path"),
    }


def runtime_meta_logs(runtime_meta: dict, max_bytes: int = 40_000) -> dict:
    direct_kind = runtime_meta.get("provider_kind")
    direct_run_id = runtime_meta.get("run_id")
    direct_run_dir = runtime_meta.get("run_dir")
    if direct_kind and direct_run_id and direct_run_dir:
        runtime_dir = Path(direct_run_dir).parent
        if direct_kind == "process_cli":
            return {"ok": True, "kind": direct_kind, **ProcessCliProvider(runtime_dir).logs(direct_run_id, max_bytes=max_bytes)}
        if direct_kind == "llm_api":
            return {"ok": True, "kind": direct_kind, **LlmApiProvider(runtime_dir).logs(direct_run_id, max_bytes=max_bytes)}
        if direct_kind == "shared_runtime":
            return {"ok": True, "kind": direct_kind, **SharedRuntimeAdapter(runtime_dir).logs(direct_run_id, max_bytes=max_bytes)}

    provider_runtime_dir = runtime_meta.get("provider_runtime_dir")
    provider_run_id = runtime_meta.get("provider_run_id")
    if provider_runtime_dir and provider_run_id:
        try:
            text = _provider(Path(provider_runtime_dir)).logs(provider_run_id, max_bytes=max_bytes)
            return {"ok": True, "kind": "provider", "text": text, "truncated": len(text.encode("utf-8")) >= max_bytes}
        except TmuxProviderError as exc:
            return {"ok": False, "kind": "provider", "text": "", "error": str(exc)}

    output_path = runtime_meta.get("output_path")
    if output_path and Path(output_path).exists():
        data = Path(output_path).read_bytes()
        truncated = len(data) > max_bytes
        if truncated:
            data = data[-max_bytes:]
        return {"ok": True, "kind": "file", "text": data.decode("utf-8", errors="replace"), "truncated": truncated}
    return {"ok": False, "kind": "pane", "text": capture_pane(runtime_meta.get("pane_id", ""), max_lines=600)}


def stop_runtime_meta(runtime_meta: dict) -> None:
    direct_kind = runtime_meta.get("provider_kind")
    direct_run_dir = runtime_meta.get("run_dir")
    if direct_kind and direct_run_dir:
        status_path = Path(direct_run_dir) / "status.json"
        status = provider_read_json(status_path, {}) or {}
        provider_write_json(status_path, {**status, "state": "stopped", "updated_at": _now()})
        return

    provider_runtime_dir = runtime_meta.get("provider_runtime_dir")
    provider_run_id = runtime_meta.get("provider_run_id")
    if provider_runtime_dir and provider_run_id:
        try:
            _provider(Path(provider_runtime_dir)).stop(provider_run_id, reason="session_deleted")
            return
        except TmuxProviderError:
            pass

    pane_id = runtime_meta.get("pane_id", "")
    if pane_id and _pane_alive(pane_id):
        proc = _run(["tmux", "kill-pane", "-t", pane_id], timeout=5)
        if proc.returncode != 0:
            raise RuntimeErrorState((proc.stderr or proc.stdout).strip() or "tmux kill-pane failed")


def start_chat_pane(session_id: str, runtime: str, prompt_path: Path, result_path: Path,
                    work_dir: Path, command: str | None = None,
                    runtime_options: dict | None = None) -> dict:
    """Start a persistent interactive CLI pane for one workbench chat session."""
    _ensure_tmux()
    provider_runtime_dir = work_dir / "provider"
    run_id = f"{session_id}-{uuid.uuid4().hex[:8]}"
    try:
        handle = _provider(provider_runtime_dir).start(
            TmuxRunSpec(
                command=_runtime_args(runtime, command, runtime_options) if runtime != "fake" else [
                    "sh",
                    "-c",
                    "sleep 1; printf '%s\\n' '{\"status\":\"success\",\"summary\":\"fake runtime ok\",\"outputs\":[],\"questions\":[],\"errors\":[]}' > \"$AGENT_WORKBENCH_RESULT_FILE\"",
                ],
                cwd=ROOT,
                runtime_dir=provider_runtime_dir,
                run_id=run_id,
                tmux_session_name=SESSION_NAME,
                prompt_text=prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path.exists() else "",
                prompt_delivery="paste" if runtime != "fake" else "manual",
                result_file_name=str(result_path),
                env={
                    "AGENT_WORKBENCH_CHAT_SESSION": session_id,
                    "AGENT_WORKBENCH_RESULT_FILE": str(result_path),
                },
                window_prefix="book-chat",
                startup_delay_seconds=TMUX_STARTUP_DELAY_S,
                poll_interval_seconds=TMUX_POLL_INTERVAL_S,
                silence_threshold_seconds=TMUX_SILENCE_THRESHOLD_S,
                prompt_idle_timeout_seconds=TMUX_PROMPT_IDLE_TIMEOUT_S,
                prompt_ready_settle_seconds=TMUX_PROMPT_READY_SETTLE_S,
                prompt_ready_settle_fast_seconds=TMUX_PROMPT_READY_SETTLE_FAST_S,
                prompt_stable_timeout_seconds=TMUX_PROMPT_STABLE_TIMEOUT_S,
                submit_delay_seconds=TMUX_SUBMIT_DELAY_S,
                submit_key=TMUX_SUBMIT_KEY,
            )
        )
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    meta = {
        "session_id": session_id,
        "runtime": runtime,
        "command": command or _default_command(runtime),
        "argv": _runtime_args(runtime, command, runtime_options) if runtime != "fake" else [command or "fake"],
        "runtime_options": runtime_options or {},
        "cwd": str(ROOT),
        "tmux_session": handle.tmux_session,
        "tmux_window": handle.tmux_window,
        "tmux_window_name": handle.tmux_window_name,
        "pane_id": handle.tmux_pane,
        "provider_run_id": handle.run_id,
        "provider_runtime_dir": str(provider_runtime_dir),
        "provider_run_dir": str(handle.run_dir),
        "prompt_path": str(handle.prompt_file),
        "startup_contract_path": str(handle.prompt_file),
        "startup_contract_version": 1,
        "result_path": str(result_path),
        "output_path": str(handle.output_log),
        "command_path": str(handle.command_file),
        "created_at": _now(),
    }
    return meta


def start_run(runtime: str, prompt: str, command: str | None = None,
              timeout_seconds: int = 1800, runtime_options: dict | None = None) -> dict:
    if runtime in NONINTERACTIVE_RUNTIMES or (runtime not in INTERACTIVE_RUNTIMES and SHARED_RUNTIME):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        result_path = RUNS_DIR / run_id / "result.json"
        return _run_noninteractive(
            run_id=run_id,
            runtime=runtime,
            prompt_text=prompt,
            result_path=result_path,
            runtime_dir=RUNS_DIR,
            command=command,
            timeout_seconds=timeout_seconds,
            runtime_options=runtime_options,
        )
    _ensure_tmux()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
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
  "outputs": [],
  "questions": [],
  "errors": []
}}
```
"""
    if runtime == "fake":
        cmd = ["sh", "-c", "echo '[fake] running'; sleep 1; printf '%s\\n' '{\"status\":\"success\",\"summary\":\"fake runtime ok\",\"outputs\":[],\"questions\":[],\"errors\":[]}' > \"$AGENT_WORKBENCH_RESULT_FILE\""]
        delivery = "manual"
    else:
        cmd = _runtime_args(runtime, command, runtime_options)
        delivery = "paste"
    try:
        handle = _provider(RUNS_DIR).start(
            TmuxRunSpec(
                command=cmd,
                cwd=ROOT,
                runtime_dir=RUNS_DIR,
                run_id=run_id,
                tmux_session_name=SESSION_NAME,
                prompt_text=prompt_contract,
                prompt_delivery=delivery,
                result_file_name="result.json",
                env={"AGENT_WORKBENCH_RUNTIME": runtime},
                window_prefix="book-run",
                timeout_seconds=timeout_seconds,
                startup_delay_seconds=TMUX_STARTUP_DELAY_S,
                poll_interval_seconds=TMUX_POLL_INTERVAL_S,
                silence_threshold_seconds=TMUX_SILENCE_THRESHOLD_S,
                prompt_idle_timeout_seconds=TMUX_PROMPT_IDLE_TIMEOUT_S,
                prompt_ready_settle_seconds=TMUX_PROMPT_READY_SETTLE_S,
                prompt_ready_settle_fast_seconds=TMUX_PROMPT_READY_SETTLE_FAST_S,
                prompt_stable_timeout_seconds=TMUX_PROMPT_STABLE_TIMEOUT_S,
                submit_delay_seconds=TMUX_SUBMIT_DELAY_S,
                submit_key=TMUX_SUBMIT_KEY,
            )
        )
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    _append_event(handle.run_dir, {"type": "runtime.started", "runtime": runtime, "pane_id": handle.tmux_pane})
    return status_run(run_id)


def _pane_alive(pane_id: str) -> bool:
    proc = _run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"], timeout=5)
    if proc.returncode != 0:
        return False
    return pane_id in proc.stdout.splitlines()


def status_run(run_id: str) -> dict:
    meta = provider_read_json(RUNS_DIR / run_id / "meta.json", {}) or {}
    kind = meta.get("provider_kind")
    if kind == "process_cli":
        return ProcessCliProvider(RUNS_DIR).status(run_id)
    if kind == "llm_api":
        return LlmApiProvider(RUNS_DIR).status(run_id)
    if kind == "shared_runtime":
        return SharedRuntimeAdapter(RUNS_DIR).status(run_id)

    try:
        status = _provider(RUNS_DIR).status(run_id)
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    result_path = Path(status["result_file"])
    result = None
    result_valid = False
    if result_path.exists():
        try:
            result = _read_json(result_path)
            result_valid = True
        except Exception as exc:  # noqa: BLE001
            result = {"status": "failed", "summary": f"result.json parse failed: {exc}", "errors": [str(exc)]}
    return {
        "run_id": run_id,
        "state": "done" if result_valid else status.get("state", "unknown"),
        "runtime": _runtime_from_status(status),
        "command": status.get("command"),
        "pane_id": status.get("tmux_pane"),
        "result_exists": result_path.exists(),
        "result_valid": result_valid,
        "result": result,
        "output_bytes": status.get("output_bytes", 0),
        "updated_at": _now(),
        "provider_status": status,
    }


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for path in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_dir() and (path / "meta.json").exists():
            try:
                runs.append(status_run(path.name))
            except Exception as exc:  # noqa: BLE001
                runs.append({"run_id": path.name, "state": "failed", "error": str(exc)})
    return runs


def logs(run_id: str, max_bytes: int = 120_000) -> dict:
    meta = provider_read_json(RUNS_DIR / run_id / "meta.json", {}) or {}
    kind = meta.get("provider_kind")
    if kind == "process_cli":
        return ProcessCliProvider(RUNS_DIR).logs(run_id, max_bytes=max_bytes)
    if kind == "llm_api":
        return LlmApiProvider(RUNS_DIR).logs(run_id, max_bytes=max_bytes)
    if kind == "shared_runtime":
        return SharedRuntimeAdapter(RUNS_DIR).logs(run_id, max_bytes=max_bytes)

    try:
        text = _provider(RUNS_DIR).logs(run_id, max_bytes=max_bytes)
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    return {"run_id": run_id, "text": text, "truncated": len(text.encode("utf-8")) >= max_bytes}


def send(run_id: str, text: str) -> dict:
    try:
        _provider(RUNS_DIR).send(run_id, text)
        meta = _provider(RUNS_DIR)._load_meta(run_id)
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    _append_event(Path(meta["run_dir"]), {"type": "runtime.send", "text": text})
    return status_run(run_id)


def stop(run_id: str) -> dict:
    meta_path = RUNS_DIR / run_id / "meta.json"
    meta = provider_read_json(meta_path, {}) or {}
    if meta.get("provider_kind") in {"process_cli", "llm_api", "shared_runtime"}:
        status_path = RUNS_DIR / run_id / "status.json"
        status = provider_read_json(status_path, {}) or meta
        provider_write_json(status_path, {**status, "state": "stopped", "updated_at": _now()})
        return status_run(run_id)

    try:
        meta = _provider(RUNS_DIR)._load_meta(run_id)
        _provider(RUNS_DIR).stop(run_id)
    except TmuxProviderError as exc:
        raise _provider_error(exc) from exc
    _append_event(Path(meta["run_dir"]), {"type": "runtime.stopped"})
    return status_run(run_id)


def _runtime_from_status(status: dict) -> str:
    argv = status.get("argv") or []
    command = Path(argv[0]).name if argv else status.get("command", "")
    if command == "codex":
        return "codex_cli"
    if command == "claude":
        return "claude_cli"
    if command in {"sh", "bash"}:
        return "fake"
    return command or "unknown"
