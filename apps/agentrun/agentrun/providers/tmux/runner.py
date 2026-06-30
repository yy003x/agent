"""tmux provider:会话内跑 codex/claude,done_file/result 契约(见 design/03 §3)。

完成只认 done_file + result.json,绝不从屏幕判断。env 显式注入(tmux 继承的是 server
环境快照,用当前环境变量必须显式传)。pane 身份四元组校验防误杀。
"""
from __future__ import annotations

import atexit
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentrun.core.config import Profile
from agentrun.core.contract import event, mark_done_if_valid, read_status, write_status
from agentrun.core.rundir import RunPaths
from agentrun.core.run import RUNNING, RunRequest, monotonic, utc_now
from agentrun.guardrail import Guardrail
from agentrun.providers.tmux.prompt import append_done_instruction, submission_text
from agentrun.providers.tmux.trust import ensure_trusted_cwd

PaneIdentity = tuple[str, str, str, str]

_ACTIVE_PANES: dict[str, PaneIdentity] = {}
_ATEXIT = False


class TmuxError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdleTickParams:
    low_bytes: int
    high_bytes: int
    min_ticks: int
    max_ticks: int
    text_density: int = 0
    text_max_lines: int = 0


@dataclass(frozen=True)
class PreparedPrompt:
    prompt_file: Path
    input_file: Path


