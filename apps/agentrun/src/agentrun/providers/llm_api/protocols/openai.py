"""OpenAI-compatible protocol:payload / header / 响应解析。"""
from __future__ import annotations

from typing import Any

ENDPOINT = "/chat/completions"


def build_payload(model: str, prompt: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": prompt}]}


def build_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def extract_text(response: dict[str, Any]) -> str:
    return response["choices"][0]["message"]["content"]
