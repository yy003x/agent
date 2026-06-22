#!/usr/bin/env python3
"""Provider-style tmux runtime built around a result-file contract.

This follows the same boundary as mozi-agent-base: callers build a
``TmuxRunSpec`` and interact with a provider through start/status/logs/send/stop
instead of hand-coding tmux operations at each call site.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

PromptDelivery = Literal["manual", "paste", "file"]


class TmuxProviderError(RuntimeError):
    """Raised when a tmux provider run fails."""


@dataclass(frozen=True)
class IdleTickParams:
    """Output thresholds used to decide when a terminal agent is idle."""

    low_bytes: int
    high_bytes: int
    min_ticks: int
    max_ticks: int
    text_density: int = 0
    text_max_lines: int = 0


@dataclass(frozen=True)
class TmuxRunSpec:
    command: list[str]
    cwd: Path
    runtime_dir: Path
    tmux_session_name: str
    prompt_text: str = ""
    run_id: str | None = None
    prompt_delivery: PromptDelivery = "manual"
    prompt_file_name: str = "prompt.md"
    result_file_name: str = "result.json"
    env: dict[str, str] | None = None
    window_prefix: str = "book-agent"
    timeout_seconds: float = 1800
    poll_interval_seconds: float = 1
    result_stable_seconds: float = 1
    silence_threshold_seconds: float = 0.6
    output_rate_window_seconds: int = 5
    startup_delay_seconds: float = 1.5
    submit_delay_seconds: float = 0.15
    submit_key: str = "C-m"
    tail_bytes: int = 120_000
    prompt_idle_timeout_seconds: float = 300
    prompt_ready_settle_seconds: float = 2
    prompt_ready_settle_fast_seconds: float = 0.5
    prompt_stable_timeout_seconds: float = 10
    startup_idle: IdleTickParams = IdleTickParams(
        low_bytes=1000,
        high_bytes=10000,
        min_ticks=5,
        max_ticks=20,
        text_density=100,
        text_max_lines=20,
    )
    tui_startup_idle: IdleTickParams = IdleTickParams(
        low_bytes=3000,
        high_bytes=20000,
        min_ticks=5,
        max_ticks=25,
    )
    runtime_idle: IdleTickParams = IdleTickParams(
        low_bytes=50,
        high_bytes=500,
        min_ticks=4,
        max_ticks=12,
    )


@dataclass(frozen=True)
class TmuxRunHandle:
    run_id: str
    run_dir: Path
    prompt_file: Path
    result_file: Path
    output_log: Path
    status_file: Path
    meta_file: Path
    command_file: Path
    tmux_session: str
    tmux_window: str
    tmux_window_name: str
    tmux_pane: str


@dataclass(frozen=True)
class TmuxRunResult:
    handle: TmuxRunHandle
    content: str


class TmuxProvider:
    """Create and control tmux-backed provider runs."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()

    def start(self, spec: TmuxRunSpec) -> TmuxRunHandle:
        run_id = spec.run_id or _new_run_id()
        run_dir = (Path(spec.runtime_dir) / run_id).resolve()
        prompt_file = run_dir / spec.prompt_file_name
        result_file = _result_file_path(run_dir, spec.result_file_name)
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        command_file = run_dir / "command.sh"
        cwd = Path(spec.cwd).resolve()
        session = spec.tmux_session_name.strip()
        if not session:
            raise TmuxProviderError("tmux_session_name must not be empty")
        if "{" in session or "}" in session:
            raise TmuxProviderError("tmux_session_name must be fixed, not a template")
        if not spec.command:
            raise TmuxProviderError("tmux provider command must not be empty")

        run_dir.mkdir(parents=True, exist_ok=False)
        prompt_file.write_text(_render(spec.prompt_text, _values(run_id, run_dir, prompt_file, result_file, output_log, cwd)), encoding="utf-8")
        output_log.touch()

        window_name = f"{spec.window_prefix}-{run_id}"[:80]
        if self._session_exists(session):
            window, pane = self._create_window(session, window_name, cwd)
        else:
            window, pane = self._create_session(session, window_name, cwd)
        self._lock_window_name(window)

        handle = TmuxRunHandle(
            run_id=run_id,
            run_dir=run_dir,
            prompt_file=prompt_file,
            result_file=result_file,
            output_log=output_log,
            status_file=status_file,
            meta_file=meta_file,
            command_file=command_file,
            tmux_session=session,
            tmux_window=window,
            tmux_window_name=window_name,
            tmux_pane=pane,
        )
        self._tmux(["pipe-pane", "-t", pane, f"cat >> {shlex.quote(str(output_log))}"])

        meta = self._build_meta(spec, handle)
        command_file.write_text(_command_script(meta), encoding="utf-8")
        command_file.chmod(0o700)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_status(handle, self._status_from_sample(handle, meta, state="running", reason="started"))

        self._tmux(["send-keys", "-t", pane, "-l", "exec " + str(command_file)])
        self._tmux(["send-keys", "-t", pane, "C-m"])
        self.activate(run_id, require_match=False)
        if spec.prompt_delivery == "paste":
            self._ensure_prompt_submitted(handle, meta)
        return handle

    def wait(self, run_id: str) -> TmuxRunResult:
        meta = self._load_meta(run_id)
        handle = self._handle_from_meta(meta)
        deadline = float(meta["started_monotonic"]) + float(meta["timeout_seconds"])
        signature = None
        seen_at = None
        while True:
            now = time.monotonic()
            if handle.result_file.exists():
                current = _file_signature(handle.result_file)
                if current == signature:
                    seen_at = seen_at if seen_at is not None else now
                else:
                    signature = current
                    seen_at = now
                if now - (seen_at or now) >= float(meta["result_stable_seconds"]):
                    content = handle.result_file.read_text(encoding="utf-8")
                    self._write_status(handle, self._status_from_sample(handle, meta, state="done", reason="result_file_stable"))
                    return TmuxRunResult(handle=handle, content=content)

            if now >= deadline:
                self.stop(run_id, reason="timeout")
                raise TmuxProviderError(f"tmux run {run_id} timed out")

            if not self._pane_alive(handle.tmux_pane) and not handle.result_file.exists():
                previous = _read_json(handle.status_file, {})
                self._write_status(handle, self._status_from_sample(handle, meta, previous=previous, state="failed", reason="exited_without_result"))
                raise TmuxProviderError(f"tmux run {run_id} exited without result")

            previous = _read_json(handle.status_file, {})
            self._write_status(handle, self._status_from_sample(handle, meta, previous=previous))
            time.sleep(float(meta["poll_interval_seconds"]))

    def status(self, run_id: str) -> dict:
        meta = self._load_meta(run_id)
        handle = self._handle_from_meta(meta)
        previous = _read_json(handle.status_file, {})
        if handle.result_file.exists() and _file_age(handle.result_file) >= float(meta["result_stable_seconds"]):
            status = self._status_from_sample(handle, meta, previous=previous, state="done", reason="result_file_stable")
            self._write_status(handle, status)
            return status
        if previous.get("state") in {"done", "failed", "stopped"}:
            return previous
        status = self._status_from_sample(handle, meta, previous=previous)
        self._write_status(handle, status)
        return status

    def logs(self, run_id: str, *, max_bytes: int | None = None) -> str:
        handle = self._handle_from_meta(self._load_meta(run_id))
        if not handle.output_log.exists():
            return ""
        data = handle.output_log.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")

    def send(self, run_id: str, text: str, *, enter: bool = True) -> None:
        meta = self._load_meta(run_id)
        handle = self._handle_from_meta(meta)
        if not self._pane_matches_handle(handle):
            raise TmuxProviderError(f"tmux pane for run {run_id} does not match recorded identity")
        self._ensure_prompt_submitted(handle, meta)
        self._wait_idle_stable(
            handle,
            meta,
            timeout_seconds=float(meta["prompt_idle_timeout_seconds"]),
            settle_seconds=0,
            phase="runtime",
        )
        self.activate(run_id, require_match=False)
        self._paste_text(handle.tmux_pane, text)
        if enter:
            time.sleep(max(0.0, float(meta.get("submit_delay_seconds", 0.15))))
            self._tmux(["send-keys", "-t", handle.tmux_pane, meta.get("submit_key", "C-m")])
        previous = _read_json(handle.status_file, {})
        status = self._status_from_sample(handle, meta, previous=previous, state="running", reason="message_sent")
        self._write_status(handle, status)

    def activate(self, run_id: str, *, require_match: bool = True) -> None:
        handle = self._handle_from_meta(self._load_meta(run_id))
        if require_match and not self._pane_matches_handle(handle):
            raise TmuxProviderError(f"tmux pane for run {run_id} does not match recorded identity")
        self._tmux(["select-window", "-t", handle.tmux_window])
        self._tmux(["select-pane", "-t", handle.tmux_pane])

    def stop(self, run_id: str, *, reason: str = "stopped") -> None:
        meta = self._load_meta(run_id)
        handle = self._handle_from_meta(meta)
        self._close_run_pane(handle)
        previous = _read_json(handle.status_file, {})
        self._write_status(handle, self._status_from_sample(handle, meta, previous=previous, state="stopped", reason=reason))

    def close(self, run_id: str) -> None:
        self._close_run_pane(self._handle_from_meta(self._load_meta(run_id)))

    def _ensure_prompt_submitted(self, handle: TmuxRunHandle, meta: dict) -> None:
        if meta.get("prompt_delivery") != "paste":
            return
        submitted = handle.run_dir / "prompt_submitted"
        failed = handle.run_dir / "prompt_failed"
        if submitted.exists() or failed.exists() or handle.result_file.exists():
            return
        try:
            self._wait_idle_stable(
                handle,
                meta,
                timeout_seconds=float(meta["prompt_idle_timeout_seconds"]),
                settle_seconds=None,
                phase="startup",
            )
            if handle.result_file.exists():
                return
            self._paste_prompt_file(handle)
            previous = _read_json(handle.status_file, {})
            self._write_status(
                handle,
                self._status_from_sample(handle, meta, previous=previous, state="running", reason="prompt_pasted"),
            )
            try:
                self._wait_idle_stable(
                    handle,
                    meta,
                    timeout_seconds=float(meta["prompt_stable_timeout_seconds"]),
                    settle_seconds=0,
                    phase="runtime",
                )
            except TmuxProviderError as exc:
                if "did not become idle before prompt submission" not in str(exc):
                    raise
                previous = _read_json(handle.status_file, {})
                self._write_status(
                    handle,
                    self._status_from_sample(
                        handle,
                        meta,
                        previous=previous,
                        state="running",
                        reason="prompt_paste_settle_timeout_continue",
                    ),
                )
            if handle.result_file.exists():
                submitted.write_text(_now(), encoding="utf-8")
                return
            self._tmux(["send-keys", "-t", handle.tmux_pane, meta.get("submit_key", "C-m")])
            submitted.write_text(_now(), encoding="utf-8")
            previous = _read_json(handle.status_file, {})
            self._write_status(
                handle,
                self._status_from_sample(handle, meta, previous=previous, state="running", reason="prompt_submitted"),
            )
        except Exception:
            failed.write_text(_now(), encoding="utf-8")
            previous = _read_json(handle.status_file, {})
            self._write_status(
                handle,
                self._status_from_sample(handle, meta, previous=previous, state="failed", reason="prompt_submission_failed"),
            )
            raise

    def _wait_idle_stable(
        self,
        handle: TmuxRunHandle,
        meta: dict,
        *,
        timeout_seconds: float,
        settle_seconds: float | None,
        phase: str,
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        previous = _read_json(handle.status_file, {})
        while time.monotonic() < deadline:
            if handle.result_file.exists():
                return self._status_from_sample(handle, meta, previous=previous)
            if not self._pane_alive(handle.tmux_pane):
                status = self._status_from_sample(
                    handle,
                    meta,
                    previous=previous,
                    state="failed",
                    reason="exited_before_prompt_submit",
                )
                self._write_status(handle, status)
                raise TmuxProviderError(f"tmux run {handle.run_id} exited before prompt submission")

            status = self._status_from_sample(handle, meta, previous={**previous, "phase": phase})
            self._write_status(handle, status)
            previous = status
            required_settle = _prompt_ready_settle(meta, status) if settle_seconds is None else settle_seconds
            if status["state"] == "idle" and (
                required_settle <= 0 or float(status.get("idle_seconds") or 0) >= required_settle
            ):
                return status
            time.sleep(float(meta["poll_interval_seconds"]))

        status = self._status_from_sample(
            handle,
            meta,
            previous=previous,
            state="failed",
            reason="prompt_idle_timeout",
        )
        self._write_status(handle, status)
        raise TmuxProviderError(f"tmux run {handle.run_id} did not become idle before prompt submission")

    def _paste_prompt_file(self, handle: TmuxRunHandle) -> None:
        submission_file = handle.run_dir / "submission.md"
        submission_file.write_text(_submission_text(handle), encoding="utf-8")
        self._paste_text(handle.tmux_pane, submission_file.read_text(encoding="utf-8"))

    def _build_meta(self, spec: TmuxRunSpec, handle: TmuxRunHandle) -> dict:
        cwd = Path(spec.cwd).resolve()
        env = {
            "AGENT_WORKBENCH_RUN_ID": handle.run_id,
            "AGENT_WORKBENCH_RUN_DIR": str(handle.run_dir),
            "AGENT_WORKBENCH_PROMPT_FILE": str(handle.prompt_file),
            "AGENT_WORKBENCH_RESULT_FILE": str(handle.result_file),
            "AGENT_WORKBENCH_OUTPUT_LOG": str(handle.output_log),
        }
        values = _values(handle.run_id, handle.run_dir, handle.prompt_file, handle.result_file, handle.output_log, cwd)
        for key, value in (spec.env or {}).items():
            env[key] = _render(value, values)
        return {
            **values,
            "status_file": str(handle.status_file),
            "meta_file": str(handle.meta_file),
            "command_file": str(handle.command_file),
            "tmux_session": handle.tmux_session,
            "tmux_window": handle.tmux_window,
            "tmux_window_name": handle.tmux_window_name,
            "tmux_pane": handle.tmux_pane,
            "command": shlex.join(spec.command),
            "argv": spec.command,
            "env": env,
            "prompt_delivery": spec.prompt_delivery,
            "timeout_seconds": spec.timeout_seconds,
            "poll_interval_seconds": spec.poll_interval_seconds,
            "result_stable_seconds": spec.result_stable_seconds,
            "silence_threshold_seconds": spec.silence_threshold_seconds,
            "output_rate_window_seconds": spec.output_rate_window_seconds,
            "tail_bytes": spec.tail_bytes,
            "prompt_idle_timeout_seconds": spec.prompt_idle_timeout_seconds,
            "prompt_ready_settle_seconds": spec.prompt_ready_settle_seconds,
            "prompt_ready_settle_fast_seconds": spec.prompt_ready_settle_fast_seconds,
            "prompt_stable_timeout_seconds": spec.prompt_stable_timeout_seconds,
            "startup_idle": _idle_params_dict(spec.startup_idle),
            "tui_startup_idle": _idle_params_dict(spec.tui_startup_idle),
            "runtime_idle": _idle_params_dict(spec.runtime_idle),
            "submit_delay_seconds": spec.submit_delay_seconds,
            "submit_key": spec.submit_key,
            "started_at": _now(),
            "started_monotonic": time.monotonic(),
        }

    def _load_meta(self, run_id: str) -> dict:
        path = self.runtime_dir / run_id / "meta.json"
        if not path.exists():
            matches = list(self.runtime_dir.glob(f"*/{run_id}/meta.json"))
            if matches:
                path = matches[0]
        if not path.exists():
            raise TmuxProviderError(f"tmux run {run_id} not found in {self.runtime_dir}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _handle_from_meta(self, meta: dict) -> TmuxRunHandle:
        return TmuxRunHandle(
            run_id=meta["run_id"],
            run_dir=Path(meta["run_dir"]),
            prompt_file=Path(meta["prompt_file"]),
            result_file=Path(meta["result_file"]),
            output_log=Path(meta["output_log"]),
            status_file=Path(meta["status_file"]),
            meta_file=Path(meta["meta_file"]),
            command_file=Path(meta["command_file"]),
            tmux_session=meta["tmux_session"],
            tmux_window=meta["tmux_window"],
            tmux_window_name=meta["tmux_window_name"],
            tmux_pane=meta["tmux_pane"],
        )

    def _status_from_sample(self, handle: TmuxRunHandle, meta: dict, *,
                            previous: dict | None = None,
                            state: str | None = None, reason: str | None = None) -> dict:
        previous = previous or {}
        output_size = _file_size(handle.output_log)
        previous_size = int(previous.get("bytes_total", 0) or 0)
        result_exists = handle.result_file.exists()
        alive = self._pane_alive(handle.tmux_pane)
        output_mtime = handle.output_log.stat().st_mtime if handle.output_log.exists() else None
        idle_seconds = max(time.time() - output_mtime, 0) if output_mtime else None
        tail = _read_tail_bytes(handle.output_log, int(meta["tail_bytes"]))
        tui_detected = bool(previous.get("tui_detected")) or _contains_decset(tail)
        line_count = _clean_line_count(tail)
        rate_samples = _rate_samples(previous, time.time(), output_size)
        bytes_per_sec = _output_rate_bps(
            rate_samples,
            time.time(),
            int(meta["output_rate_window_seconds"]),
        )
        previous_state = previous.get("state")
        phase = previous.get("phase", "startup")
        if previous_state == "idle" and not _is_silent(idle_seconds, meta):
            phase = "runtime"
        silent = _is_silent(idle_seconds, meta)
        previous_silent_ticks = int(previous.get("silent_ticks", 0) or 0)
        previous_active_start = int(previous.get("active_start_bytes", 0) or 0)
        if silent:
            silent_ticks = previous_silent_ticks + 1
            active_start_bytes = previous_active_start
        else:
            active_start_bytes = output_size if previous_silent_ticks > 0 or previous_state == "idle" else previous_active_start
            silent_ticks = 0
        detector = _idle_detector(
            phase=phase,
            output_size=output_size,
            active_start_bytes=active_start_bytes,
            line_count=line_count,
            tui_detected=tui_detected,
            output_rate_bps=bytes_per_sec,
            meta=meta,
        )
        if state is None:
            if result_exists and _file_age(handle.result_file) >= float(meta["result_stable_seconds"]):
                state = "done"
                reason = "result_file_stable"
            elif not alive:
                state = "failed"
                reason = "exited_without_result"
            elif silent and silent_ticks >= detector["required_idle_ticks"]:
                state = "idle"
                reason = "stream_silent"
            else:
                state = "running"
                reason = "output_observed" if output_size > previous_size else "process_alive"
        return {
            "run_id": handle.run_id,
            "state": state,
            "reason": reason,
            "started_at": meta["started_at"],
            "updated_at": _now(),
            "cwd": meta["cwd"],
            "command": meta["command"],
            "argv": meta.get("argv", []),
            "tmux_session": handle.tmux_session,
            "tmux_window": handle.tmux_window,
            "tmux_window_name": handle.tmux_window_name,
            "tmux_pane": handle.tmux_pane,
            "pane_id": handle.tmux_pane,
            "run_dir": str(handle.run_dir),
            "prompt_file": str(handle.prompt_file),
            "result_file": str(handle.result_file),
            "output_log": str(handle.output_log),
            "result_file_exists": result_exists,
            "result_exists": result_exists,
            "process_alive": alive,
            "output_bytes": output_size,
            "bytes_total": output_size,
            "bytes_per_sec": round(bytes_per_sec, 3),
            "line_count": line_count,
            "last_output_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(output_mtime)) if output_mtime else None,
            "idle_seconds": round(idle_seconds, 3) if idle_seconds is not None else None,
            "elapsed_seconds": round(time.monotonic() - float(meta["started_monotonic"]), 3),
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

    def _write_status(self, handle: TmuxRunHandle, status: dict) -> None:
        handle.status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _paste_text(self, pane: str, text: str) -> None:
        name = f"workbench-provider-{uuid.uuid4().hex[:8]}"
        proc = subprocess.run(
            ["tmux", "load-buffer", "-b", name, "-"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise TmuxProviderError((proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip())
        try:
            self._tmux(["paste-buffer", "-d", "-b", name, "-t", pane])
        finally:
            subprocess.run(["tmux", "delete-buffer", "-b", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def _close_run_pane(self, handle: TmuxRunHandle) -> None:
        if self._pane_matches_handle(handle):
            subprocess.run(["tmux", "kill-pane", "-t", handle.tmux_pane], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def _pane_alive(self, pane: str) -> bool:
        return self._pane_identity(pane) is not None

    def _pane_matches_handle(self, handle: TmuxRunHandle) -> bool:
        identity = self._pane_identity(handle.tmux_pane)
        if identity is None:
            return False
        session, window, window_name, pane = identity
        return (
            session == handle.tmux_session
            and window == handle.tmux_window
            and window_name == handle.tmux_window_name
            and pane == handle.tmux_pane
        )

    def _pane_identity(self, pane: str) -> tuple[str, str, str, str] | None:
        proc = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 4 and parts[3] == pane:
                return parts[0], parts[1], parts[2], parts[3]
        return None

    def _session_exists(self, session: str) -> bool:
        return subprocess.run(["tmux", "has-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0

    def _create_session(self, session: str, window_name: str, cwd: Path) -> tuple[str, str]:
        return _parse_window_pane(self._tmux(["new-session", "-d", "-s", session, "-n", window_name, "-P", "-F", "#{window_id} #{pane_id}", "-c", str(cwd), "/bin/sh"], capture=True))

    def _create_window(self, session: str, window_name: str, cwd: Path) -> tuple[str, str]:
        return _parse_window_pane(self._tmux(["new-window", "-d", "-t", session, "-n", window_name, "-P", "-F", "#{window_id} #{pane_id}", "-c", str(cwd), "/bin/sh"], capture=True))

    def _lock_window_name(self, window: str) -> None:
        self._tmux(["set-option", "-w", "-t", window, "automatic-rename", "off"])
        self._tmux(["set-option", "-w", "-t", window, "allow-rename", "off"])

    def _tmux(self, args: list[str], *, capture: bool = False) -> str:
        proc = subprocess.run(
            ["tmux", *args],
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise TmuxProviderError(proc.stderr.strip() or "unknown tmux error")
        return proc.stdout if capture else ""


def _command_script(meta: dict) -> str:
    argv = ["env", *[f"{k}={v}" for k, v in sorted(meta["env"].items())], *meta["argv"]]
    return "#!/bin/sh\nexec " + shlex.join(argv) + "\n"


def _submission_text(handle: TmuxRunHandle) -> str:
    return f"""# 调度入口

## 任务文件

请按照以下任务文件完成本次任务：
{handle.prompt_file}

## 完成信号

最终结果必须写入：
{handle.result_file}

终端输出只作为过程日志，不能替代 result_file。

## 写入要求

- 无论成功、失败还是部分完成，都要写入 result_file。
- 如果任务文件要求 JSON，result_file 必须是单个合法 JSON 值，不要包 Markdown 代码块。
- 写入后重新读取 result_file，确认路径正确、内容可解析、结构符合任务文件要求。
"""


def _values(run_id: str, run_dir: Path, prompt_file: Path, result_file: Path, output_log: Path, cwd: Path) -> dict[str, str]:
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "prompt_file": str(prompt_file),
        "result_file": str(result_file),
        "output_log": str(output_log),
        "cwd": str(cwd),
    }


def _render(value: str, values: dict[str, str]) -> str:
    for key, replacement in values.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def _parse_window_pane(output: str) -> tuple[str, str]:
    parts = output.strip().split()
    if len(parts) != 2:
        raise TmuxProviderError(f"unexpected tmux output: {output!r}")
    return parts[0], parts[1]


def _result_file_path(run_dir: Path, result_file_name: str) -> Path:
    path = Path(result_file_name)
    return path if path.is_absolute() else run_dir / path


def _idle_params_dict(params: IdleTickParams) -> dict[str, int]:
    return {
        "low_bytes": params.low_bytes,
        "high_bytes": params.high_bytes,
        "min_ticks": params.min_ticks,
        "max_ticks": params.max_ticks,
        "text_density": params.text_density,
        "text_max_lines": params.text_max_lines,
    }


def _read_tail_bytes(path: Path, max_bytes: int) -> bytes:
    if not path.exists():
        return b""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, os.SEEK_END)
        return fh.read(max_bytes)


def _contains_decset(data: bytes) -> bool:
    return b"\x1b[?" in data or b"\x1b=" in data


def _clean_line_count(data: bytes) -> int:
    text = data.decode("utf-8", errors="ignore")
    text = text.replace("\r", "\n")
    return len([line for line in text.splitlines() if line.strip()])


def _rate_samples(previous: dict, now: float, output_size: int) -> list[list[float]]:
    samples = previous.get("_rate_samples") or []
    cleaned: list[list[float]] = []
    for item in samples:
        if not isinstance(item, list | tuple) or len(item) != 2:
            continue
        try:
            ts = float(item[0])
            size = float(item[1])
        except (TypeError, ValueError):
            continue
        if now - ts <= 30:
            cleaned.append([ts, size])
    cleaned.append([now, float(output_size)])
    return cleaned[-20:]


def _output_rate_bps(samples: list[list[float]], now: float, window_seconds: int) -> float:
    if len(samples) < 2:
        return 0.0
    recent = [item for item in samples if now - item[0] <= window_seconds]
    if len(recent) < 2:
        recent = samples[-2:]
    first = recent[0]
    last = recent[-1]
    elapsed = max(last[0] - first[0], 0.001)
    return max(last[1] - first[1], 0.0) / elapsed


def _is_silent(idle_seconds: float | None, meta: dict) -> bool:
    return idle_seconds is not None and idle_seconds >= float(meta["silence_threshold_seconds"])


def _idle_detector(
    *,
    phase: str,
    output_size: int,
    active_start_bytes: int,
    line_count: int,
    tui_detected: bool,
    output_rate_bps: float,
    meta: dict,
) -> dict[str, int]:
    if phase == "runtime":
        params = meta["runtime_idle"]
    elif tui_detected:
        params = meta["tui_startup_idle"]
    else:
        params = meta["startup_idle"]
    burst = max(output_size - active_start_bytes, 0)
    low = int(params["low_bytes"])
    high = int(params["high_bytes"])
    min_ticks = int(params["min_ticks"])
    max_ticks = int(params["max_ticks"])
    if burst <= low and output_rate_bps <= low:
        required = min_ticks
    elif burst >= high or output_rate_bps >= high:
        required = max_ticks
    else:
        ratio = (max(burst, output_rate_bps) - low) / max(high - low, 1)
        required = round(min_ticks + ratio * (max_ticks - min_ticks))
    text_density = int(params.get("text_density") or 0)
    text_max_lines = int(params.get("text_max_lines") or 0)
    if text_density and text_max_lines and line_count <= text_max_lines and burst <= text_density * max(line_count, 1):
        required = min(required, min_ticks)
    return {"required_idle_ticks": max(required, 1), "burst_bytes": burst}


def _prompt_ready_settle(meta: dict, status: dict) -> float:
    if status.get("tui_detected"):
        return float(meta["prompt_ready_settle_seconds"])
    return float(meta["prompt_ready_settle_fast_seconds"])


def _new_run_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _file_age(path: Path) -> float:
    return time.time() - path.stat().st_mtime


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
