"""StateManager:loop 状态机(见 design/04 §1)。

与 02 的 run 状态机分属不同层级:这里是 planning→executing→observing→terminal。
终态复用 02 终态词汇(done/failed/blocked/cancelled),语义在 loop 层级。
"""
from __future__ import annotations

from agentrun.core.run import utc_now

PLANNING = "planning"
EXECUTING = "executing"
OBSERVING = "observing"
TERMINAL = "terminal"
PHASES = (PLANNING, EXECUTING, OBSERVING, TERMINAL)

# loop 终态(复用 02 终态词汇)
LOOP_DONE = "done"
LOOP_FAILED = "failed"
LOOP_BLOCKED = "blocked"
LOOP_CANCELLED = "cancelled"
LOOP_TERMINALS = (LOOP_DONE, LOOP_FAILED, LOOP_BLOCKED, LOOP_CANCELLED)


class StateManager:
    def __init__(self) -> None:
        self.phase = PLANNING
        self.outcome: str | None = None
        self.step = 0
        self.transitions: list[dict[str, str]] = []
        self._record(PLANNING)

    def to(self, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"未知 loop phase: {phase}")
        self.phase = phase
        self._record(phase)

    def terminate(self, outcome: str) -> None:
        if outcome not in LOOP_TERMINALS:
            raise ValueError(f"非法 loop 终态: {outcome}")
        self.phase = TERMINAL
        self.outcome = outcome
        self._record(TERMINAL, outcome)

    @property
    def is_terminal(self) -> bool:
        return self.phase == TERMINAL

    def _record(self, phase: str, outcome: str | None = None) -> None:
        self.transitions.append({"phase": phase, "outcome": outcome or "", "at": utc_now()})
