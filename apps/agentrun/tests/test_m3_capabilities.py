"""M3 能力体系:Skill 加载隔离/路由、Tool+Guardrail、Memory 注入、Workspace。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentrun.capabilities import (
    InMemoryBackend,
    Memory,
    MemoryItem,
    SkillManager,
    ToolManager,
    WorkspaceManager,
)
from agentrun.guardrail import Guardrail
from agentrun.orchestration.executor import Executor


class SkillTest(unittest.TestCase):
    def test_load_isolation_and_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "good.skill.yaml").write_text(
                "name: summarize\ndescription: 摘要\nkeywords: [摘要, summary]\n", encoding="utf-8"
            )
            (d / "bad.skill.yaml").write_text("description: 缺 name\n", encoding="utf-8")  # 坏技能
            sm = SkillManager()
            sm.register_dir(d)
            doc = sm.doctor()
            self.assertTrue(doc["ok"])  # 内核没被坏技能拖垮
            self.assertEqual(doc["loaded"], 1)
            self.assertEqual(len(doc["errors"]), 1)

    def test_keyword_routing(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "s.skill.yaml").write_text("name: sum\ndescription: x\nkeywords: [摘要]\n", encoding="utf-8")
            sm = SkillManager()
            sm.register_dir(d)
            self.assertEqual(sm.route("帮我做个摘要").name, "sum")
            self.assertIsNone(sm.route("无关查询"))


class ToolGuardrailTest(unittest.TestCase):
    def test_dangerous_tool_blocked_without_capability(self) -> None:
        tm = ToolManager()
        tm.register("rm", lambda a: "deleted", dangerous=True, capability="delete")
        executor = Executor(tools=tm.as_executor_tools(), guardrail=Guardrail(capabilities=set()))
        out = executor.execute({"type": "tool", "name": "rm", "args": {}})
        self.assertEqual(out["status"], "blocked")

    def test_dangerous_tool_ok_with_capability(self) -> None:
        tm = ToolManager()
        tm.register("rm", lambda a: "deleted", dangerous=True, capability="delete")
        executor = Executor(tools=tm.as_executor_tools(), guardrail=Guardrail(capabilities={"delete"}))
        out = executor.execute({"type": "tool", "name": "rm", "args": {}})
        self.assertEqual(out["status"], "ok")

    def test_tool_schemas(self) -> None:
        tm = ToolManager()
        tm.register("echo", lambda a: a, description="回声", schema={"type": "object"})
        schemas = tm.tool_schemas()
        self.assertEqual(schemas[0]["name"], "echo")
        self.assertEqual(schemas[0]["description"], "回声")

    def test_external_tool_returns_descriptor(self) -> None:
        tm = ToolManager()
        tm.register("subrun", None, kind="external", capability="shell")
        desc = tm.describe_external("subrun", {"profile": "codex-cli"})
        self.assertEqual(desc["type"], "run_agent")
        self.assertEqual(desc["capability"], "shell")


class MemoryTest(unittest.TestCase):
    def test_inject_write_recall_forget(self) -> None:
        backend = InMemoryBackend()
        mem = Memory(backend)
        self.assertIs(mem.backend, backend)  # 注入复用,不重建
        mem.write([
            MemoryItem(id="1", type="fact", content="天空是蓝色", source="kb-a"),
            MemoryItem(id="2", type="preference", content="喜欢简洁", source="kb-b"),
        ])
        hits = mem.recall("蓝色")
        self.assertEqual([h.id for h in hits], ["1"])
        self.assertEqual(mem.list_sources(), [{"source": "kb-a", "count": 1}, {"source": "kb-b", "count": 1}])
        mem.forget(["1"])
        self.assertEqual(mem.recall("蓝色"), [])

    def test_filter_by_type(self) -> None:
        mem = Memory(InMemoryBackend())
        mem.write([
            MemoryItem(id="1", type="fact", content="x事实"),
            MemoryItem(id="2", type="preference", content="x偏好"),
        ])
        hits = mem.recall("x", filters={"type": "preference"})
        self.assertEqual([h.id for h in hits], ["2"])


class WorkspaceTest(unittest.TestCase):
    def test_path_within_and_run_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            ws = WorkspaceManager(t)
            p = ws.path("a", "b.txt")
            self.assertTrue(p.is_relative_to(ws.root))
            run = ws.run_workspace("run-1")
            self.assertTrue(run.exists())
            ws.gc("run-1")
            self.assertFalse(run.exists())

    def test_escape_rejected(self) -> None:
        from agentrun.capabilities.workspace import WorkspaceError

        with tempfile.TemporaryDirectory() as t:
            ws = WorkspaceManager(t)
            with self.assertRaises(WorkspaceError):
                ws.path("..", "etc")


if __name__ == "__main__":
    unittest.main()
