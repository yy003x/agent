"""Runtime state types and shared errors."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class RuntimeErrorState(RuntimeError):
    """Raised when a runtime operation cannot be completed."""


DeliveryMode = Literal["contract", "send"]


@dataclass(frozen=True)
class RuntimeTask:
    """A task handed from the workbench to an external CLI worker."""

    task_id: str
    runtime: str
    prompt_path: Path
    result_path: Path
    work_dir: Path
    command: str | None = None
    runtime_options: dict | None = None


@dataclass(frozen=True)
class WorkerHandle:
    """Stable metadata for a long-lived external CLI worker."""

    task_id: str
    runtime: str
    meta: dict
