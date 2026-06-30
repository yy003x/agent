#!/usr/bin/env python3
"""Smoke test OpenAI-compatible model backends without exposing API keys."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "design" / "README.md"
DEFAULT_CONFIG = ROOT / "model_tests.json"
LOCAL_ENV_FILE = ROOT / ".env"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
PLACEHOLDER_MARKERS = ("xxxx", "your_", "changeme")


def load_local_env() -> None:
    if not LOCAL_ENV_FILE.exists():
        return
    for raw_line in LOCAL_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


def chat_url(base_url: str, provider: str | None = None) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if provider == "openrouter" and cleaned.endswith("/api"):
        return f"{cleaned}/v1/chat/completions"
    return f"{cleaned}/chat/completions"


def default_model_configs() -> list[dict[str, Any]]:
    openrouter_url = chat_url(os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"), "openrouter")
    openrouter_title = os.getenv("OPENROUTER_TITLE", "agent-model-test")
    return [
        {
            "name": os.getenv("OPENROUTER_NAME", "openrouter"),
            "provider": "openrouter",
            "url": openrouter_url,
            "api_key_env": "OPENROUTER_API_KEY",
            "model": os.getenv("OPENROUTER_MODEL", "z-ai/glm-5.1"),
            "headers": {
                "HTTP-Referer": "${OPENROUTER_HTTP_REFERER}",
                "X-OpenRouter-Title": openrouter_title,
            },
        },
    ]


def load_model_configs(config_path: Path) -> tuple[str, list[dict[str, Any]]]:
    if not config_path.exists():
        return "env-defaults", default_model_configs()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return str(config_path), data
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        return str(config_path), data["models"]
    raise ValueError(f"配置格式错误：{config_path} 必须是数组，或包含 models 数组")


def load_content(source_arg: str | None) -> str:
    source = Path(source_arg) if source_arg else DEFAULT_SOURCE
    if not source.exists():
        raise FileNotFoundError(
            f"输入文件不存在：{source}\n"
            "请传入要总结的文件，例如：python3 scripts/model_backend_smoke.py design/README.md"
        )
    return source.read_text(encoding="utf-8")


def expand_env(value: str) -> str:
    return ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)


def secret_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    normalized = value.lower()
    if (
        not normalized
        or (normalized.startswith("<") and normalized.endswith(">"))
        or any(marker in normalized for marker in PLACEHOLDER_MARKERS)
    ):
        return ""
    return value


def has_key(config: dict[str, Any]) -> bool:
    key_env = str(config.get("api_key_env", ""))
    return bool(key_env and secret_env(key_env))


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": config.get("name") or config.get("model") or "unnamed",
        "provider": config.get("provider", ""),
        "url": chat_url(str(config.get("url", "")), str(config.get("provider", ""))) if config.get("url") else "",
        "api_key_env": config.get("api_key_env", ""),
        "api_key_set": has_key(config),
        "model": config.get("model", ""),
    }


def build_headers(config: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    api_key_env = str(config.get("api_key_env", ""))
    api_key = secret_env(api_key_env) if api_key_env else None
    if not api_key:
        return None, f"缺少环境变量 {api_key_env or '<api_key_env>'}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for key, value in dict(config.get("headers", {})).items():
        header_value = expand_env(str(value)).strip()
        if header_value:
            headers[str(key)] = header_value
    return headers, None


def extract_content(response_json: Any) -> str | None:
    try:
        return response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def run_one(config: dict[str, Any], messages: list[dict[str, str]], timeout: float, raw: bool) -> dict[str, Any]:
    import requests

    name = str(config.get("name") or config.get("model") or "unnamed")
    model = str(config.get("model", ""))
    provider = str(config.get("provider", ""))
    raw_url = str(config.get("url", "")).strip()
    url = chat_url(raw_url, provider) if raw_url else ""
    started = time.perf_counter()
    headers, header_error = build_headers(config)
    if header_error:
        return {"name": name, "model": model, "ok": False, "skipped": True, "error": header_error}
    if not url or not model:
        return {"name": name, "model": model, "ok": False, "skipped": True, "error": "缺少 url 或 model"}

    payload = {"model": model, "messages": messages, "temperature": float(config.get("temperature", 0.7))}
    payload.update(dict(config.get("extra_body", {})))
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        result = {
            "name": name,
            "provider": provider,
            "model": model,
            "ok": response.ok,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "content": extract_content(body),
            "usage": body.get("usage") if isinstance(body, dict) else None,
        }
        if not response.ok:
            result["error"] = body
        if raw:
            result["response"] = body
        return result
    except requests.RequestException as exc:
        return {
            "name": name,
            "provider": provider,
            "model": model,
            "ok": False,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试 OpenRouter 模型后端")
    parser.add_argument("source", nargs="?", help="可选：要总结的本地文本文件")
    parser.add_argument("--config", default=os.getenv("MODEL_TEST_CONFIG", str(DEFAULT_CONFIG)))
    parser.add_argument("--prompt", default=os.getenv("MODEL_TEST_PROMPT"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("MODEL_TEST_TIMEOUT_S", "60")))
    parser.add_argument("--raw", action="store_true", help="输出每个 provider 的完整原始响应")
    parser.add_argument("--require-all", action="store_true", help="有模型缺 key 或失败时返回非 0")
    parser.add_argument("--list", action="store_true", help="只列出解析后的模型配置，不发请求、不显示 key")
    parser.add_argument("--output", help="把测试报告写入 JSON 文件")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    try:
        config_source, configs = load_model_configs(Path(args.config))
        if args.list:
            print(json.dumps({"config_source": config_source, "models": [public_config(c) for c in configs]}, indent=2, ensure_ascii=False))
            return 0
        prompt = args.prompt or f"总结一下这个内容说了什么：\n\n{load_content(args.source)}"
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2

    messages = [{"role": "user", "content": prompt}]
    results = [run_one(config, messages, args.timeout, args.raw) for config in configs]
    summary = {
        "config_source": config_source,
        "total": len(results),
        "ok": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok") and not item.get("skipped")),
        "skipped": sum(1 for item in results if item.get("skipped")),
        "results": results,
    }
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if args.require_all and summary["ok"] != summary["total"]:
        return 1
    if summary["failed"] > 0 or (summary["ok"] == 0 and summary["skipped"] == 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
