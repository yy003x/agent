"""M1 tmux smoke:done_file/result 契约 + 会话生命周期 + pane 身份。

需要 tmux 二进制;用 stub 脚本替身(读 env 写 result+touch done),不依赖真 codex。
若本机无 tmux 则跳过(门禁机器须装 tmux)。
"""
from __future__ import annotations

import shutil
import shlex
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agentrun.core.config import Profile
from agentrun.core.contract import read_status, write_status
from agentrun.core.rundir import run_paths
from agentrun.core.run import RUNNING, SESSION, TASK, RunRequest
from agentrun.providers.tmux import TmuxProvider
from agentrun.providers.tmux.runner import TmuxError

_RESULT = (
    '{"schema_version":1,"run_id":"x","outcome":"succeeded","summary":"stub tmux",'
    '"artifacts":[],"errors":[],"validation":{"commands":[],"passed":true}}'
)


def _session() -> str:
    return f"agentrun-test-{uuid.uuid4().hex[:8]}"


def _script(body: str, d: Path) -> Path:
    p = d / "stub-codex.sh"
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _profile(binary: str, session: str, **extra) -> Profile:
    raw = {
        "tmux_session_name": session,
        "prompt_delivery": "none",
        "session_ready_timeout_seconds": 2,
        "session_ready_settle_seconds": 0.05,
        **extra,
    }
    return Profile("tmux-x", "tmux", "x", binary, [], extra.get("timeout", 15), "optional", raw=raw)


_FAST_IDLE = {
    "poll_interval_seconds": 0.05,
    "silence_threshold_seconds": 0.05,
    "prompt_idle_timeout_seconds": 2,
    "prompt_ready_settle_seconds": 0.05,
    "prompt_ready_settle_fast_seconds": 0.05,
    "prompt_stable_timeout_seconds": 2,
    "startup_idle": {"low_bytes": 0, "high_bytes": 1, "min_ticks": 1, "max_ticks": 1},
    "tui_startup_idle": {"low_bytes": 0, "high_bytes": 1, "min_ticks": 1, "max_ticks": 1},
    "runtime_idle": {"low_bytes": 0, "high_bytes": 1, "min_ticks": 1, "max_ticks": 1},
}


