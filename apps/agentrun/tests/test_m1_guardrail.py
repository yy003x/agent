"""M1 guardrail:capability 门禁 + 路径越界拦截。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentrun.core.config import Profile
from agentrun.core.rundir import run_paths
from agentrun.core.run import TASK, RunRequest
from agentrun.guardrail import Guardrail, GuardrailError
from agentrun.providers.tmux import TmuxProvider


class GuardrailTest(unittest.TestCase):
    def test_require_missing_capability(self) -> None:
        g = Guardrail(capabilities=set())
        with self.assertRaises(GuardrailError):
            g.require("remote_write")

    def test_require_granted_capability(self) -> None:
        g = Guardrail(capabilities={"remote_write"})
        g.require("remote_write")  # 不抛

    def test_forbidden_overrides_grant(self) -> None:
        g = Guardrail(capabilities={"delete"}, forbidden_actions={"delete"})
        with self.assertRaises(GuardrailError):
            g.require("delete")

    def test_path_within_root_ok(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            g = Guardrail(allowed_roots=[root])
            sub = root / "a" / "b.txt"
            # root 可能是符号链接(macOS /var→/private/var),按 resolve 后比较
            self.assertTrue(g.check_path_within(sub).is_relative_to(root.resolve()))

    def test_path_outside_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            g = Guardrail(allowed_roots=[Path(t)])
            with self.assertRaises(GuardrailError):
                g.check_path_within("/etc/passwd")

    def test_path_with_dotdot_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            g = Guardrail(allowed_roots=[root])
            with self.assertRaises(GuardrailError):
                g.check_path_within(str(root / ".." / "x"))

    def test_no_allowed_roots_denies(self) -> None:
        g = Guardrail(allowed_roots=[])
        with self.assertRaises(GuardrailError):
            g.check_path_within("/tmp/x")

    def test_tmux_auto_trust_requires_capability(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            paths = run_paths(root / "runs", "_default", TASK, "task-1").ensure()
            req = RunRequest(
                run_type=TASK,
                run_id="task-1",
                provider_profile="tmux-x",
                provider="tmux",
                cwd=root,
                result_file=paths.result_file,
            )
            profile = Profile(
                "tmux-x",
                "tmux",
                "x",
                "/bin/sh",
                [],
                1,
                "required",
                raw={"tmux_session_name": "x", "auto_trust_cwd": ["codex"]},
            )
            provider = TmuxProvider(profile)
            provider._require_tmux = lambda: None  # type: ignore[method-assign]
            with self.assertRaises(GuardrailError):
                provider.run(req, paths)


if __name__ == "__main__":
    unittest.main()
