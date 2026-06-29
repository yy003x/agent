"""Shared AgentRun adapter for the local workbench UI.

The UI keeps its own session files under ``runs/workbench``. Execution is
delegated to ``~/agents/runtime`` and this adapter maps AgentRun metadata back
to the fields the HTTP API expects.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_ROOT = PROJECT_ROOT.parent
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("AGENT_SHARED_RUNTIME_ROOT", AGENTS_ROOT / "runtime")).expanduser()
DEFAULT_SHARED_RUNTIME_CLI = DEFAULT_SHARED_RUNTIME_ROOT / "bin" / "agentrun"
DEFAULT_SHARED_RUNS_DIR = PROJECT_ROOT / "runs" / "agentrun"
DEFAULT_AGENT_PYTHON = AGENTS_ROOT / ".venv" / "bin" / "python3"
PROJECT_ID = "agent"

SESSION_PROFILES = {
    "tmux": "tmux-codex",
}
TASK_PROFILES = {
    "fake": "fake",
    "code_cli": "codex-cli",
    "llm_api": "llm-api",
    "tmux": "tmux-codex",
}
TRANSPORT_LABELS = {
    "fake": "fake",
    "code_cli": "code_cli",
    "llm_api": "llm_api",
    "tmux": "tmux",
}


@dataclass(frozen=True)
class SharedRuntimeRunSpec:
    runtime: str
    prompt_text: str
    cwd: Path
    runtime_dir: Path
    result_file_name: str = "result.json"
    run_id: str | None = None
    timeout_seconds: int = 300
    provider_profile: str | None = None


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def result_file_path(run_dir: Path, result_file_name: str) -> Path:
    path = Path(result_file_name)
    return path if path.is_absolute() else run_dir / path


def text_tail(path: Path, max_bytes: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace"), truncated


def safe_result_from_text(text: str, *, status: str = "success", errors: list[str] | None = None) -> dict:
    text = (text or "").strip()
    return {
        "status": status,
        "assistant_message": text or "（runtime 未返回内容）",
        "summary": text[:160] if text else "runtime finished",
        "outputs": [],
        "questions": [],
        "errors": errors or [],
    }


def workbench_result_from_shared(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return safe_result_from_text("shared runtime 没有返回 result。", status="failed", errors=["missing shared result"])
    outcome = str(result.get("outcome") or result.get("status") or "failed")
    status = {
        "succeeded": "success",
        "failed": "failed",
        "blocked": "failed",
        "partial": "partial",
        "cancelled": "failed",
    }.get(outcome, outcome)
    summary = str(result.get("summary") or "").strip()
    errors = result.get("errors") or []
    if not isinstance(errors, list):
        errors = [str(errors)]
    error_text = [
        str(item.get("message") or item.get("error") or item) if isinstance(item, dict) else str(item)
        for item in errors
    ]
    payload = {
        "status": status,
        "assistant_message": str(result.get("assistant_message") or summary).strip(),
        "summary": summary or str(result.get("assistant_message") or "runtime finished"),
        "outputs": result.get("outputs") or result.get("artifacts") or [],
        "questions": result.get("questions") or [],
        "errors": error_text,
        "shared_result": result,
    }
    return payload


def _new_run_id(prefix: str = "shared") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class SharedRuntimeAdapter:
    def __init__(
        self,
        runtime_dir: str | Path,
        cli_path: str | Path | None = None,
        runs_dir: str | Path | None = None,
        runtime_root: str | Path | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        self.runtime_root = Path(runtime_root or DEFAULT_SHARED_RUNTIME_ROOT).expanduser().resolve()
        configured = cli_path or os.environ.get("AGENT_SHARED_RUNTIME_CLI") or DEFAULT_SHARED_RUNTIME_CLI
        self.cli_path = Path(configured).expanduser()
        self.runs_dir = Path(runs_dir or os.environ.get("AGENT_SHARED_RUNTIME_RUNS_DIR") or DEFAULT_SHARED_RUNS_DIR).expanduser().resolve()

    def run(self, spec: SharedRuntimeRunSpec) -> dict:
        profile = spec.provider_profile or _task_profile(spec.runtime)
        run_id = spec.run_id or _new_run_id("task")
        run_dir = self._prepare_local_run(run_id)
        prompt_file = run_dir / "prompt.md"
        result_file = result_file_path(run_dir, spec.result_file_name)
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        prompt_file.write_text(spec.prompt_text, encoding="utf-8")
        meta = {
            "run_id": run_id,
            "provider_kind": "shared_runtime",
            "shared_run_type": "task",
            "runtime": spec.runtime,
            "transport": TRANSPORT_LABELS.get(spec.runtime, spec.runtime),
            "provider_profile": profile,
            "cwd": str(Path(spec.cwd).resolve()),
            "run_dir": str(run_dir),
            "prompt_file": str(prompt_file),
            "result_file": str(result_file),
            "output_log": str(output_log),
            "status_file": str(status_file),
            "shared_runtime_cli": str(self.cli_path),
            "shared_runtime_root": str(self.runtime_root),
            "shared_runs_dir": str(self.runs_dir),
            "started_at": now(),
        }
        write_json(meta_file, meta)
        write_json(status_file, {**meta, "state": "running", "updated_at": now()})
        payload, proc = self._call(
            [
                "task",
                "run",
                "--project",
                PROJECT_ID,
                "--profile",
                profile,
                "--prompt-file",
                str(prompt_file),
                "--run-id",
                run_id,
                "--cwd",
                str(spec.cwd),
                "--deadline-seconds",
                str(spec.timeout_seconds),
                "--force",
                "--json",
            ],
            cwd=spec.cwd,
            timeout=spec.timeout_seconds,
        )
        output_log.write_text("## stdout\n" + proc.stdout + "\n\n## stderr\n" + proc.stderr, encoding="utf-8")
        shared_run_dir = self.runs_dir / "tasks" / PROJECT_ID / run_id
        shared_result_file = Path(str(payload.get("result_file") or shared_run_dir / "result.json")) if isinstance(payload, dict) else shared_run_dir / "result.json"
        shared_status_file = shared_run_dir / "status.json"
        shared_output_log = shared_run_dir / "output.log"
        shared_status = read_json(shared_status_file, {}) or {}
        shared_result = read_json(shared_result_file, None)
        result = workbench_result_from_shared(shared_result)
        if proc.returncode != 0 or _payload_failed(payload, shared_status):
            message = _shared_failure_message(payload, shared_status, shared_output_log, proc)
            result = safe_result_from_text(message, status="failed", errors=[message])
        write_json(result_file, result)
        shared_meta = {
            "shared_run_dir": str(shared_run_dir),
            "shared_result_file": str(shared_result_file),
            "shared_status_file": str(shared_status_file),
            "shared_output_log": str(shared_output_log),
            "shared_state": payload.get("state") if isinstance(payload, dict) else "",
            "shared_failure_reason": payload.get("failure_reason") if isinstance(payload, dict) else "",
            "shared_message": shared_status.get("message", ""),
        }
        meta.update(shared_meta)
        write_json(meta_file, meta)
        write_json(status_file, {**meta, **shared_meta, "state": "done" if result.get("status") == "success" else "failed", "updated_at": now()})
        return self.status(run_id)

    def start_session(
        self,
        *,
        runtime: str,
        prompt_text: str,
        cwd: Path,
        runtime_dir: Path,
        result_file_name: str = "result.json",
        run_id: str | None = None,
        timeout_seconds: int = 30,
        runtime_options: dict | None = None,
    ) -> dict:
        profile = _session_profile(runtime)
        actual_run_id = run_id or _new_run_id("session")
        run_dir = self._prepare_local_run(actual_run_id)
        prompt_file = run_dir / "prompt.md"
        result_file = result_file_path(run_dir, result_file_name)
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        payload, proc = self._call(
            [
                "session",
                "start",
                "--project",
                PROJECT_ID,
                "--profile",
                profile,
                "--run-id",
                actual_run_id,
                "--cwd",
                str(cwd),
                "--json",
            ],
            cwd=cwd,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            message = str(payload.get("error") if isinstance(payload, dict) else proc.stderr or proc.stdout).strip()
            raise RuntimeError(message or "shared runtime session start failed")
        shared_run_dir = self.runs_dir / "sessions" / PROJECT_ID / actual_run_id
        meta = {
            "run_id": actual_run_id,
            "provider_kind": "shared_runtime_session",
            "shared_run_type": "session",
            "runtime": runtime,
            "transport": TRANSPORT_LABELS.get(runtime, runtime),
            "provider_profile": profile,
            "runtime_options": runtime_options or {},
            "cwd": str(Path(cwd).resolve()),
            "run_dir": str(run_dir),
            "prompt_file": str(prompt_file),
            "startup_contract_path": str(prompt_file),
            "startup_contract_version": 1,
            "result_file": str(result_file),
            "result_path": str(result_file),
            "output_log": str(output_log),
            "output_path": str(output_log),
            "status_file": str(status_file),
            "shared_run_dir": str(shared_run_dir),
            "shared_status_file": str(shared_run_dir / "status.json"),
            "shared_events_file": str(shared_run_dir / "events.jsonl"),
            "shared_output_log": str(shared_run_dir / "output.log"),
            "shared_runtime_cli": str(self.cli_path),
            "shared_runtime_root": str(self.runtime_root),
            "shared_runs_dir": str(self.runs_dir),
            "tmux_session": payload.get("session", "") if isinstance(payload, dict) else "",
            "pane_id": payload.get("pane_id", "") if isinstance(payload, dict) else "",
            "provider_run_id": actual_run_id,
            "created_at": now(),
        }
        write_json(meta_file, meta)
        write_json(status_file, {**meta, "state": "running", "updated_at": now()})
        if prompt_text.strip():
            self.send(actual_run_id, prompt_text)
        return self.status(actual_run_id)

    def send(self, run_id: str, text: str, submit: bool = True) -> dict:
        meta = self._meta(run_id)
        if meta.get("shared_run_type") != "session":
            raise RuntimeError("send is only supported for shared runtime sessions")
        args = ["session", "send", run_id, "--project", PROJECT_ID, "--text", text, "--json"]
        if not submit:
            args.insert(-1, "--no-submit")
        payload, proc = self._call(args, cwd=Path(meta.get("cwd") or "."))
        if proc.returncode != 0:
            message = str(payload.get("error") if isinstance(payload, dict) else proc.stderr or proc.stdout).strip()
            raise RuntimeError(message or "shared runtime session send failed")
        return payload

    def status(self, run_id: str) -> dict:
        meta = self._meta(run_id)
        run_type = meta.get("shared_run_type", "task")
        if run_type == "session":
            payload, proc = self._call(["session", "status", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
            status = payload if isinstance(payload, dict) else {}
            state = _shared_state_to_workbench(str(status.get("state", "unknown")))
            result_file = Path(meta.get("result_file", ""))
            result = read_json(result_file) if result_file.exists() else None
            return {
                **meta,
                "ok": proc.returncode == 0,
                "state": state,
                "provider_state": status.get("state"),
                "result_exists": result_file.exists(),
                "result_valid": isinstance(result, dict),
                "result": result,
                "output_bytes": _path_size(Path(meta.get("shared_output_log") or meta.get("output_log", ""))),
                "updated_at": now(),
                "shared_status": status,
            }
        payload, proc = self._call(["task", "status", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
        status = payload if isinstance(payload, dict) else {}
        result_file = Path(meta.get("result_file", ""))
        result = read_json(result_file) if result_file.exists() else None
        return {
            **meta,
            "ok": proc.returncode == 0,
            "state": _shared_state_to_workbench(str(status.get("state", "failed"))),
            "provider_state": status.get("state"),
            "result_exists": result_file.exists(),
            "result_valid": isinstance(result, dict),
            "result": result,
            "output_bytes": _path_size(Path(meta.get("shared_output_log") or meta.get("output_log", ""))),
            "updated_at": now(),
            "shared_status": status,
        }

    def logs(self, run_id: str, max_bytes: int = 120_000) -> dict:
        meta = self._meta(run_id)
        tail = max(1, max_bytes // 200)
        command = "session" if meta.get("shared_run_type") == "session" else "task"
        payload, proc = self._call([command, "logs", run_id, "--project", PROJECT_ID, "--tail", str(tail), "--json"], cwd=Path(meta.get("cwd") or "."))
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("content") or payload.get("text") or "")
        if proc.returncode != 0 and not text:
            text = proc.stderr or proc.stdout
        truncated = len(text.encode("utf-8")) > max_bytes
        if truncated:
            text = text.encode("utf-8")[-max_bytes:].decode("utf-8", errors="replace")
        return {"run_id": run_id, "text": text, "truncated": truncated}

    def stop(self, run_id: str) -> dict:
        meta = self._meta(run_id)
        if meta.get("shared_run_type") == "session":
            payload, proc = self._call(["session", "stop", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
            if proc.returncode == 0:
                status_path = Path(meta["status_file"])
                status = read_json(status_path, {}) or {}
                write_json(status_path, {**status, "state": "stopped", "updated_at": now()})
            return payload
        self._call(["task", "cancel", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
        status_path = Path(meta["status_file"])
        status = read_json(status_path, {}) or {}
        stopped = {**meta, **status, "ok": True, "state": "stopped", "updated_at": now()}
        write_json(status_path, stopped)
        return stopped

    def list_local_runs(self) -> list[dict]:
        if not self.runtime_dir.exists():
            return []
        runs = []
        for path in sorted(self.runtime_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.is_dir() and (path / "meta.json").exists():
                try:
                    runs.append(self.status(path.name))
                except Exception as exc:  # noqa: BLE001
                    runs.append({"run_id": path.name, "state": "failed", "error": str(exc)})
        return runs

    def _prepare_local_run(self, run_id: str) -> Path:
        run_dir = (self.runtime_dir / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def _meta(self, run_id: str) -> dict:
        meta = read_json(self.runtime_dir / run_id / "meta.json", {}) or {}
        if not meta:
            raise RuntimeError(f"shared runtime run not found: {run_id}")
        return meta

    def _call(self, args: list[str], *, cwd: Path, timeout: int | float = 30) -> tuple[dict, subprocess.CompletedProcess[str]]:
        command, env, unavailable = self._command()
        if unavailable:
            return {"ok": False, "error": unavailable}, _failed_process(args, unavailable)
        proc = subprocess.run(
            [*command, "--runs-dir", str(self.runs_dir), *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=max(float(timeout) + 5, float(timeout)),
            check=False,
            env=env,
        )
        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception:
            payload = {"ok": proc.returncode == 0, "raw_stdout": proc.stdout, "error": proc.stderr.strip()}
        return payload, proc

    def _command(self) -> tuple[list[str], dict[str, str] | None, str]:
        if self.cli_path.exists() and os.access(self.cli_path, os.X_OK):
            return [str(self.cli_path)], None, ""
        src_dir = self.runtime_root / "src"
        if not src_dir.is_dir():
            return [], None, f"shared runtime src unavailable: {src_dir}"
        python = _python_bin()
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(src_dir)
            if not env.get("PYTHONPATH")
            else f"{src_dir}{os.pathsep}{env['PYTHONPATH']}"
        )
        return [python, "-m", "agentrun.cli.main"], env, ""


def shared_runtime_available() -> bool:
    configured = os.environ.get("AGENT_SHARED_RUNTIME_CLI")
    path = Path(configured).expanduser() if configured else DEFAULT_SHARED_RUNTIME_CLI
    if path.exists() and os.access(path, os.X_OK):
        return True
    return (DEFAULT_SHARED_RUNTIME_ROOT / "src" / "agentrun").is_dir() and Path(_python_bin()).exists()


def _session_profile(runtime: str) -> str:
    try:
        return SESSION_PROFILES[runtime]
    except KeyError as exc:
        raise RuntimeError(f"unsupported shared session runtime: {runtime}") from exc


def _task_profile(runtime: str) -> str:
    try:
        return TASK_PROFILES[runtime]
    except KeyError as exc:
        raise RuntimeError(f"unsupported shared task runtime: {runtime}") from exc


def _shared_state_to_workbench(status: str) -> str:
    return {
        "pending": "queued",
        "running": "running",
        "done": "done",
        "failed": "failed",
        "blocked": "failed",
        "succeeded": "done",
        "cancelled": "stopped",
        "partial": "done",
        "orphaned": "failed",
    }.get(status, status or "unknown")


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def _payload_failed(payload: dict | None, shared_status: dict | None) -> bool:
    if isinstance(payload, dict) and payload.get("state") in {"failed", "blocked", "cancelled"}:
        return True
    if isinstance(shared_status, dict) and shared_status.get("state") in {"failed", "blocked", "cancelled"}:
        return True
    return False


def _shared_failure_message(
    payload: dict | None,
    shared_status: dict | None,
    output_log: Path,
    proc: subprocess.CompletedProcess[str],
) -> str:
    status = shared_status if isinstance(shared_status, dict) else {}
    body = payload if isinstance(payload, dict) else {}
    provider_status = status.get("provider_status") if isinstance(status.get("provider_status"), dict) else {}
    pieces = [
        str(body.get("error") or "").strip(),
        str(status.get("message") or "").strip(),
        str(provider_status.get("error_excerpt") or "").strip(),
    ]
    if not any(pieces):
        pieces.append(_tail_nonempty(output_log))
    pieces.append((proc.stderr or proc.stdout or "").strip())
    message = "\n".join(_dedupe([piece for piece in pieces if piece]))
    if message:
        return message
    reason = status.get("failure_reason") or body.get("failure_reason") or "unknown"
    return f"shared runtime 执行失败：{reason}"


def _tail_nonempty(path: Path, max_lines: int = 12, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [line for line in lines if line and not line.startswith("argv=")]
    text = "\n".join(lines[-max_lines:])
    return text[-max_chars:]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _python_bin() -> str:
    if DEFAULT_AGENT_PYTHON.exists():
        return str(DEFAULT_AGENT_PYTHON)
    return sys.executable or "python3"


def _failed_process(args: list[str], stderr: str = "shared runtime cli unavailable") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=stderr)
