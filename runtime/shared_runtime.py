"""Shared runtime adapter for the local workbench UI.

The UI keeps its own session files under ``runs/workbench``. Execution is
delegated to ``~/agents/runtime`` and this adapter maps shared runtime metadata
back to the fields the existing HTTP API expects.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SHARED_RUNTIME_CLI = Path("/Users/yang/agents/runtime/scripts/agent-runtime")
PROJECT_ID = "agent"

SESSION_PROFILES = {
    "fake": "agent-fake-session",
    "codex_cli": "agent-codex-session",
    "claude_cli": "agent-claude-session",
}
TURN_PROFILES = {
    "fake": "fake",
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
    status = str(result.get("status", "failed"))
    if status == "succeeded":
        status = "success"
    payload = dict(result)
    payload["status"] = status
    payload.setdefault("assistant_message", str(payload.get("summary") or "").strip())
    payload.setdefault("summary", payload.get("assistant_message") or "runtime finished")
    payload.setdefault("outputs", payload.get("artifacts", []))
    payload.setdefault("questions", [])
    payload.setdefault("errors", [])
    return payload


def _new_run_id(prefix: str = "shared") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class SharedRuntimeAdapter:
    def __init__(self, runtime_dir: str | Path, cli_path: str | Path | None = None) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        configured = cli_path or os.environ.get("AGENT_SHARED_RUNTIME_CLI") or DEFAULT_SHARED_RUNTIME_CLI
        self.cli_path = Path(configured).expanduser()

    def run(self, spec: SharedRuntimeRunSpec) -> dict:
        profile = _turn_profile(spec.runtime)
        run_id = spec.run_id or _new_run_id("turn")
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
            "shared_run_type": "turn",
            "runtime": spec.runtime,
            "provider_profile": profile,
            "cwd": str(Path(spec.cwd).resolve()),
            "run_dir": str(run_dir),
            "prompt_file": str(prompt_file),
            "result_file": str(result_file),
            "output_log": str(output_log),
            "status_file": str(status_file),
            "shared_runtime_cli": str(self.cli_path),
            "started_at": now(),
        }
        write_json(meta_file, meta)
        write_json(status_file, {**meta, "state": "running", "updated_at": now()})
        payload, proc = self._call(
            [
                "turn",
                "run",
                "--project",
                PROJECT_ID,
                "--provider",
                profile,
                "--prompt-file",
                str(prompt_file),
                "--id",
                run_id,
                "--cwd",
                str(spec.cwd),
                "--force",
                "--json",
            ],
            cwd=spec.cwd,
            timeout=spec.timeout_seconds,
        )
        output_log.write_text("## stdout\n" + proc.stdout + "\n\n## stderr\n" + proc.stderr, encoding="utf-8")
        shared_result = payload.get("result") if isinstance(payload, dict) else None
        result = workbench_result_from_shared(shared_result)
        if proc.returncode != 0:
            message = str(payload.get("error") if isinstance(payload, dict) else proc.stderr or proc.stdout).strip()
            result = safe_result_from_text(message, status="failed", errors=[message])
        write_json(result_file, result)
        shared_meta = {
            "shared_run_dir": payload.get("run_dir") if isinstance(payload, dict) else "",
            "shared_result_file": payload.get("result_file") if isinstance(payload, dict) else "",
            "shared_status_file": payload.get("status_file") if isinstance(payload, dict) else "",
            "shared_output_log": payload.get("output_log") if isinstance(payload, dict) else "",
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
                "--provider",
                profile,
                "--id",
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
        session = payload.get("session", {}) if isinstance(payload, dict) else {}
        meta = {
            "run_id": actual_run_id,
            "provider_kind": "shared_runtime_session",
            "shared_run_type": "session",
            "runtime": runtime,
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
            "shared_run_dir": payload.get("run_dir", ""),
            "shared_status_file": payload.get("status_file", ""),
            "shared_events_file": payload.get("events_file", ""),
            "shared_output_log": payload.get("output_log", ""),
            "shared_runtime_cli": str(self.cli_path),
            "tmux_session": session.get("session_name", ""),
            "pane_id": session.get("pane_id", ""),
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
        run_type = meta.get("shared_run_type", "turn")
        if run_type == "session":
            payload, _proc = self._call(["session", "status", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
            status = payload.get("status", {}) if isinstance(payload, dict) else {}
            state = _shared_state_to_workbench(str(status.get("status", "unknown")))
            result_file = Path(meta.get("result_file", ""))
            result = read_json(result_file) if result_file.exists() else None
            return {
                **meta,
                "ok": bool(payload.get("ok", False)) if isinstance(payload, dict) else False,
                "state": state,
                "provider_state": status.get("status"),
                "result_exists": result_file.exists(),
                "result_valid": isinstance(result, dict),
                "result": result,
                "output_bytes": _path_size(Path(meta.get("shared_output_log") or meta.get("output_log", ""))),
                "updated_at": now(),
                "shared_status": status,
            }
        command = "turn" if run_type == "turn" else "task"
        payload, _proc = self._call([command, "status", run_id, "--project", PROJECT_ID, "--json"], cwd=Path(meta.get("cwd") or "."))
        status = payload.get("status", {}) if isinstance(payload, dict) else {}
        result_file = Path(meta.get("result_file", ""))
        result = read_json(result_file) if result_file.exists() else payload.get("result") if isinstance(payload, dict) else None
        return {
            **meta,
            "ok": bool(payload.get("ok", False)) if isinstance(payload, dict) else False,
            "state": _shared_state_to_workbench(str(status.get("status", "failed"))),
            "provider_state": status.get("status"),
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
        command = "session" if meta.get("shared_run_type") == "session" else str(meta.get("shared_run_type", "turn"))
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
        if not (self.cli_path.exists() and os.access(self.cli_path, os.X_OK)):
            return {"ok": False, "error": f"shared runtime cli unavailable: {self.cli_path}"}, _failed_process(args)
        proc = subprocess.run(
            [str(self.cli_path), *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception:
            payload = {"ok": proc.returncode == 0, "raw_stdout": proc.stdout, "error": proc.stderr.strip()}
        return payload, proc


def shared_runtime_available() -> bool:
    configured = os.environ.get("AGENT_SHARED_RUNTIME_CLI")
    path = Path(configured).expanduser() if configured else DEFAULT_SHARED_RUNTIME_CLI
    return path.exists() and os.access(path, os.X_OK)


def _session_profile(runtime: str) -> str:
    try:
        return SESSION_PROFILES[runtime]
    except KeyError as exc:
        raise RuntimeError(f"unsupported shared session runtime: {runtime}") from exc


def _turn_profile(runtime: str) -> str:
    try:
        return TURN_PROFILES[runtime]
    except KeyError as exc:
        raise RuntimeError(f"unsupported shared turn runtime: {runtime}") from exc


def _shared_state_to_workbench(status: str) -> str:
    return {
        "succeeded": "done",
        "cancelled": "stopped",
        "partial": "done",
        "blocked": "failed",
        "orphaned": "failed",
    }.get(status, status or "unknown")


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def _failed_process(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="shared runtime cli unavailable")
