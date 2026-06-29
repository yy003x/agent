"""LoopEngine:驱动 plan→guard→execute→observe→update 主循环(见 design/04 §2)。

终止条件:Planner 判完成 / 步数上限 / 阻断 / 取消。防无限循环是硬约束。
一切执行委托 Executor(→ LLMGateway 或 02 run 动词),本层不自己拉进程。
"""
from __future__ import annotations

from typing import Any, Callable

from agentrun.orchestration.context import ContextManager
from agentrun.orchestration.executor import Executor
from agentrun.orchestration.observer import Observer
from agentrun.orchestration.planner import Planner, PlannerError
from agentrun.orchestration.state import (
    EXECUTING,
    LOOP_BLOCKED,
    LOOP_CANCELLED,
    LOOP_DONE,
    LOOP_FAILED,
    OBSERVING,
    PLANNING,
    StateManager,
)


class LoopEngine:
    def __init__(
        self,
        planner: Planner,
        executor: Executor,
        observer: Observer | None = None,
        context: ContextManager | None = None,
        max_steps: int = 10,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.observer = observer or Observer()
        self.context = context or ContextManager()
        self.max_steps = max_steps
        self._on_event = on_event
        self._cancelled = False

    def cancel(self) -> None:
        """interrupt/cancel 信号:下一次循环顶检查时终止(穿透到正在跑的循环)。"""
        self._cancelled = True

    def run(self, user_input: str) -> dict[str, Any]:
        state = StateManager()
        history: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        final: Any = None

        while True:
            if self._cancelled:
                state.terminate(LOOP_CANCELLED)
                break
            if state.step >= self.max_steps:
                state.terminate(LOOP_FAILED)  # 防无限循环
                break

            state.to(PLANNING)
            messages = self.context.assemble(user_input, history, observations)
            try:
                action = self.planner.next_action(messages)
            except PlannerError as exc:
                self._emit("planner.invalid", {"error": str(exc)})
                state.terminate(LOOP_FAILED)
                break
            self._emit("planner.action", {"type": action["type"]})

            if action["type"] == "respond":
                final = action["content"]
                state.terminate(LOOP_DONE)
                break

            state.to(EXECUTING)
            exec_result = self.executor.execute(action)
            state.to(OBSERVING)
            signal = self.observer.observe(action, exec_result)
            observations.append(signal)
            self._emit("observe", {"kind": signal["kind"]})

            if signal["kind"] == "blocked":
                state.terminate(LOOP_BLOCKED)
                break
            state.step += 1

        return {
            "loop_state": state.phase,
            "outcome": state.outcome,
            "output": final,
            "steps": state.step,
            "transitions": state.transitions,
        }

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event(event_type, data)
