"""cli provider:结构化 exec + stdin,读 result.json(见 design/03 §1)。

不从 terminal 文本判断成功;退出后只认 result.json。超时归 failed + failure_reason: timeout。
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from agentrun.core.config import Profile
from agentrun.core.contract import event, mark_done_if_valid, read_result, write_result, write_status
from agentrun.core.rundir import RunPaths
from agentrun.core.run import CANCELLED, RUNNING, RunRequest


class CodeCliProvider:
    transport = "cli"

    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def run(self, request: RunRequest, paths: RunPaths) -> dict[str, Any]:
        if not self.profile.binary:
            raise ValueError(f"profile {self.profile.id} 缺少 binary")
        binary = _expand(self.profile.binary)
        argv = [binary, *self.profile.default_args]
        timeout = self.profile.timeout_seconds or None
        prompt = ""
        if request.prompt_file and request.prompt_file.exists():
            prompt = request.prompt_file.read_text(encoding="utf-8")
        env = _runtime_env(request, paths, self.profile.raw)

        provider_status = {
            "argv": argv,
            "cwd": str(request.cwd) if request.cwd else None,
            "env_allowlist": self.profile.raw.get("env_allowlist") or [],
        }

        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(request.cwd) if request.cwd else None,
                env=env,
                start_new_session=True,
            )
            provider_status["pid"] = proc.pid
            provider_status["pgid"] = os.getpgid(proc.pid)
            write_status(paths, request, RUNNING, provider_status=provider_status, message="cli running")
            event(paths, request, "status.changed", {"state": RUNNING, "transport": self.transport, "pid": proc.pid})
            stdout, stderr = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = _terminate_process_group(proc)
            _write_log(paths, argv, _as_text(exc.stdout) + stdout, _as_text(exc.stderr) + stderr, "timeout")
            event(paths, request, "provider.timeout", {"timeout_seconds": timeout})
            error_excerpt = _output_excerpt(_as_text(exc.stdout) + stdout, _as_text(exc.stderr) + stderr)
            provider_status["error_excerpt"] = error_excerpt
            status = write_status(
                paths,
                request,
                "failed",
                failure_reason="timeout",
                provider_status=provider_status,
                message=_failure_message(f"超时 {timeout}s", error_excerpt),
            )
            return {"status": status}
        except KeyboardInterrupt:
            stdout, stderr = _terminate_process_group(proc)
            _write_log(paths, argv, stdout, stderr, "cancelled")
            event(paths, request, "provider.cancelled", {"transport": self.transport})
            status = write_status(paths, request, CANCELLED, provider_status=provider_status, message="cli cancelled")
            return {"status": status}

        returncode = proc.returncode if proc is not None else 1
        _write_log(paths, argv, stdout, stderr, f"returncode={returncode}")
        event(paths, request, "provider.exited", {"returncode": returncode})
        provider_status["returncode"] = returncode

        if paths.result_file.exists():
            status = mark_done_if_valid(paths, request)
            return {"status": status, "result": read_result(paths)}

        if returncode == 0 and self.profile.result_contract != "required":
            write_result(paths, request, "succeeded", summary="cli 退出 0,无 result 契约")
            status = mark_done_if_valid(paths, request)
            return {"status": status}

        reason = "result_missing" if returncode == 0 else "exited"
        error_excerpt = _output_excerpt(stdout, stderr)
        provider_status["error_excerpt"] = error_excerpt
        base_message = f"退出 {returncode},缺 required result_file" if reason == "result_missing" else f"退出码 {returncode}"
        status = write_status(
            paths,
            request,
            "failed",
            failure_reason=reason,
            provider_status=provider_status,
            message=_failure_message(base_message, error_excerpt),
        )
        return {"status": status}


def _expand(binary: str) -> str:
    return str(Path(binary).expanduser()) if binary.startswith("~") else binary


def _runtime_env(request: RunRequest, paths: RunPaths, raw: dict[str, Any] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENTRUN_PROJECT_ID": request.project_id,
            "AGENTRUN_RUN_TYPE": request.run_type,
            "AGENTRUN_RUN_ID": request.run_id,
            "AGENTRUN_REQUEST_FILE": str(paths.request_file),
            "AGENTRUN_STATUS_FILE": str(paths.status_file),
            "AGENTRUN_EVENTS_FILE": str(paths.events_file),
            "AGENTRUN_OUTPUT_LOG": str(paths.output_log),
            "AGENTRUN_RESULT_FILE": str(paths.result_file),
        }
    )
    raw = raw or {}
    static_env = raw.get("env")
    if isinstance(static_env, dict):
        env.update({str(k): str(v) for k, v in static_env.items()})
    for name in raw.get("env_passthrough") or raw.get("env_allowlist") or []:
        key = str(name)
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _write_log(paths: RunPaths, argv: list[str], stdout: str, stderr: str, status_line: str) -> None:
    paths.output_log.write_text(
        "\n".join([f"argv={argv!r}", status_line, "--- stdout ---", stdout, "--- stderr ---", stderr]) + "\n",
        encoding="utf-8",
    )


def _output_excerpt(stdout: str, stderr: str, max_lines: int = 8, max_chars: int = 1000) -> str:
    parts: list[str] = []
    for label, value in (("stderr", stderr), ("stdout", stdout)):
        text = _clean_tail(value, max_lines=max_lines, max_chars=max_chars)
        if text:
            parts.append(f"{label}:\n{text}")
    return "\n".join(parts)


def _clean_tail(value: str, *, max_lines: int, max_chars: int) -> str:
    lines = [line.strip() for line in (value or "").splitlines()]
    lines = [line for line in lines if line]
    text = "\n".join(lines[-max_lines:])
    return text[-max_chars:]


def _failure_message(base: str, error_excerpt: str) -> str:
    return f"{base}\n{error_excerpt}" if error_excerpt else base


def _terminate_process_group(proc: subprocess.Popen[str] | None) -> tuple[str, str]:
    if proc is None:
        return "", ""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=2)
            return stdout or "", stderr or ""
        except subprocess.TimeoutExpired as exc:
            os.killpg(pgid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return _as_text(exc.stdout) + (stdout or ""), _as_text(exc.stderr) + (stderr or "")
    except ProcessLookupError:
        stdout, stderr = proc.communicate()
        return stdout or "", stderr or ""


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
