"""protocol 适配:openai / openai-compatible / anthropic(见 design/03 §2)。"""
from __future__ import annotations

from agentrun.providers.llm_api.protocols import anthropic, openai

_PROTOCOLS = {"openai": openai, "openai-compatible": openai, "anthropic": anthropic}


def get_protocol(name: str):
    if name not in _PROTOCOLS:
        raise ValueError(f"未知 protocol: {name}(支持 {list(_PROTOCOLS)})")
    return _PROTOCOLS[name]
