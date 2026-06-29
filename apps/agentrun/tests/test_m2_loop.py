"""M2 编排:loop 状态机、录制-重放、cancel 穿透、防无限循环、guardrail、两路径。"""
from __future__ import annotations

import unittest

from agentrun.capabilities.llm_gateway import LLMGateway
from agentrun.guardrail import Guardrail
from agentrun.orchestration.executor import Executor
from agentrun.orchestration.loop import LoopEngine
from agentrun.orchestration.planner import Planner


class Scripted:
    """录制好的 model:按顺序吐 action;越界则重复最后一个。"""

    def __init__(self, actions: list[dict]) -> None:
        self.actions = actions
        self.calls = 0

    def __call__(self, messages):  # noqa: ANN001
        a = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        return a


def _engine(model, tools=None, guardrail=None, agent_runner=None, max_steps=10):
    planner = Planner(LLMGateway(model))
    executor = Executor(tools=tools, guardrail=guardrail, agent_runner=agent_runner)
    return LoopEngine(planner, executor, max_steps=max_steps)


class LoopTest(unittest.TestCase):
    def test_state_machine_and_done(self) -> None:
        model = Scripted([{"type": "tool", "name": "echo", "args": {"x": 1}}, {"type": "respond", "content": "final"}])
        eng = _engine(model, tools={"echo": (lambda a: a["x"], None)})
        out = eng.run("hi")
        self.assertEqual(out["loop_state"], "terminal")
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["output"], "final")
        self.assertEqual(out["steps"], 1)
        phases = [t["phase"] for t in out["transitions"]]
        self.assertIn("executing", phases)
        self.assertIn("observing", phases)
        self.assertEqual(phases[-1], "terminal")

    def test_record_replay_deterministic(self) -> None:
        def build():
            m = Scripted([{"type": "tool", "name": "echo", "args": {"x": 9}}, {"type": "respond", "content": "ok"}])
            return _engine(m, tools={"echo": (lambda a: a["x"], None)})

        a = build().run("q")
        b = build().run("q")
        self.assertEqual(a["output"], b["output"])
        self.assertEqual([t["phase"] for t in a["transitions"]], [t["phase"] for t in b["transitions"]])

    def test_cancel_before_run(self) -> None:
        eng = _engine(Scripted([{"type": "respond", "content": "x"}]))
        eng.cancel()
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "cancelled")
        self.assertEqual(out["steps"], 0)

    def test_cancel_propagates_mid_loop(self) -> None:
        holder: dict = {}

        def cancel_tool(args):  # noqa: ANN001
            holder["engine"].cancel()
            return "cancelling"

        model = Scripted([{"type": "tool", "name": "cancel", "args": {}}, {"type": "respond", "content": "never"}])
        eng = _engine(model, tools={"cancel": (cancel_tool, None)})
        holder["engine"] = eng
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "cancelled")
        self.assertNotEqual(out["output"], "never")  # respond 未到达

    def test_max_steps_prevents_infinite_loop(self) -> None:
        model = Scripted([{"type": "tool", "name": "noop", "args": {}}])  # 永远 tool
        eng = _engine(model, tools={"noop": (lambda a: 1, None)}, max_steps=3)
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["steps"], 3)

    def test_guardrail_blocks_dangerous_tool(self) -> None:
        model = Scripted([{"type": "tool", "name": "danger", "args": {}}])
        g = Guardrail(capabilities=set())  # 不授予
        eng = _engine(model, tools={"danger": (lambda a: "boom", "remote_write")}, guardrail=g)
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "blocked")

    def test_guardrail_allows_with_capability(self) -> None:
        model = Scripted([{"type": "tool", "name": "danger", "args": {}}, {"type": "respond", "content": "done"}])
        g = Guardrail(capabilities={"remote_write"})
        eng = _engine(model, tools={"danger": (lambda a: "ok", "remote_write")}, guardrail=g)
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "done")

    def test_run_agent_path(self) -> None:
        calls = []
        model = Scripted([{"type": "run_agent", "request": {"profile": "codex-cli"}}, {"type": "respond", "content": "fin"}])
        eng = _engine(model, agent_runner=lambda req: calls.append(req) or {"state": "done"})
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(calls, [{"profile": "codex-cli"}])

    def test_run_agent_capability_blocked(self) -> None:
        calls = []
        model = Scripted([{"type": "run_agent", "capability": "shell", "request": {"profile": "codex-cli"}}])
        eng = _engine(model, guardrail=Guardrail(capabilities=set()), agent_runner=lambda req: calls.append(req))
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "blocked")
        self.assertEqual(calls, [])

    def test_planner_invalid_action_fails(self) -> None:
        eng = _engine(Scripted([{"type": "bogus"}]))
        out = eng.run("hi")
        self.assertEqual(out["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
