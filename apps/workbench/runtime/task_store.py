"""Filesystem task-store helpers for runtime runs."""
from __future__ import annotations

from pathlib import Path


class TaskStore:
    """Resolve and list runtime task directories."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def task_dir(self, task_id: str) -> Path:
        return self.root / task_id