@unittest.skipIf(shutil.which("tmux") is None, "tmux 未安装")
class TmuxTest(unittest.TestCase):
    def test_done_file_contract_reaches_done(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script(
                'printf \'%s\' \'' + _RESULT + '\' > "$AGENTRUN_RESULT_FILE"\n'
                'touch "$AGENTRUN_DONE_FILE"\n'
                "exec sleep 2\n",
                d,
            )
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            prompt = d / "p.md"
            prompt.write_text("做事", encoding="utf-8")
            req = RunRequest(
                run_type=TASK, run_id="task-1", provider_profile="tmux-x", provider="tmux",
                cwd=d, prompt_file=prompt, result_file=paths.result_file, deadline_seconds=15,
            )
            provider = TmuxProvider(_profile(str(script), session))
            try:
                out = provider.run(req, paths)
                self.assertEqual(out["status"]["state"], "done")
                import json

                self.assertEqual(json.loads(paths.result_file.read_text())["outcome"], "succeeded")
            finally:
                provider.stop(paths)

    def test_missing_result_after_done_fails(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            # 只 touch done,不写 result → 应 failed(result_missing)
            script = _script('touch "$AGENTRUN_DONE_FILE"\nexec sleep 2\n', d)
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-2").ensure()
            req = RunRequest(
                run_type=TASK, run_id="task-2", provider_profile="tmux-x", provider="tmux",
                cwd=d, result_file=paths.result_file, deadline_seconds=15,
            )
            provider = TmuxProvider(_profile(str(script), session))
            try:
                out = provider.run(req, paths)
                self.assertEqual(out["status"]["state"], "failed")
                self.assertEqual(out["status"]["failure_reason"], "result_missing")
            finally:
                provider.stop(paths)

    def test_session_lifecycle_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("echo session-ready\nexec /bin/sh\n", d)
            session = _session()
            paths = run_paths(d / "runs", "_default", SESSION, "sess-1").ensure()
            req = RunRequest(
                run_type=SESSION, run_id="sess-1", provider_profile="tmux-x", provider="tmux", cwd=d,
            )
            provider = TmuxProvider(_profile(str(script), session))
            try:
                start = provider.start_session(req, paths)
                self.assertIn("pane_id", start)
                self.assertIn("window_id", start)
                self.assertTrue(start["ready"])
                self.assertEqual(start["ready_reason"], "output_stable")
                st = provider.session_status(paths)
                self.assertTrue(st["alive"])  # pane 身份四元组匹配=alive
                self.assertTrue(st["session_ready"])
                import time

                logs = ""
                deadline = time.time() + 3
                while time.time() < deadline:
                    logs = provider.logs(paths)["content"]
                    if "session-ready" in logs:
                        break
                    time.sleep(0.1)
                self.assertIn("session-ready", logs)

                provider.send(paths, "echo agentrun-smoke", submit=True)
                deadline = time.time() + 3
                while time.time() < deadline:
                    logs = provider.logs(paths)["content"]
                    if "agentrun-smoke" in logs:
                        break
                    time.sleep(0.1)
                self.assertIn("agentrun-smoke", logs)

                provider.interrupt(paths)
                provider.stop(paths)
                st2 = provider.session_status(paths)
                self.assertFalse(st2["alive"])  # stop 后 pane 不在
                with self.assertRaises(TmuxError):
                    provider.send(paths, "echo should-not-send", submit=True)
            finally:
                provider.stop(paths)

    def test_session_start_fails_when_tui_never_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("exec sleep 5\n", d)
            session = _session()
            paths = run_paths(d / "runs", "_default", SESSION, "sess-no-output").ensure()
            req = RunRequest(
                run_type=SESSION, run_id="sess-no-output", provider_profile="tmux-x", provider="tmux", cwd=d,
            )
            provider = TmuxProvider(
                _profile(
                    str(script),
                    session,
                    session_ready_timeout_seconds=0.2,
                    session_ready_settle_seconds=0.01,
                    poll_interval_seconds=0.05,
                )
            )
            try:
                with self.assertRaises(TmuxError):
                    provider.start_session(req, paths)
                status = read_status(paths) or {}
                self.assertEqual(status["state"], "failed")
                self.assertEqual(status["failure_reason"], "timeout")
                self.assertEqual(status["provider_status"]["session_ready_reason"], "session_ready_timeout")
            finally:
                provider.stop(paths)

    def test_stop_only_closes_recorded_session_pane(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("echo ready\nexec /bin/sh\n", d)
            session = _session()
            provider = TmuxProvider(_profile(str(script), session))
            paths1 = run_paths(d / "runs", "_default", SESSION, "sess-a").ensure()
            paths2 = run_paths(d / "runs", "_default", SESSION, "sess-b").ensure()
            req1 = RunRequest(
                run_type=SESSION, run_id="sess-a", provider_profile="tmux-x", provider="tmux", cwd=d,
            )
            req2 = RunRequest(
                run_type=SESSION, run_id="sess-b", provider_profile="tmux-x", provider="tmux", cwd=d,
            )
            try:
                provider.start_session(req1, paths1)
                provider.start_session(req2, paths2)
                self.assertTrue(provider.session_status(paths1)["alive"])
                self.assertTrue(provider.session_status(paths2)["alive"])

                provider.stop(paths1)

                self.assertFalse(provider.session_status(paths1)["alive"])
                self.assertTrue(provider.session_status(paths2)["alive"])
            finally:
                provider.stop(paths1)
                provider.stop(paths2)

    def test_raw_paste_delivery_submits_user_text_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            reader = d / "reader.py"
            reader.write_text(
                r"""
import json
import os
import select
import sys
import time
from pathlib import Path

print("OpenAI Codex", flush=True)
print("› Draft", flush=True)
print("Context 100% left", flush=True)

buf = ""
deadline = time.time() + 5
while time.time() < deadline:
    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
    if not ready:
        continue
    chunk = os.read(sys.stdin.fileno(), 4096).decode("utf-8", errors="replace")
    buf += chunk
    if "分析当前项目实现了什么功能。" in buf:
        break

run_dir = Path(os.environ["AGENTRUN_RUN_DIR"])
(run_dir / "submission.txt").write_text(buf, encoding="utf-8")
result = {
    "schema_version": 1,
    "run_id": os.environ["AGENTRUN_RUN_ID"],
    "outcome": "succeeded",
    "summary": "paste ok",
    "artifacts": [],
    "errors": [],
    "validation": {"commands": [], "passed": True},
}
tmp = Path(os.environ["AGENTRUN_RESULT_FILE"] + ".tmp")
tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
tmp.replace(os.environ["AGENTRUN_RESULT_FILE"])
Path(os.environ["AGENTRUN_DONE_FILE"]).touch()
""",
                encoding="utf-8",
            )
            script = _script(
                f"python3 {shlex.quote(str(reader))}\n",
                d,
            )
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-paste").ensure()
            prompt = d / "p.md"
            user_text = "分析当前项目实现了什么功能。"
            prompt.write_text(user_text, encoding="utf-8")
            req = RunRequest(
                run_type=TASK, run_id="task-paste", provider_profile="tmux-x", provider="tmux",
                cwd=d, prompt_file=prompt, result_file=paths.result_file, deadline_seconds=15,
            )
            provider = TmuxProvider(
                _profile(
                    str(script),
                    session,
                    prompt_delivery="paste",
                    tmux_input_mode="raw",
                    paste_bracketed=True,
                    **_FAST_IDLE,
                )
            )
            try:
                out = provider.run(req, paths)
                self.assertEqual(out["status"]["state"], "done")
                submission = (paths.run_dir / "submission.txt").read_text(encoding="utf-8")
                self.assertTrue((paths.run_dir / "submission.md").exists())
                self.assertTrue((paths.run_dir / "prompt_submitted").exists())
                self.assertEqual((paths.run_dir / "submission.md").read_text(encoding="utf-8"), user_text)
                self.assertEqual(submission, user_text + "\n")
                self.assertEqual((paths.run_dir / "input.md").read_text(encoding="utf-8"), user_text)
                self.assertIn(str(paths.result_file), (paths.run_dir / "prompt.md").read_text(encoding="utf-8"))
            finally:
                provider.stop(paths)

    def test_submission_mode_still_pastes_contract_entry(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            reader = d / "reader.py"
            reader.write_text(
                r"""
import json
import os
import select
import sys
import time
from pathlib import Path

print("OpenAI Codex", flush=True)
print("› Draft", flush=True)
print("Context 100% left", flush=True)

buf = ""
deadline = time.time() + 5
while time.time() < deadline:
    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
    if not ready:
        continue
    chunk = os.read(sys.stdin.fileno(), 4096).decode("utf-8", errors="replace")
    buf += chunk
    if "终端输出只作为过程日志" in buf:
        break

run_dir = Path(os.environ["AGENTRUN_RUN_DIR"])
(run_dir / "submission.txt").write_text(buf, encoding="utf-8")
result = {
    "schema_version": 1,
    "run_id": os.environ["AGENTRUN_RUN_ID"],
    "outcome": "succeeded",
    "summary": "paste ok",
    "artifacts": [],
    "errors": [],
    "validation": {"commands": [], "passed": True},
}
tmp = Path(os.environ["AGENTRUN_RESULT_FILE"] + ".tmp")
tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
tmp.replace(os.environ["AGENTRUN_RESULT_FILE"])
Path(os.environ["AGENTRUN_DONE_FILE"]).touch()
""",
                encoding="utf-8",
            )
            script = _script(
                f"python3 {shlex.quote(str(reader))}\n",
                d,
            )
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-paste-submission").ensure()
            prompt = d / "p.md"
            prompt.write_text("做事", encoding="utf-8")
            req = RunRequest(
                run_type=TASK, run_id="task-paste-submission", provider_profile="tmux-x", provider="tmux",
                cwd=d, prompt_file=prompt, result_file=paths.result_file, deadline_seconds=15,
            )
            provider = TmuxProvider(
                _profile(
                    str(script),
                    session,
                    prompt_delivery="paste",
                    tmux_input_mode="submission",
                    paste_bracketed=True,
                    **_FAST_IDLE,
                )
            )
            try:
                out = provider.run(req, paths)
                self.assertEqual(out["status"]["state"], "done")
                submission = (paths.run_dir / "submission.txt").read_text(encoding="utf-8")
                self.assertIn("请按照以下任务文件完成本次任务", submission)
                self.assertIn(str(paths.result_file), submission)
            finally:
                provider.stop(paths)


class TmuxReadyGateTest(unittest.TestCase):
    def test_numeric_tmux_session_name_is_rejected_by_provider(self) -> None:
        provider = TmuxProvider(_profile("codex", "1"))
        with self.assertRaisesRegex(TmuxError, "不能是纯数字"):
            provider._validate_session_name()

    def test_command_sh_includes_static_env_and_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-env").ensure()
            req = RunRequest(
                run_type=TASK,
                run_id="task-env",
                provider_profile="tmux-x",
                provider="tmux",
                cwd=d,
                result_file=paths.result_file,
            )
            profile = _profile(
                "codex",
                session,
                env={"AGENTRUN_TMUX_STATIC": "static"},
                env_passthrough=["AGENTRUN_TMUX_PASS"],
            )
            provider = TmuxProvider(profile)
            with patch.dict("os.environ", {"AGENTRUN_TMUX_PASS": "pass"}, clear=False):
                command = provider._write_command_sh(paths, req, paths.run_dir / "done", None)
            text = command.read_text(encoding="utf-8")
            self.assertIn("AGENTRUN_TMUX_STATIC=static", text)
            self.assertIn("AGENTRUN_TMUX_PASS=pass", text)

    def test_ready_gate_uses_idle_metrics_not_codex_banner(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            session = _session()
            paths = run_paths(d / "runs", "_default", TASK, "task-ready").ensure()
            req = RunRequest(
                run_type=TASK,
                run_id="task-ready",
                provider_profile="tmux-x",
                provider="tmux",
                cwd=d,
                result_file=paths.result_file,
                deadline_seconds=15,
            )
            provider = TmuxProvider(_profile("codex", session, **_FAST_IDLE))
            paths.output_log.parent.mkdir(parents=True, exist_ok=True)
            paths.output_log.write_text("plain tui startup without vendor banner\n", encoding="utf-8")
            status = provider._wait_idle_stable(
                req,
                paths,
                paths.run_dir / "done",
                identity=None,
                timeout_seconds=2,
                settle_seconds=None,
                timeout_reason="prompt_ready_timeout",
                wait_context="before prompt paste",
            )
            self.assertEqual(status["state"], "idle")


if __name__ == "__main__":
    unittest.main()
