"""Provider 协议:实现 02 的 run 动词(见 design/03 §0)。"""
from __future__ import annotations

from typing import Any, Protocol

from agentrun.core.config import Profile
from agentrun.core.rundir import RunPaths
from agentrun.core.run import RunRequest


class Provider(Protocol):
    transport: str

    def __init__(self, profile: Profile) -> None: ...

    def run(self, request: RunRequest, paths: RunPaths) -> dict[str, Any]:
        """同步执行 turn/task 直到终态,返回 result.json 内容。"""
        ...
