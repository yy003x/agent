"""Result-file provider for OpenAI-compatible and Anthropic LLM APIs."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import model_backends

from .process_cli_provider import read_json, safe_result_from_text, text_tail, write_json


class LlmApiProviderError(RuntimeError):
    """Raised when an LLM API run cannot be completed."""


@dataclass(frozen=True)
class LlmApiRunSpec:
    prompt_text: str
    cwd: Path
    runtime_dir: Path
    result_file_name: str = "result.json"
    run_id: str | None = None
    timeout_seconds: int = 120
    backend_id: str | None = None
    task: str = "text"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_run_id() -> str:
    return f"llm-{uuid.uuid4().hex[:12]}"


def _result_file(run_dir: Path, name: str) -> Path:
    path = Path(name)
    return path if path.is_absolute() else run_dir / path


def _extract_openai_content(body: Any) -> str:
    try:
        return str(body["choices"][0]["message"]["content"]).strip()
    except Exception:
        return json.dumps(body, ensure_ascii=False)[:4000]


def _extract_anthropic_content(body: Any) -> str:
    parts = []
    for item in body.get("content", []) if isinstance(body, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip() or json.dumps(body, ensure_ascii=False)[:4000]


class LlmApiProvider:
    """Run one model API request per turn."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()

    def run(self, spec: LlmApiRunSpec) -> dict:
        run_id = spec.run_id or new_run_id()
        run_dir = (Path(spec.runtime_dir) / run_id).resolve()
        prompt_file = run_dir / "prompt.md"
        result_file = _result_file(run_dir, spec.result_file_name)
        output_log = run_dir / "output.log"
        status_file = run_dir / "status.json"
        meta_file = run_dir / "meta.json"
        run_dir.mkdir(parents=True, exist_ok=False)
        prompt_file.write_text(spec.prompt_text, encoding="utf-8")

        backend = model_backends.resolve_model_backend(spec.task, spec.backend_id)
        public_backend = {k: v for k, v in backend.items() if k not in {"api_key", "headers"}}
        meta = {
            "run_id": run_id,
            "provider_kind": "llm_api",
            "runtime": "llm_api",
            "cwd": str(Path(spec.cwd).resolve()),
            "run_dir": str(run_dir),
            "prompt_file": str(prompt_file),
            "result_file": str(result_file),
            "output_log": str(output_log),
            "status_file": str(status_file),
            "timeout_seconds": spec.timeout_seconds,
            "backend": public_backend,
            "started_at": now(),
        }
        write_json(meta_file, meta)
        write_json(status_file, {**meta, "state": "running", "updated_at": now()})

        started = time.monotonic()
        try:
            if backend["protocol"] == "anthropic":
                content = self._run_anthropic(backend, spec.prompt_text, spec.timeout_seconds, output_log)
            else:
                content = self._run_openai_compatible(backend, spec.prompt_text, spec.timeout_seconds, output_log)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            write_json(result_file, safe_result_from_text(content))
            write_json(status_file, {**meta, "state": "done", "elapsed_ms": elapsed_ms, "updated_at": now()})
            return self.status(run_id)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - started) * 1000)
            output_log.write_text(f"LLM API failed: {exc}\n", encoding="utf-8")
            write_json(result_file, safe_result_from_text(f"LLM API 调用失败：{exc}", status="failed", errors=[str(exc)]))
            write_json(status_file, {**meta, "state": "failed", "elapsed_ms": elapsed_ms, "updated_at": now()})
            return self.status(run_id)

    def _run_openai_compatible(self, backend: dict, prompt: str, timeout: int, output_log: Path) -> str:
        import requests

        headers = {"Authorization": f"Bearer {backend['api_key']}", "Content-Type": "application/json"}
        headers.update(backend.get("headers") or {})
        payload = {
            "model": backend["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        response = requests.post(backend["chat_url"], headers=headers, json=payload, timeout=timeout)
        body = response.json() if response.content else {}
        output_log.write_text(json.dumps({"status_code": response.status_code, "body": body}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not response.ok:
            raise LlmApiProviderError(f"{backend['id']} HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)[:500]}")
        return _extract_openai_content(body)

    def _run_anthropic(self, backend: dict, prompt: str, timeout: int, output_log: Path) -> str:
        import requests

        headers = {
            "x-api-key": backend["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": backend["model"],
            "max_tokens": int(model_backends._env("ANTHROPIC_MAX_TOKENS", "2048")),
            "messages": [{"role": "user", "content": prompt}],
        }
        url = backend["base_url"].rstrip("/") + "/messages"
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        body = response.json() if response.content else {}
        output_log.write_text(json.dumps({"status_code": response.status_code, "body": body}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not response.ok:
            raise LlmApiProviderError(f"anthropic HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)[:500]}")
        return _extract_anthropic_content(body)

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
