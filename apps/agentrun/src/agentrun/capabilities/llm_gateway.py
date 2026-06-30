"""LLMGateway:面向编排层的统一"调模型"接口(见 design/01 澄清节 / design/05)。

provider 路由 + 协议适配 + 常驻客户端管理;一个 backend 是 api provider。
本类对编排层暴露 complete();backend 可注入(测试用 stub,真实用 api),
满足 design/06 D「LLMGateway 可注入 stub」。
"""
from __future__ import annotations

from typing import Any, Callable

# complete: messages -> action dict(由 backend 决定如何产出)
CompleteFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


class LLMGateway:
    def __init__(self, complete_fn: CompleteFn) -> None:
        self._complete_fn = complete_fn

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return self._complete_fn(messages)
