"""Anthropic 原生 protocol:payload / header / 响应解析。"""
from __future__ import annotations

from typing import Any

ENDPOINT = "/messages"
ANTHROPIC_VERSION = "2023-06-01"


def build_payload(model: str, prompt: str) -> dict[str, Any]:
    return {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}


def build_headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION, "Content-Type": "application/json"}


def extract_text(response: dict[str, Any]) -> str:
    return response["content"][0]["text"]
