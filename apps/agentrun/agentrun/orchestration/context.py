"""ContextManager:组装每轮 LLM 输入(纯函数式,见 design/04 §3 / ContextManager)。

给定状态产出 messages,无副作用,便于测试与录制-重放。Memory/RAG 注入在 M3。
"""
from __future__ import annotations

from typing import Any


class ContextManager:
    def __init__(self, system: str = "") -> None:
        self.system = system

    def assemble(
        self,
        user_input: str,
        history: list[dict[str, Any]],
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        for obs in observations:
            messages.append({"role": "observation", "content": obs.get("content", "")})
        return messages
