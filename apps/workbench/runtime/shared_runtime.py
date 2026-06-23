"""Adapter for the shared runtime CLI, with a local fake fallback for smoke tests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .process_cli_provider import read_json, safe_result_from_text, text_tail, write_json


DEFAULT_SHARED_RUNTIME_CLI = Path("/Users/yang/agents/runtime/scripts/agent-runtime")


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


def _new_run_id() -> str:
    return f"shared-{uuid.uuid4().hex[:12]}"


class SharedRuntimeAdapter:
    """Run a turn through shared runtime when available.

    The local fallback intentionally supports only `fake`; real shared runtime
    execution must go through the explicit CLI so missing shared dependencies do
    not silently masquerade as a production execution.
    """

    def __init__(self, runtime_dir: str | Path, cli_path: str | Path | None = None) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        configured = cli_path or os.environ.get("AGENT_SHARED_RUNTIME_CLI") or DEFAULT_SHARED_RUNTIME_CLI
        self.cli_path = Path(configured)

    def run(self, spec: SharedRuntimeRunSpec) -> dict:
        run_id = spec.run_id or _new_run_id()
        run_dir = (Path(spec.runtime_dir) / run_id).resolve()
        prompt_file = run_dir / "prompt.md"
        result_file = Path(spec.result_file_name)
        result_file = result_file if result_file.is_absolute() else run_dir / result_file
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        run_dir.mkdir(parents=True, exist_ok=False)
        prompt_file.write_text(spec.prompt_text, encoding="utf-8")
        meta = {
            "run_id": run_id,
            "provider_kind": "shared_runtime",
            "runtime": spec.runtime,
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
        started = time.monotonic()

        if spec.runtime == "fake":
            output_log.write_text("[fake] shared runtime fallback\n", encoding="utf-8")
            write_json(result_file, safe_result_from_text("fake runtime ok"))
            write_json(status_file, {**meta, "state": "done", "elapsed_ms": int((time.monotonic() - started) * 1000), "updated_at": now()})
            return self.status(run_id)

        if self.cli_path.exists() and os.access(self.cli_path, os.X_OK):
            argv = [
                str(self.cli_path),
                "task",
                "run",
                "--project",
                "agent",
                "--provider",
                spec.runtime,
                "--prompt-file",
                str(prompt_file),
                "--result-file",
                str(result_file),
                "--id",
                run_id,
                "--cwd",
                str(spec.cwd),
                "--force",
                "--json",
            ]
            proc = subprocess.run(
                argv,
                cwd=spec.cwd,
                text=True,
                capture_output=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
            output_log.write_text("## stdout\n" + proc.stdout + "\n\n## stderr\n" + proc.stderr, encoding="utf-8")
            state = "done" if proc.returncode == 0 and result_file.exists() else "failed"
            if state == "failed" and not result_file.exists():
                write_json(result_file, safe_result_from_text(proc.stderr or proc.stdout, status="failed", errors=[proc.stderr or proc.stdout]))
            write_json(status_file, {**meta, "state": state, "returncode": proc.returncode, "elapsed_ms": int((time.monotonic() - started) * 1000), "updated_at": now()})
            return self.status(run_id)

        message = f"shared runtime cli unavailable: {self.cli_path}"
        output_log.write_text(message + "\n", encoding="utf-8")
        write_json(result_file, safe_result_from_text(message, status="failed", errors=[message]))
        write_json(status_file, {**meta, "state": "failed", "reason": "shared_runtime_missing", "elapsed_ms": int((time.monotonic() - started) * 1000), "updated_at": now()})
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        run_dir = self.runtime_dir / run_id
        status = read_json(run_dir / "status.json", {}) or {}
        meta = read_json(run_dir / "meta.json", {}) or {}
        result_file = Path(meta.get("result_file", run_dir / "result.json"))
        result = read_json(result_file) if result_file.exists() else None
        output_log = Path(meta.get("output_log", run_dir / "output.log"))
        output_bytes = output_log.stat().st_size if output_log.exists() else 0
        return {
            **meta,
            **status,
            "result_exists": result_file.exists(),
            "result_valid": isinstance(result, dict),
            "result": result,
            "output_bytes": output_bytes,
            "updated_at": now(),
        }

    def logs(self, run_id: str, max_bytes: int = 120_000) -> dict:
        run_dir = self.runtime_dir / run_id
        meta = read_json(run_dir / "meta.json", {}) or {}
        text, truncated = text_tail(Path(meta.get("output_log", run_dir / "output.log")), max_bytes)
        return {"run_id": run_id, "text": text, "truncated": truncated}


def shared_runtime_available() -> bool:
    configured = os.environ.get("AGENT_SHARED_RUNTIME_CLI")
    path = Path(configured) if configured else DEFAULT_SHARED_RUNTIME_CLI
    return path.exists() and os.access(path, os.X_OK)
