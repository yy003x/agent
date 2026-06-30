"""Common workflow state helpers.

The workflow layer stores explicit run state under ``runs/workflows`` so the
API and UI can resume human-gated flows without parsing skill Markdown.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[4]
RUNS_ROOT = ROOT / "runs" / "workflows"

WorkflowStatus = Literal["running", "waiting", "completed", "failed", "cancelled"]
StepStatus = Literal["pending", "running", "waiting", "completed", "failed", "skipped"]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def safe_run_id(run_id: str, prefix: str) -> str:
    if not re.fullmatch(rf"{re.escape(prefix)}-[a-z0-9]{{12}}", run_id):
        raise ValueError("invalid workflow run id")
    return run_id


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


@dataclass
class WorkflowStep:
    id: str
    title: str
    owner: str
    status: StepStatus = "pending"
    gates: list[str] = field(default_factory=list)
    message: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=now)

    def patch(self, *, status: StepStatus | None = None, message: str | None = None,
              artifacts: dict[str, Any] | None = None) -> None:
        if status is not None:
            self.status = status
        if message is not None:
            self.message = message
        if artifacts:
            self.artifacts.update(artifacts)
        self.updated_at = now()


@dataclass
class WorkflowState:
    workflow: str
    run_id: str
    status: WorkflowStatus
    current_step: str
    created_at: str
    updated_at: str
    inputs: dict[str, Any]
    run_dir: str
    steps: list[WorkflowStep]
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def step(self, step_id: str) -> WorkflowStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"unknown step: {step_id}")

    def as_dict(self) -> dict:
        data = asdict(self)
        data["steps"] = [asdict(step) for step in self.steps]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowState":
        return cls(
            workflow=data["workflow"],
            run_id=data["run_id"],
            status=data["status"],
            current_step=data["current_step"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            inputs=dict(data.get("inputs") or {}),
            run_dir=data["run_dir"],
            steps=[WorkflowStep(**step) for step in data.get("steps", [])],
            outputs=dict(data.get("outputs") or {}),
            errors=list(data.get("errors") or []),
        )
