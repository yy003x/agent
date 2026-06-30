"""ToolManager:注册原子工具 + 生成 tool schema;危险/外部工具元数据供 Guardrail/Executor(见 design/05 §2)。

外部进程类工具不自己执行,而返回声明式描述,交 Executor 过 Guardrail 后走 02 run 动词。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[[dict[str, Any]], Any] | None
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)
    dangerous: bool = False
    capability: str | None = None
    kind: str = "function"  # function | external


class ToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        fn: Callable[[dict[str, Any]], Any] | None = None,
        *,
        description: str = "",
        schema: dict[str, Any] | None = None,
        dangerous: bool = False,
        capability: str | None = None,
        kind: str = "function",
    ) -> None:
        if kind == "function" and fn is None:
            raise ValueError(f"function 工具 {name} 必须提供 fn")
        self._tools[name] = Tool(name, fn, description, schema or {}, dangerous, capability, kind)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"未注册工具: {name}")
        return self._tools[name]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "parameters": t.schema} for t in self._tools.values()]

    def as_executor_tools(self) -> dict[str, tuple]:
        """桥接 M2 Executor:{name: (fn, capability)};仅 function 工具。"""
        return {t.name: (t.fn, t.capability) for t in self._tools.values() if t.kind == "function"}

    def describe_external(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """外部进程类工具的声明式描述(交 Executor → Guardrail → 02 run 动词)。"""
        tool = self.get(name)
        if tool.kind != "external":
            raise ValueError(f"{name} 不是 external 工具")
        return {"type": "run_agent", "tool": name, "capability": tool.capability, "request": args}