class TmuxProvider:
    transport = "tmux"

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        raw = profile.raw
        self.session = str(raw.get("tmux_session_name", "agentrun"))
        self.prompt_delivery = str(raw.get("prompt_delivery", "paste"))
        self.tmux_input_mode = str(raw.get("tmux_input_mode", "submission"))
        if self.tmux_input_mode not in {"submission", "raw"}:
            raise TmuxError("tmux_input_mode 必须是 submission 或 raw")
        self.paste_bracketed = bool(raw.get("paste_bracketed", "codex" in profile.binary))
        self.auto_trust_cwd = [str(x) for x in (raw.get("auto_trust_cwd") or [])]
        self.env_passthrough = [str(x) for x in (raw.get("env_passthrough") or [])]
        self.poll = float(raw.get("poll_interval_seconds", 0.3))
        self.ready_timeout = float(raw.get("ready_timeout_seconds", raw.get("prompt_idle_timeout_seconds", 300)))
        self.prompt_idle_timeout = float(raw.get("prompt_idle_timeout_seconds", self.ready_timeout))
        self.prompt_ready_settle = float(raw.get("prompt_ready_settle_seconds", raw.get("ready_settle_seconds", 2)))
        self.prompt_ready_settle_fast = float(raw.get("prompt_ready_settle_fast_seconds", 0.5))
        self.prompt_stable_timeout = float(raw.get("prompt_stable_timeout_seconds", 10))
        self.silence_threshold = float(raw.get("silence_threshold_seconds", 0.6))
        self.output_rate_window = int(raw.get("output_rate_window_seconds", 5))
        self.tail_bytes = int(raw.get("tail_bytes", 1 << 20))
        self.startup_idle = _idle_params(
            raw,
            "startup_idle",
            IdleTickParams(low_bytes=1000, high_bytes=10000, min_ticks=5, max_ticks=20, text_density=100, text_max_lines=20),
        )
        self.tui_startup_idle = _idle_params(
            raw,
            "tui_startup_idle",
            IdleTickParams(low_bytes=3000, high_bytes=20000, min_ticks=5, max_ticks=25),
        )
        self.runtime_idle = _idle_params(
            raw,
            "runtime_idle",
            IdleTickParams(low_bytes=50, high_bytes=500, min_ticks=4, max_ticks=12),
        )

    # ---------- task:done_file/result 契约 ----------
    def run(self, request: RunRequest, paths: RunPaths) -> dict[str, Any]:
        self._require_tmux()
        self._validate_session_name()
        cwd = (request.cwd or Path.cwd()).resolve()
        self._ensure_trusted_cwd(cwd, request)

        done_file = paths.run_dir / "done"
        prepared_prompt = self._prepare_prompt(request, paths, done_file)
        window_name = f"agentrun-{request.run_id}"

        write_status(paths, request, RUNNING, message="tmux task running")
        event(paths, request, "status.changed", {"state": RUNNING, "transport": self.transport})

        command_sh = self._write_command_sh(paths, request, done_file, prepared_prompt.prompt_file if prepared_prompt else None)
        window_id, pane_id = self._open(window_name, cwd, command_sh)
        self._lock_window(window_id)
        self._tmux(["pipe-pane", "-t", pane_id, f"cat >> {shlex.quote(str(paths.output_log))}"], check=False)
        identity = (self.session, window_id, window_name, pane_id)
        _register(identity)

        try:
            self._write_running_status(
                paths,
                request,
                identity=identity,
                message="tmux task running",
                command_file=command_sh,
            )
            if self.prompt_delivery == "paste" and prepared_prompt is not None:
                submitted = self._ensure_prompt_submitted(request, paths, done_file, prepared_prompt, identity)
                if not submitted:
                    return {"status": read_status(paths) or {}}

            status = self._await_done(request, paths, done_file, identity)
            return {"status": status}
        finally:
            self._close(pane_id, identity)

    def _await_done(self, request: RunRequest, paths: RunPaths, done_file: Path, identity: PaneIdentity) -> dict[str, Any]:
        deadline = monotonic() + request.deadline_seconds if request.deadline_seconds else None
        while True:
            if done_file.exists():
                return mark_done_if_valid(paths, request)
            if not self._pane_matches(identity):
                if done_file.exists():
                    return mark_done_if_valid(paths, request)
                provider_status = self._sample_status(request, paths, identity=identity, write=False)
                provider_status["reason"] = "exited_without_done_file"
                return write_status(
                    paths,
                    request,
                    "failed",
                    failure_reason="exited",
                    provider_status=provider_status,
                    message="pane 提前退出且无 done_file",
                )
            if deadline and monotonic() >= deadline:
                return write_status(
                    paths,
                    request,
                    "failed",
                    failure_reason="timeout",
                    provider_status=self._provider_status(paths),
                    message="tmux task 超时",
                )
            self._sample_status(request, paths, identity=identity)
            time.sleep(self.poll)

    def _ensure_prompt_submitted(
        self,
        request: RunRequest,
        paths: RunPaths,
        done_file: Path,
        prompt: PreparedPrompt,
        identity: PaneIdentity,
    ) -> bool:
        submitted = paths.run_dir / "prompt_submitted"
        failed = paths.run_dir / "prompt_failed"
        if submitted.exists() or done_file.exists():
            return True
        if failed.exists():
            return False

        try:
            self._wait_idle_stable(
                request,
                paths,
                done_file,
                identity=identity,
                timeout_seconds=self.prompt_idle_timeout,
                settle_seconds=None,
                timeout_reason="prompt_ready_timeout",
                wait_context="before prompt paste",
            )
            if done_file.exists():
                return True
            self._clear_input(identity[3])
            self._paste_submission_file(paths, identity[3], prompt, done_file)
            self._reset_detector(request, paths, identity=identity, reason="prompt_pasted")
            self._wait_idle_stable(
                request,
                paths,
                done_file,
                identity=identity,
                timeout_seconds=self.prompt_stable_timeout,
                settle_seconds=0,
                timeout_reason="prompt_stable_timeout",
                wait_context="after prompt paste",
            )
            if done_file.exists():
                submitted.write_text(utc_now(), encoding="utf-8")
                return True
            self._tmux(["send-keys", "-t", identity[3], "C-m"], check=False)
            submitted.write_text(utc_now(), encoding="utf-8")
            self._reset_detector(request, paths, identity=identity, reason="prompt_submitted")
            return True
        except Exception as exc:
            failed.write_text(utc_now(), encoding="utf-8")
            current = read_status(paths) or {}
            if current.get("state") != "failed":
                provider_status = self._provider_status(paths)
                provider_status["reason"] = "prompt_submission_failed"
                provider_status["error"] = str(exc)
                write_status(
                    paths,
                    request,
                    "failed",
                    failure_reason="provider_error",
                    provider_status=provider_status,
                    message="tmux prompt 投递失败",
                )
            return False

    def _wait_idle_stable(
        self,
        request: RunRequest,
        paths: RunPaths,
        done_file: Path,
        *,
        identity: PaneIdentity | None,
        timeout_seconds: float,
        settle_seconds: float | None,
        timeout_reason: str,
        wait_context: str,
    ) -> dict[str, Any]:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if done_file.exists():
                return self._sample_status(request, paths, identity=identity)
            if identity is not None and not self._pane_matches(identity):
                provider_status = self._sample_status(request, paths, identity=identity, write=False)
                provider_status["reason"] = "exited_before_prompt_submit"
                write_status(
                    paths,
                    request,
                    "failed",
                    failure_reason="exited",
                    provider_status=provider_status,
                    message="pane 在 prompt 投递前退出",
                )
                raise TmuxError("tmux pane exited before prompt submission")

            status = self._sample_status(request, paths, identity=identity)
            required_settle = self._prompt_ready_settle(status) if settle_seconds is None else settle_seconds
            if status.get("state") == "idle" and (
                required_settle <= 0 or float(status.get("idle_seconds") or 0) >= required_settle
            ):
                return status
            time.sleep(self.poll)

        provider_status = self._provider_status(paths)
        provider_status["reason"] = timeout_reason
        write_status(
            paths,
            request,
            "failed",
            failure_reason="timeout",
            provider_status=provider_status,
            message=f"tmux prompt 投递等待超时:{wait_context}",
        )
        raise TmuxError(_idle_timeout_message(paths, provider_status, wait_context))

    # ---------- session 生命周期 ----------
    def start_session(self, request: RunRequest, paths: RunPaths) -> dict[str, Any]:
        self._require_tmux()
        self._validate_session_name()
        cwd = (request.cwd or Path.cwd()).resolve()
        self._ensure_trusted_cwd(cwd, request)
        done_file = paths.run_dir / "done"
        window_name = f"agentrun-{request.run_id}"
        command_sh = self._write_command_sh(paths, request, done_file, None)
        window_id, pane_id = self._open(window_name, cwd, command_sh)
        self._lock_window(window_id)
        self._tmux(["pipe-pane", "-t", pane_id, f"cat >> {shlex.quote(str(paths.output_log))}"], check=False)
        identity = (self.session, window_id, window_name, pane_id)
        try:
            self._write_running_status(
                paths,
                request,
                identity=identity,
                message="tmux session running",
                command_file=command_sh,
            )
        except Exception:
            self._close(pane_id, identity)
            raise
        return {
            "session": self.session,
            "window_id": window_id,
            "window_name": window_name,
            "pane_id": pane_id,
            "attach": f"tmux attach -t {self.session}",
        }

    def send(self, paths: RunPaths, text: str, submit: bool = True) -> dict[str, Any]:
        identity = self._identity_from_status(paths)
        if identity is None:
            raise TmuxError("status 缺少 tmux pane 身份")
        pane_id = identity[3]
        self._paste(pane_id, text, bracketed=self.paste_bracketed)
        if submit:
            self._tmux(["send-keys", "-t", pane_id, "C-m"], check=False)
        import hashlib

        return {"sent": True, "pane_id": pane_id, "chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}

    def logs(self, paths: RunPaths, tail: int = 120) -> dict[str, Any]:
        try:
            pane_id = self._identity_from_status(paths)[3]
        except TmuxError:
            return {"content": self._read_log(paths)}
        cap = self._tmux(["capture-pane", "-p", "-t", pane_id, "-S", f"-{tail}"], check=False)
        return {"content": cap.stdout if cap.returncode == 0 else self._read_log(paths)}

    def interrupt(self, paths: RunPaths) -> dict[str, Any]:
        identity = self._identity_from_status(paths)
        if identity is None:
            raise TmuxError("status 缺少 tmux pane 身份")
        pane_id = identity[3]
        self._tmux(["send-keys", "-t", pane_id, "C-c"], check=False)
        return {"interrupted": True, "pane_id": pane_id}

    def cancel(self, paths: RunPaths) -> dict[str, Any]:
        identity = self._identity_from_status(paths, require_match=False)
        if identity is None:
            return {"cancelled": False, "already_gone": True}
        self._close(identity[3], identity)
        return {"cancelled": True, "pane_id": identity[3]}

    def stop(self, paths: RunPaths) -> dict[str, Any]:
        identity = self._identity_from_status(paths, require_match=False)
        if identity is None:
            return {"stopped": False, "already_gone": True}
        self._close(identity[3], identity)
        return {"stopped": True, "pane_id": identity[3]}

    def session_status(self, paths: RunPaths) -> dict[str, Any]:
        status = read_status(paths) or {}
        ps = status.get("provider_status", {})
        identity = self._identity_from_provider_status(ps, require_match=False)
        alive = self._pane_matches(identity) if identity is not None else False
        return {
            "state": status.get("state"),
            "alive": alive,
            "session": ps.get("session"),
            "window_id": ps.get("window_id"),
            "window_name": ps.get("window_name"),
            "pane_id": ps.get("pane_id"),
        }

    # ---------- tmux 原语 ----------
    def _open(self, window_name: str, cwd: Path, command_file: Path | None = None) -> tuple[str, str]:
        fmt = ["-P", "-F", "#{window_id} #{pane_id}"]
        command = ["/bin/sh"]
        if command_file is not None:
            command = [str(command_file)]
        if self._session_exists():
            out = self._tmux(["new-window", "-d", "-t", self.session, "-n", window_name, *fmt, "-c", str(cwd), *command]).stdout
        else:
            out = self._tmux(["new-session", "-d", "-s", self.session, "-n", window_name, *fmt, "-c", str(cwd), *command]).stdout
        parts = out.strip().split()
        if len(parts) != 2:
            raise TmuxError(f"tmux 返回异常: {out!r}")
        return parts[0], parts[1]

    def _lock_window(self, window_id: str) -> None:
        self._tmux(["set-option", "-w", "-t", window_id, "automatic-rename", "off"], check=False)
        self._tmux(["set-option", "-w", "-t", window_id, "allow-rename", "off"], check=False)

    def _send_line(self, pane_id: str, line: str) -> None:
        self._paste(pane_id, line, bracketed=False)
        self._tmux(["send-keys", "-t", pane_id, "C-m"], check=False)

    def _paste(self, pane_id: str, text: str, bracketed: bool = False) -> None:
        buf = f"agentrun-{uuid.uuid4().hex[:8]}"
        proc = subprocess.run(["tmux", "load-buffer", "-b", buf, "-"], input=text.encode("utf-8"),
                              capture_output=True, check=False)
        if proc.returncode != 0:
            raise TmuxError((proc.stderr or b"").decode().strip() or "load-buffer 失败")
        args = ["paste-buffer", "-d", "-b", buf, "-t", pane_id]
        if bracketed:
            args.insert(1, "-p")  # 括号粘贴:多行作为单次输入,避免换行被当作提交
        try:
            self._tmux(args, check=False)
        finally:
            self._tmux(["delete-buffer", "-b", buf], check=False)

    def _paste_file(self, pane_id: str, path: Path, bracketed: bool = False) -> None:
        buf = f"agentrun-{uuid.uuid4().hex[:8]}"
        proc = subprocess.run(
            ["tmux", "load-buffer", "-b", buf, str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise TmuxError(proc.stderr.strip() or "load-buffer 失败")
        args = ["paste-buffer", "-d", "-b", buf, "-t", pane_id]
        if bracketed:
            args.insert(1, "-p")
        try:
            self._tmux(args, check=False)
        finally:
            self._tmux(["delete-buffer", "-b", buf], check=False)

    def _paste_submission_file(self, paths: RunPaths, pane_id: str, prompt: PreparedPrompt, done_file: Path) -> None:
        submission_file = paths.run_dir / "submission.md"
        if self.tmux_input_mode == "raw":
            submission_file.write_text(prompt.input_file.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            submission_file.write_text(submission_text(prompt.prompt_file, paths.result_file, done_file), encoding="utf-8")
        self._paste_file(pane_id, submission_file, bracketed=self.paste_bracketed)

    def _clear_input(self, pane_id: str) -> None:
        """清掉 TUI 可能恢复的输入草稿,避免把新任务追加到已有文本后。"""
        self._tmux(["send-keys", "-t", pane_id, "C-u"], check=False)
        time.sleep(0.1)

    def _session_exists(self) -> bool:
        return self._tmux(["has-session", "-t", self.session], check=False).returncode == 0

    def _pane_identity(self, pane_id: str | None) -> tuple[str, str, str, str] | None:
        if not pane_id:
            return None
        proc = self._tmux(["list-panes", "-a", "-F", "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}"], check=False)
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 4 and parts[3] == pane_id:
                return parts[0], parts[1], parts[2], parts[3]
        return None

    def _pane_matches(self, identity: PaneIdentity) -> bool:
        return self._pane_identity(identity[3]) == identity

    def _close(self, pane_id: str, identity: PaneIdentity) -> None:
        try:
            if self._pane_matches(identity):
                self._tmux(["kill-pane", "-t", pane_id], check=False)
        finally:
            _unregister(pane_id)

    def _validate_session_name(self) -> None:
        if "{" in self.session or "}" in self.session:
            raise TmuxError("tmux_session_name 必须是固定名,不能是模板")

    def _ensure_trusted_cwd(self, cwd: Path, request: RunRequest) -> None:
        if not self.auto_trust_cwd:
            return
        Guardrail(
            capabilities=set(request.allowed_actions),
            forbidden_actions=set(request.forbidden_actions),
        ).require("auto_trust_cwd")
        ensure_trusted_cwd(cwd, self.auto_trust_cwd)

    def _command_display(self) -> str:
        argv = [self.profile.binary, *self.profile.default_args]
        return shlex.join(argv)

    def _provider_identity_status(
        self,
        identity: PaneIdentity,
        *,
        command_file: Path | None = None,
    ) -> dict[str, Any]:
        status = {
            "session": identity[0],
            "window_id": identity[1],
            "window_name": identity[2],
            "pane_id": identity[3],
            "binary": self.profile.binary,
            "argv": list(self.profile.default_args),
            "command_display": self._command_display(),
        }
        if command_file is not None:
            status["command_file"] = str(command_file)
        return status

    def _write_running_status(
        self,
        paths: RunPaths,
        request: RunRequest,
        *,
        identity: PaneIdentity,
        message: str,
        command_file: Path | None = None,
    ) -> dict[str, Any]:
        return write_status(
            paths,
            request,
            RUNNING,
            provider_status=self._provider_identity_status(identity, command_file=command_file),
            message=message,
        )

    def _provider_status(self, paths: RunPaths) -> dict[str, Any]:
        status = read_status(paths) or {}
        provider_status = status.get("provider_status") or {}
        return dict(provider_status) if isinstance(provider_status, dict) else {}

    def _sample_status(
        self,
        request: RunRequest,
        paths: RunPaths,
        *,
        identity: PaneIdentity | None = None,
        write: bool = True,
    ) -> dict[str, Any]:
        now = time.time()
        previous = self._provider_status(paths)
        output_size = _file_size(paths.output_log)
        previous_size = int(previous.get("bytes_total", 0) or 0)
        output_mtime = paths.output_log.stat().st_mtime if paths.output_log.exists() else None
        idle_seconds = max(now - output_mtime, 0) if output_mtime else None
        tail = _read_tail_bytes(paths.output_log, self.tail_bytes)
        tui_detected = bool(previous.get("tui_detected")) or _contains_decset(tail)
        line_count = _clean_line_count(tail)
        rate_samples = _rate_samples(previous, now, output_size)
        bytes_per_sec = _output_rate_bps(rate_samples, now, self.output_rate_window)
        alive = self._pane_matches(identity) if identity is not None else None

        previous_state = previous.get("state")
        previous_phase = str(previous.get("phase") or "startup")
        phase = previous_phase
        silent = _is_silent(idle_seconds, self.silence_threshold)
        if previous_state == "idle" and not silent:
            phase = "runtime"

        previous_silent_ticks = int(previous.get("silent_ticks", 0) or 0)
        previous_active_start = int(previous.get("active_start_bytes", 0) or 0)
        if silent:
            silent_ticks = previous_silent_ticks + 1
            active_start_bytes = previous_active_start
        else:
            active_start_bytes = output_size if previous_silent_ticks > 0 or previous_state == "idle" else previous_active_start
            silent_ticks = 0

        detector = self._idle_detector(
            phase=phase,
            output_size=output_size,
            active_start_bytes=active_start_bytes,
            line_count=line_count,
            tui_detected=tui_detected,
            output_rate_bps=bytes_per_sec,
        )

        done_file = paths.run_dir / "done"
        result_file_exists = paths.result_file.exists()
        done_file_exists = done_file.exists()
        if done_file_exists and result_file_exists:
            provider_state = "done"
            reason = "done_file_present"
        elif done_file_exists:
            provider_state = "failed"
            reason = "done_file_without_result"
        elif alive is False:
            provider_state = "failed"
            reason = "exited_without_done_file" if result_file_exists else "exited_without_result"
        elif silent and silent_ticks >= detector["required_idle_ticks"]:
            provider_state = "idle"
            reason = "stream_silent"
        else:
            provider_state = "running"
            reason = "output_observed" if output_size > previous_size else "process_alive"

        status: dict[str, Any] = {
            "state": provider_state,
            "reason": reason,
            "session": identity[0] if identity is not None else self.session,
            "window_id": identity[1] if identity is not None else None,
            "window_name": identity[2] if identity is not None else None,
            "pane_id": identity[3] if identity is not None else None,
            "binary": self.profile.binary,
            "argv": list(self.profile.default_args),
            "command_display": self._command_display(),
            "result_file_exists": result_file_exists,
            "done_file_exists": done_file_exists,
            "process_alive": alive,
            "bytes_total": output_size,
            "bytes_per_sec": round(bytes_per_sec, 3),
            "line_count": line_count,
            "last_output_at": datetime.fromtimestamp(output_mtime, UTC).isoformat() if output_mtime else None,
            "idle_seconds": round(idle_seconds, 3) if idle_seconds is not None else None,
            "phase": phase,
            "tui_detected": tui_detected,
            "silent": silent,
            "silent_ticks": silent_ticks,
            "required_idle_ticks": detector["required_idle_ticks"],
            "burst_bytes": detector["burst_bytes"],
            "active_start_bytes": active_start_bytes,
            "tail_bytes_sampled": len(tail),
            "_rate_samples": rate_samples,
        }
        if previous.get("command_file"):
            status["command_file"] = previous["command_file"]
        if write:
            write_status(paths, request, RUNNING, provider_status=status, message="tmux task running")
        return status

    def _reset_detector(self, request: RunRequest, paths: RunPaths, *, identity: PaneIdentity | None, reason: str) -> None:
        output_size = _file_size(paths.output_log)
        status = self._sample_status(request, paths, identity=identity, write=False)
        status.update(
            {
                "state": "running",
                "reason": reason,
                "phase": "startup",
                "silent": False,
                "silent_ticks": 0,
                "active_start_bytes": output_size,
            }
        )
        write_status(paths, request, RUNNING, provider_status=status, message="tmux task running")

    def _prompt_ready_settle(self, status: dict[str, Any]) -> float:
        if status.get("tui_detected"):
            return self.prompt_ready_settle
        return self.prompt_ready_settle_fast if self.prompt_ready_settle_fast > 0 else self.prompt_ready_settle

    def _idle_detector(
        self,
        *,
        phase: str,
        output_size: int,
        active_start_bytes: int,
        line_count: int,
        tui_detected: bool,
        output_rate_bps: float,
    ) -> dict[str, int]:
        if phase == "startup":
            params = self.tui_startup_idle if tui_detected else self.startup_idle
            burst_bytes = output_size
            required = _calc_required_idle_ticks(burst_bytes, params)
            if output_size < params.low_bytes:
                scale = params.max_ticks // params.min_ticks
                if scale < 2:
                    scale = 2
                required = params.max_ticks * scale
            if (
                params.text_density > 0
                and line_count > 0
                and line_count < params.text_max_lines
                and output_size // line_count < params.text_density
                and required < params.max_ticks
            ):
                required = params.max_ticks
            if output_rate_bps == 0 and output_size >= params.high_bytes:
                required = min(required, params.min_ticks + 1)
        else:
            params = self.runtime_idle
            burst_bytes = max(output_size - active_start_bytes, 0)
            required = _calc_required_idle_ticks(burst_bytes, params)
        return {"required_idle_ticks": required, "burst_bytes": burst_bytes}

    def _prepare_prompt(self, request: RunRequest, paths: RunPaths, done_file: Path) -> PreparedPrompt | None:
        if not (request.prompt_file and request.prompt_file.exists()):
            return None
        user_text = request.prompt_file.read_text(encoding="utf-8")
        input_path = paths.run_dir / "input.md"
        input_path.write_text(user_text, encoding="utf-8")
        text = append_done_instruction(user_text, paths.result_file, done_file)
        prompt_path = paths.run_dir / "prompt.md"
        prompt_path.write_text(text, encoding="utf-8")
        return PreparedPrompt(prompt_file=prompt_path, input_file=input_path)

    def _write_command_sh(self, paths: RunPaths, request: RunRequest, done_file: Path, prompt_file: Path | None) -> Path:
        import os

        if not self.profile.binary:
            raise TmuxError("tmux profile binary 不能为空")
        env = {
            "AGENTRUN_RUN_ID": request.run_id,
            "AGENTRUN_RUN_DIR": str(paths.run_dir),
            "AGENTRUN_RESULT_FILE": str(paths.result_file),
            "AGENTRUN_DONE_FILE": str(done_file),
            "AGENTRUN_OUTPUT_LOG": str(paths.output_log),
        }
        if prompt_file is not None:
            env["AGENTRUN_PROMPT_FILE"] = str(prompt_file)
        static_env = self.profile.raw.get("env")
        if isinstance(static_env, dict):
            env.update({str(k): str(v) for k, v in static_env.items()})
        for name in self.env_passthrough:
            if name in os.environ:
                env[name] = os.environ[name]
        argv = ["env", *[f"{k}={v}" for k, v in sorted(env.items())], self.profile.binary, *self.profile.default_args]
        script = paths.run_dir / "command.sh"
        script.write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n", encoding="utf-8")
        script.chmod(0o700)
        return script

    def _identity_from_status(self, paths: RunPaths, *, require_match: bool = True) -> PaneIdentity | None:
        status = read_status(paths) or {}
        return self._identity_from_provider_status(status.get("provider_status") or {}, require_match=require_match)

    def _identity_from_provider_status(
        self,
        provider_status: dict[str, Any],
        *,
        require_match: bool,
    ) -> PaneIdentity | None:
        values = (
            provider_status.get("session"),
            provider_status.get("window_id"),
            provider_status.get("window_name"),
            provider_status.get("pane_id"),
        )
        if not all(values):
            if require_match:
                raise TmuxError("status 缺少 tmux pane 身份")
            return None
        identity = (str(values[0]), str(values[1]), str(values[2]), str(values[3]))
        if require_match and not self._pane_matches(identity):
            raise TmuxError("tmux pane 身份与 status 记录不匹配")
        return identity

    def _read_log(self, paths: RunPaths) -> str:
        return paths.output_log.read_text(encoding="utf-8", errors="replace") if paths.output_log.exists() else ""

    def _require_tmux(self) -> None:
        if shutil.which("tmux") is None:
            raise TmuxError("tmux 未安装")

    def _tmux(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tmux", *args], text=True, capture_output=True, check=check)


def _idle_params(raw: dict[str, Any], key: str, default: IdleTickParams) -> IdleTickParams:
    value = raw.get(key)
    if not isinstance(value, dict):
        return default
    return IdleTickParams(
        low_bytes=int(value.get("low_bytes", default.low_bytes)),
        high_bytes=int(value.get("high_bytes", default.high_bytes)),
        min_ticks=int(value.get("min_ticks", default.min_ticks)),
        max_ticks=int(value.get("max_ticks", default.max_ticks)),
        text_density=int(value.get("text_density", default.text_density)),
        text_max_lines=int(value.get("text_max_lines", default.text_max_lines)),
    )


def _calc_required_idle_ticks(burst_bytes: int, params: IdleTickParams) -> int:
    if burst_bytes >= params.high_bytes:
        return params.min_ticks
    if burst_bytes <= params.low_bytes:
        return params.max_ticks
    ratio = (burst_bytes - params.low_bytes) / (params.high_bytes - params.low_bytes)
    return params.max_ticks - int((params.max_ticks - params.min_ticks) * ratio)


def _is_silent(idle_seconds: float | None, threshold_seconds: float) -> bool:
    if idle_seconds is None:
        return False
    return idle_seconds >= threshold_seconds


def _rate_samples(previous: dict[str, Any] | None, now: float, bytes_total: int) -> list[dict[str, float | int]]:
    samples = list(previous.get("_rate_samples", [])) if previous else []
    samples.append({"at": now, "bytes": bytes_total})
    cutoff = now - 60
    return [sample for sample in samples[-120:] if float(sample.get("at", 0)) >= cutoff]


def _output_rate_bps(samples: list[dict[str, float | int]], now: float, window: int) -> float:
    if window <= 0:
        window = 5
    recent = [sample for sample in samples if now - float(sample.get("at", 0)) <= window]
    if len(recent) < 2:
        return 0.0
    first = recent[0]
    last = recent[-1]
    elapsed = float(last["at"]) - float(first["at"])
    if elapsed <= 0:
        return 0.0
    return max(int(last["bytes"]) - int(first["bytes"]), 0) / elapsed


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _read_tail_bytes(path: Path, limit: int) -> bytes:
    if not path.exists() or limit <= 0:
        return b""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(size - limit)
        return fh.read(limit)


_TUI_MODES = {47, 1000, 1002, 1003, 1047, 1049, 2026, 2031}


def _contains_decset(data: bytes) -> bool:
    end = len(data) - 3
    for i in range(end + 1):
        if data[i : i + 3] != b"\x1b[?":
            continue
        j = i + 3
        while j < len(data):
            n = 0
            digits = 0
            while j < len(data) and 48 <= data[j] <= 57:
                n = n * 10 + data[j] - 48
                digits += 1
                j += 1
            if digits == 0 or j >= len(data):
                break
            if data[j] == ord("h"):
                if n in _TUI_MODES:
                    return True
                break
            if data[j] == ord(";"):
                if n in _TUI_MODES:
                    return True
                j += 1
                continue
            break
    return False


_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\].*?(?:\x07|\x1b\\)"
    r"|\x1b.",
    re.DOTALL,
)


def _clean_line_count(data: bytes) -> int:
    text = data.decode("utf-8", errors="replace")
    count = 0
    for raw in text.splitlines():
        line = _strip_ansi_line(raw)
        if line:
            count += 1
    return count


def _strip_ansi_line(line: str) -> str:
    line = _ANSI_RE.sub("", line)
    line = "".join(ch for ch in line if ch >= " " or ch in "\r\t")
    line = line.rstrip("\r")
    if "\r" in line:
        line = line.rsplit("\r", 1)[-1]
    return line.rstrip(" \t")


def _idle_timeout_message(paths: RunPaths, status: dict[str, Any], wait_context: str) -> str:
    return (
        f"tmux run did not become idle {wait_context}; "
        f"reason={status.get('reason')} "
        f"phase={status.get('phase')} "
        f"silent_ticks={status.get('silent_ticks')}/{status.get('required_idle_ticks')} "
        f"idle_seconds={status.get('idle_seconds')} "
        f"bytes_total={status.get('bytes_total')} "
        f"bytes_per_sec={status.get('bytes_per_sec')} "
        f"tui_detected={status.get('tui_detected')} "
        f"last_output_at={status.get('last_output_at')} "
        f"output_log={paths.output_log} "
        f"status_file={paths.status_file}"
    )


def _register(identity: PaneIdentity) -> None:
    global _ATEXIT
    _ACTIVE_PANES[identity[3]] = identity
    if not _ATEXIT:
        atexit.register(_cleanup_active)
        _ATEXIT = True


def _unregister(pane_id: str) -> None:
    _ACTIVE_PANES.pop(pane_id, None)


def _cleanup_active() -> None:
    for pane_id, identity in list(_ACTIVE_PANES.items()):
        if _pane_identity_from_tmux(pane_id) == identity:
            subprocess.run(["tmux", "kill-pane", "-t", pane_id], capture_output=True, check=False)
        _unregister(pane_id)


def _pane_identity_from_tmux(pane_id: str) -> PaneIdentity | None:
    proc = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[3] == pane_id:
            return parts[0], parts[1], parts[2], parts[3]
    return None
