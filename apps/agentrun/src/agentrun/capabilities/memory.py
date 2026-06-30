"""Memory:接口 + backend 注入(见 design/05 §3)。

内核只定义接口与机制;具体 backend(检索栈/向量库)由调用方注入,且常驻复用不重载。
召回低延迟同步,写入可异步。M3 提供参考用 InMemoryBackend。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentrun.core.run import utc_now

MEMORY_TYPES = ("fact", "preference", "feedback", "reference")


@dataclass
class MemoryItem:
    id: str
    type: str
    content: str
    source: str = ""
    created_at: str = field(default_factory=utc_now)


class MemoryBackend(Protocol):
    def recall(self, query: str, filters: dict[str, Any] | None, top_k: int) -> list[MemoryItem]: ...
    def write(self, items: list[MemoryItem]) -> None: ...
    def forget(self, ids: list[str]) -> None: ...
    def list_sources(self) -> list[dict[str, Any]]: ...


class InMemoryBackend:
    """参考实现:子串关键词召回。真实向量库 backend 由调用方注入。"""

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    def write(self, items: list[MemoryItem]) -> None:
        for it in items:
            self._items[it.id] = it

    def recall(self, query: str, filters: dict[str, Any] | None, top_k: int) -> list[MemoryItem]:
        q = query.lower()
        hits = [it for it in self._items.values() if q in it.content.lower()]
        if filters and "type" in filters:
            hits = [it for it in hits if it.type == filters["type"]]
        return hits[:top_k]

    def forget(self, ids: list[str]) -> None:
        for i in ids:
            self._items.pop(i, None)

    def list_sources(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for it in self._items.values():
            counts[it.source] = counts.get(it.source, 0) + 1
        return [{"source": s, "count": n} for s, n in sorted(counts.items())]


class Memory:
    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend  # 注入,常驻复用

    @property
    def backend(self) -> MemoryBackend:
        return self._backend

    def recall(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[MemoryItem]:
        return self._backend.recall(query, filters, top_k)

    def write(self, items: list[MemoryItem]) -> None:
        self._backend.write(items)

    def forget(self, ids: list[str]) -> None:
        self._backend.forget(ids)

    def list_sources(self) -> list[dict[str, Any]]:
        return self._backend.list_sources()
