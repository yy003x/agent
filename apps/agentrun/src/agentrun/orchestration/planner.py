"""Planner:经 LLMGateway 产出结构化、可校验的下一步(见 design/04 §3)。

只决策不执行、不碰副作用;输出 action 必须结构化可校验,解析失败可被上层纠偏。
策略默认最简单单步(由注入的 gateway backend 决定具体决策)。
"""
from __future__ import annotations

from typing import Any

from agentrun.capabilities.llm_gateway import LLMGateway

ACTION_TYPES = ("respond", "tool", "run_agent")


class PlannerError(ValueError):
    pass


class Planner:
    def __init__(self, gateway: LLMGateway) -> None:
        self.gateway = gateway

    def next_action(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        action = self.gateway.complete(messages)
        return self._validate(action)

    @staticmethod
    def _validate(action: Any) -> dict[str, Any]:
        if not isinstance(action, dict):
            raise PlannerError(f"action 必须是 dict,得到 {type(action).__name__}")
        atype = action.get("type")
        if atype not in ACTION_TYPES:
            raise PlannerError(f"非法 action.type: {atype!r}(支持 {ACTION_TYPES})")
        if atype == "respond" and "content" not in action:
            raise PlannerError("respond 缺少 content")
        if atype == "tool" and "name" not in action:
            raise PlannerError("tool 缺少 name")
        if atype == "run_agent" and "request" not in action:
            raise PlannerError("run_agent 缺少 request")
        return action
