"""agentrun:业务无关的本地 Agent 运行时(库优先)。

对外导出门面与核心服务;详见 design/。
"""
from __future__ import annotations

__version__ = "0.1.0"

from agentrun.kernel import AgentRuntime
from agentrun.service import RuntimeService

__all__ = ["AgentRuntime", "RuntimeService", "__version__"]
