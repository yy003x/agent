"""Executor:执行 Planner 决策,执行前必过 Guardrail(见 design/04 §3 / design/01 澄清)。

两条互斥路径:
- tool:调注入的工具(危险工具经 Guardrail 授权);
- run_agent:跑外部 agent —— 委托 02 的 run 动词(注入 agent_runner),**不自己拉进程**。
不存在"直接 new provider"或绕过 02。
"""
from __future__ import annotations

from typing import Any, Callable

from agentrun.guardrail import Guardrail, GuardrailError

# 工具登记:name -> (fn, 需要的 capability 或 None)
ToolSpec = tuple[Callable[[dict[str, Any]], Any], str | None]


class Executor:
    def __init__(
        self,
        tools: dict[str, ToolSpec] | None = None,
        guardrail: Guardrail | None = None,
        agent_runner: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.tools = tools or {}
        self.guardrail = guardrail
        self.agent_runner = agent_runner

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        atype = action["type"]
        if atype == "tool":
            return self._run_tool(action)
        if atype == "run_agent":
            return self._run_agent(action)
        raise ValueError(f"Executor 不处理 action.type={atype}(respond 由 LoopEngine 处理)")

    def _run_tool(self, action: dict[str, Any]) -> dict[str, Any]:
        name = action["name"]
        spec = self.tools.get(name)
        if spec is None:
            return {"status": "error", "output": f"未注册工具: {name}"}
        fn, capability = spec
        if capability:
            if self.guardrail is None:
                return {"status": "blocked", "output": f"工具 {name} 需 capability {capability},无 Guardrail"}
            try:
                self.guardrail.require(capability)
            except GuardrailError as exc:
                return {"status": "blocked", "output": str(exc)}
        try:
            return {"status": "ok", "output": fn(action.get("args", {}))}
        except Exception as exc:  # noqa: BLE001 工具执行错落 observation,不崩主循环
            return {"status": "error", "output": f"{type(exc).__name__}: {exc}"}

    def _run_agent(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.agent_runner is None:
            return {"status": "error", "output": "未注入 agent_runner(02 run 动词)"}
        capability = action.get("capability")
        if capability:
            if self.guardrail is None:
                return {"status": "blocked", "output": f"run_agent 需 capability {capability},无 Guardrail"}
            try:
                self.guardrail.require(str(capability))
            except GuardrailError as exc:
                return {"status": "blocked", "output": str(exc)}
        try:
            return {"status": "ok", "output": self.agent_runner(action["request"])}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "output": f"{type(exc).__name__}: {exc}"}
