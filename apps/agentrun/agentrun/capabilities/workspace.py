"""WorkspaceManager:工作区根注入 + 路径解析/隔离/清理(见 design/05 §4)。

是 Persistence/Executor 产物路径的唯一来源;多 run 并发目录隔离。
"""
from __future__ import annotations

import shutil
from pathlib import Path


class WorkspaceError(ValueError):
    pass


class WorkspaceManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        """解析 root 下路径,拒绝 .. / 越界。"""
        if any(".." in str(p).split("/") for p in parts):
            raise WorkspaceError(f"路径含 ..,拒绝: {parts}")
        resolved = self.root.joinpath(*parts).resolve()
        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"路径越界: {resolved}")
        return resolved

    def run_workspace(self, run_id: str) -> Path:
        p = self.path("work", run_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def gc(self, run_id: str) -> None:
        p = self.path("work", run_id)
        if p.exists():
            shutil.rmtree(p)
