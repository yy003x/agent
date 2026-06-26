"""Model backend inventory and resolver for diagnostics and LLM API runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV_FILE = ROOT / ".env"
PLACEHOLDER_MARKERS = ("xxxx", "your_", "changeme")


def _load_local_env() -> None:
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


def _env(name: str, default: str = "") -> str:
    _load_local_env()
    return os.getenv(name, default)


def _is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or (normalized.startswith("<") and normalized.endswith(">"))
        or any(marker in normalized for marker in PLACEHOLDER_MARKERS)
    )


def _secret_env(name: str) -> str:
    value = _env(name).strip()
    return "" if _is_placeholder_secret(value) else value


def chat_url(base_url: str, provider: str | None = None) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions") or provider == "anthropic":
        return cleaned
    if provider == "openrouter" and cleaned.endswith("/api"):
        return f"{cleaned}/v1/chat/completions"
    return f"{cleaned}/chat/completions"


BACKEND_SPECS: list[dict[str, Any]] = [
    {
        "id": "dashscope-text",
        "label": "DashScope 文本",
        "provider": "dashscope",
        "protocol": "openai",
        "task": "text",
        "key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "DASHSCOPE_TEXT_MODEL",
        "model": "qwen3.6-plus",
    },
    {
        "id": "dashscope-vision",
        "label": "DashScope 视觉",
        "provider": "dashscope",
        "protocol": "openai",
        "task": "vision",
        "key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "DASHSCOPE_VISION_MODEL",
        "model": "qwen-vl-max",
    },
    {
        "id": "aliyun-qwen-1",
        "label": "阿里云 Qwen 1",
        "provider": "aliyun",
        "protocol": "openai",
        "task": "text",
        "key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "ALIYUN_QWEN_MODEL_1",
        "model": "qwen-plus",
    },
    {
        "id": "aliyun-qwen-2",
        "label": "阿里云 Qwen 2",
        "provider": "aliyun",
        "protocol": "openai",
        "task": "vision",
        "key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "ALIYUN_QWEN_MODEL_2",
        "model": "qwen-vl-max",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "provider": "openrouter",
        "protocol": "openai",
        "task": "text",
        "key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
        "base_url": "https://openrouter.ai/api",
        "model_env": "OPENROUTER_MODEL",
        "model": "openai/gpt-4o-mini",
        "headers": {
            "HTTP-Referer": "OPENROUTER_HTTP_REFERER",
            "X-OpenRouter-Title": "OPENROUTER_TITLE",
        },
    },
    {
        "id": "zhipu-glm",
        "label": "智谱 BigModel",
        "provider": "zhipu",
        "protocol": "openai",
        "task": "text",
        "key_env": "ZHIPUAI_API_KEY",
        "base_url_env": "ZHIPUAI_BASE_URL",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model_env": "ZHIPUAI_MODEL",
        "model": "glm-4-flash",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "provider": "openai",
        "protocol": "openai",
        "task": "text",
        "key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url": "https://api.openai.com/v1",
        "model_env": "OPENAI_MODEL",
        "model": "gpt-4o-mini",
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "provider": "anthropic",
        "protocol": "anthropic",
        "task": "text",
        "key_env": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "base_url": "https://api.anthropic.com/v1",
        "model_env": "ANTHROPIC_MODEL",
        "model": "claude-3-5-haiku-latest",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "provider": "deepseek",
        "protocol": "openai",
        "task": "text",
        "key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "model": "deepseek-chat",
    },
]


def _resolved_public(spec: dict[str, Any]) -> dict[str, Any]:
    key_env = spec["key_env"]
    base_url = _env(spec["base_url_env"], spec["base_url"])
    provider = spec["provider"]
    model = _env(spec["model_env"], spec["model"])
    key_set = bool(_secret_env(key_env))
    return {
        "id": spec["id"],
        "label": spec["label"],
        "provider": provider,
        "protocol": spec["protocol"],
        "task": spec["task"],
        "status": "ok" if key_set else "warn",
        "detail": "api key configured" if key_set else f"{key_env} not set",
        "key_env": key_env,
        "key_set": key_set,
        "base_url": base_url,
        "chat_url": chat_url(base_url, provider),
        "model": model,
        "model_env": spec["model_env"],
    }


def collect_model_backends() -> list[dict[str, Any]]:
    return [_resolved_public(spec) for spec in BACKEND_SPECS]


def resolve_model_backend(task: str = "text", backend_id: str | None = None) -> dict[str, Any]:
    requested = backend_id or _env("AGENT_WORKBENCH_LLM_API_BACKEND", "")
    candidates = BACKEND_SPECS
    if requested:
        candidates = [
            spec for spec in BACKEND_SPECS
            if requested in {spec["id"], spec["provider"], spec["label"]}
        ]
    if not candidates:
        raise ValueError(f"unknown model backend: {requested}")
    task_candidates = [spec for spec in candidates if spec.get("task") == task] or candidates
    public = _resolved_public(task_candidates[0])
    api_key = _secret_env(public["key_env"])
    if not api_key:
        raise RuntimeError(f"缺少环境变量 {public['key_env']}")
    headers = {}
    for header, env_name in dict(task_candidates[0].get("headers", {})).items():
        value = _env(env_name)
        if value:
            headers[header] = value
    return {**public, "api_key": api_key, "headers": headers}
