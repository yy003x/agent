"""上层 SDK client(见 design/08 §3)。不是内核的一部分,也不是调 LLM API 的适配层。"""
from agentrun.sdk.client import AgentRunClient, local_transport

__all__ = ["AgentRunClient", "local_transport"]
