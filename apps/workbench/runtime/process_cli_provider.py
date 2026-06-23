"""Structured subprocess provider for non-interactive Codex/Claude CLI calls."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class ProcessCliProviderError(RuntimeError):
    """Raised when a structured CLI run cannot be completed."""


@dataclass(frozen=True)
class ProcessCliRunSpec:
    runtime: str
    argv: list[str]
    cwd: Path
    runtime_dir: Path
    prompt_text: str
    result_file_name: str = "result.json"
    run_id: str | None = None
    timeout_seconds: int = 300
    env: dict[str, str] = field(default_factory=dict)
    output_mode: str = "text"
    last_message_file_name: str = "last-message.txt"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_run_id(prefix: str = "proc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


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


def command_display(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def safe_result_from_text(text: str, *, status: str = "success", errors: list[str] | None = None) -> dict:
    text = (text or "").strip()
    return {
        "status": status,
        "assistant_message": text or "（模型未返回内容）",
        "summary": text[:160] if text else "structured CLI run finished",
        "outputs": [],
        "questions": [],
        "errors": errors or [],
    }


def parse_claude_json(stdout: str) -> str:
    body = json.loads(stdout)
    for key in ("result", "content", "text", "response"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = body.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                item["text"]
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if parts:
                return "\n".join(parts).strip()
    return stdout.strip()


class ProcessCliProvider:
    """Run one CLI process per turn or diagnostic run."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()

    def run(self, spec: ProcessCliRunSpec) -> dict:
        if not spec.argv:
            raise ProcessCliProviderError("process CLI argv must not be empty")
        executable = spec.argv[0]
        if not shutil.which(executable):
            raise ProcessCliProviderError(f"{executable} not found")

        run_id = spec.run_id or new_run_id()
        run_dir = (Path(spec.runtime_dir) / run_id).resolve()
        prompt_file = run_dir / "prompt.md"
        result_file = result_file_path(run_dir, spec.result_file_name)
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        argv_file = run_dir / "argv.json"
        last_message_file = run_dir / spec.last_message_file_name
        cwd = Path(spec.cwd).resolve()

        run_dir.mkdir(parents=True, exist_ok=False)
        prompt_file.write_text(spec.prompt_text, encoding="utf-8")
        write_json(argv_file, {"argv": spec.argv})
        meta = {
            "run_id": run_id,
            "provider_kind": "process_cli",
            "runtime": spec.runtime,
            "cwd": str(cwd),
            "argv": spec.argv,
            "command": command_display(spec.argv),
            "run_dir": str(run_dir),
            "prompt_file": str(prompt_file),
            "result_file": str(result_file),
            "output_log": str(output_log),
            "status_file": str(status_file),
            "argv_file": str(argv_file),
            "last_message_file": str(last_message_file),
            "timeout_seconds": spec.timeout_seconds,
            "started_at": now(),
        }
        write_json(meta_file, meta)
        write_json(status_file, {**meta, "state": "running", "updated_at": now()})

        env = dict(os.environ)
        env.update(spec.env or {})
        env.setdefault("AGENT_WORKBENCH_RESULT_FILE", str(result_file))
        env.setdefault("AGENT_WORKBENCH_PROMPT_FILE", str(prompt_file))

        started = time.monotonic()
        try:
            proc = subprocess.run(
                spec.argv,
                cwd=cwd,
                env=env,
                input=spec.prompt_text,
                text=True,
                capture_output=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            output_log.write_text(
                "## stdout\n" + (proc.stdout or "") + "\n\n## stderr\n" + (proc.stderr or ""),
                encoding="utf-8",
            )
            if result_file.exists() and isinstance(read_json(result_file), dict):
                state = "done" if proc.returncode == 0 else "failed"
                write_json(status_file, {**meta, "state": state, "returncode": proc.returncode, "elapsed_ms": elapsed_ms, "updated_at": now()})
                return self.status(run_id)

            if spec.output_mode == "codex_last_message" and last_message_file.exists():
                content = last_message_file.read_text(encoding="utf-8", errors="ignore")
            elif spec.output_mode == "claude_json" and (proc.stdout or "").strip():
                try:
                    content = parse_claude_json(proc.stdout)
                except Exception:
                    content = proc.stdout
            else:
                content = proc.stdout or proc.stderr
            result_status = "success" if proc.returncode == 0 else "failed"
            errors = [] if proc.returncode == 0 else [(proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()]
            write_json(result_file, safe_result_from_text(content, status=result_status, errors=errors))
            write_json(status_file, {**meta, "state": "done" if proc.returncode == 0 else "failed", "returncode": proc.returncode, "elapsed_ms": elapsed_ms, "updated_at": now()})
            return self.status(run_id)
        except subprocess.TimeoutExpired as exc:
            output_log.write_text((exc.stdout or "") + "\n\n## timeout\n" + str(exc), encoding="utf-8", errors="replace")
            write_json(result_file, safe_result_from_text(f"CLI 调用超时：{spec.timeout_seconds}s", status="failed", errors=[str(exc)]))
            write_json(status_file, {**meta, "state": "failed", "reason": "timeout", "updated_at": now()})
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
