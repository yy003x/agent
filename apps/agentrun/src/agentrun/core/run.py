"""run 对象 + run_type + run 状态机(见 design/02 §1-§3)。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CONTRACT_VERSION = 1

# run_type
SESSION = "session"
TURN = "turn"
TASK = "task"
RUN_TYPES = (SESSION, TURN, TASK)

# run 生命周期状态(status.json 的 state)
PENDING = "pending"
RUNNING = "running"
RESULT_PENDING = "result_pending"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"
CANCELLED = "cancelled"
RUN_STATES = (PENDING, RUNNING, RESULT_PENDING, DONE, FAILED, BLOCKED, CANCELLED)
TERMINAL_STATES = (DONE, FAILED, BLOCKED, CANCELLED)

# 失败原因(state == failed 时;见 design/06 §B.2)
FAILURE_REASONS = (
    "timeout",
    "exited",
    "result_missing",
    "schema_invalid",
    "interrupted",
    "provider_error",
)

# result.json 的 outcome(工作质量,见 design/06 §B.3)
OUTCOMES = ("succeeded", "failed", "blocked", "partial", "cancelled")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id(run_type: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{run_type}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class RunRequest:
    """一次 run 的输入(落 request.json,见 design/02 §2)。"""

    run_type: str
    run_id: str
    provider_profile: str
    provider: str = ""
    project_id: str = "_default"
    caller: str = ""
    cwd: Path | None = None
    prompt_file: Path | None = None
    deadline_seconds: int = 0
    result_file: Path | None = None
    result_schema: str = ""
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    contract_version: int = CONTRACT_VERSION
    runtime_version: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "contract_version": self.contract_version,
            "runtime_version": self.runtime_version,
            "project_id": self.project_id,
            "run_type": self.run_type,
            "run_id": self.run_id,
            "caller": self.caller,
            "provider_profile": self.provider_profile,
            "provider": self.provider,
            "cwd": str(self.cwd) if self.cwd else None,
            "prompt_file": str(self.prompt_file) if self.prompt_file else None,
            "deadline_seconds": self.deadline_seconds,
            "result_file": str(self.result_file) if self.result_file else None,
            "result_schema": self.result_schema,
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "created_at": self.created_at,
            "updated_at": utc_now(),
        }


def monotonic() -> float:
    return time.monotonic()


def check_contract_version(data: dict) -> None:
    """读取端按固定契约版本解析。"""
    v = data.get("contract_version")
    if v is not None and v != CONTRACT_VERSION:
        raise ValueError(
            f"contract_version 不匹配:期望 {CONTRACT_VERSION},得到 {v}(请用匹配版本读取)"
        )
