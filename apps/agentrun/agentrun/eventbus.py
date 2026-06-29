"""横切:进程内异步事件总线(订阅者失败隔离,见 design/03-orchestration 横切)。

M0 为同步广播 + 失败隔离;事件落盘由 core.events 负责,这里只做进程内分发。
"""
from __future__ import annotations

from typing import Any, Callable

Subscriber = Callable[[str, dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        self._subscribers.append(fn)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        for fn in list(self._subscribers):
            try:
                fn(event_type, data)
            except Exception:  # noqa: BLE001 订阅者失败不回压主流程
                continue
