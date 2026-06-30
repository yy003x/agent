"""cli provider:结构化 exec + stdin,读 result.json(见 design/03 §1)。

不从 terminal 文本判断成功;退出后只认 result.json。超时归 failed + failure_reason: timeout。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
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
        stream_log: _StreamLog | None = None
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
            stream_log = _StreamLog(paths, argv)
            stream_log.attach(proc.stdout, proc.stderr)
            _send_prompt(proc, prompt)
            provider_status["pid"] = proc.pid
            provider_status["pgid"] = os.getpgid(proc.pid)
            write_status(paths, request, RUNNING, provider_status=provider_status, message="cli running")
            event(paths, request, "status.changed", {"state": RUNNING, "transport": self.transport, "pid": proc.pid})
            returncode = _wait_process(proc, timeout)
            stdout, stderr = stream_log.finish(f"returncode={returncode}")
        except subprocess.TimeoutExpired:
            _terminate_process_group(proc)
            stdout, stderr = stream_log.finish("timeout") if stream_log else ("", "")
            event(paths, request, "provider.timeout", {"timeout_seconds": timeout})
            error_excerpt = _output_excerpt(stdout, stderr)
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
            _terminate_process_group(proc)
            stdout, stderr = stream_log.finish("cancelled") if stream_log else ("", "")
            event(paths, request, "provider.cancelled", {"transport": self.transport})
            status = write_status(paths, request, CANCELLED, provider_status=provider_status, message="cli cancelled")
            return {"status": status}

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
        env.update({str(k): _expand_env_value(str(v)) for k, v in static_env.items()})
    for name in raw.get("env_passthrough") or raw.get("env_allowlist") or []:
        key = str(name)
        if key in os.environ:
            env[key] = os.environ[key]
    return env


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env_value(value: str) -> str:
    return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)


class _StreamLog:
    def __init__(self, paths: RunPaths, argv: list[str]) -> None:
        self._fh = paths.output_log.open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stdout_parts: list[str] = []
        self._stderr_parts: list[str] = []
        self._closed = False
        self._write(f"argv={argv!r}\nrunning\n--- stream ---\n")

    def attach(self, stdout, stderr) -> None:
        for label, pipe, parts in (
            ("stdout", stdout, self._stdout_parts),
            ("stderr", stderr, self._stderr_parts),
        ):
            if pipe is None:
                continue
            thread = threading.Thread(target=self._read_pipe, args=(label, pipe, parts), daemon=True)
            thread.start()
            self._threads.append(thread)

    def finish(self, status_line: str) -> tuple[str, str]:
        for thread in self._threads:
            thread.join(timeout=2)
        self._write(f"{status_line}\n")
        self._close()
        return "".join(self._stdout_parts), "".join(self._stderr_parts)

    def _read_pipe(self, label: str, pipe, parts: list[str]) -> None:
        try:
            for chunk in pipe:
                parts.append(chunk)
                self._write(f"[{label}] {chunk}")
        finally:
            pipe.close()

    def _write(self, text: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._fh.write(text)
            self._fh.flush()

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._fh.close()


def _send_prompt(proc: subprocess.Popen[str], prompt: str) -> None:
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except BrokenPipeError:
        pass


def _wait_process(proc: subprocess.Popen[str], timeout: int | None) -> int:
    deadline = time.monotonic() + timeout if timeout else None
    while proc.poll() is None:
        if deadline and time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(0.1)
    return int(proc.returncode or 0)


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


def _terminate_process_group(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGINT)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait()
    except ProcessLookupError:
        if proc.poll() is None:
            proc.wait()
