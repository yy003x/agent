"""M0 cli provider 全链路单测:start → result.json → done(无外部依赖)。"""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from agentrun.cli.main import main as cli_main
from agentrun.kernel import AgentRuntime


def _script(d: Path) -> Path:
    p = d / "stub-cli.sh"
    p.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        "printf '{\"schema_version\":1,\"run_id\":\"%s\",\"outcome\":\"succeeded\",\"summary\":\"stub cli\",\"artifacts\":[],\"errors\":[],\"validation\":{\"commands\":[],\"passed\":true}}' \"$AGENTRUN_RUN_ID\" > \"$AGENTRUN_RESULT_FILE\"\n",
        encoding="utf-8",
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _script_with_body(d: Path, body: str) -> Path:
    p = d / "stub-cli.sh"
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _conf(root: Path, script: Path) -> Path:
    conf = root / "conf"
    providers = conf / "providers"
    providers.mkdir(parents=True)
    (conf / "runtime.yaml").write_text("default_profile: test-cli\n", encoding="utf-8")
    (providers / "cli.yaml").write_text(
        "codex:\n"
        "  profile: test-cli\n"
        f"  command: {script}\n"
        "  args: []\n"
        "  timeout_seconds: 30\n"
        "  result_contract: required\n",
        encoding="utf-8",
    )
    return conf


class CliProviderE2ETest(unittest.TestCase):
    def test_run_task_reaches_done(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "prompt.md"
            prompt.write_text("做一件测试任务", encoding="utf-8")
            rt = AgentRuntime(conf_dir=_conf(root, _script(root)), runs_dir=root / "runs")

            out = rt.run_task(prompt_file=prompt)
            self.assertEqual(out["state"], "done")
            self.assertIsNone(out["failure_reason"])

            result = Path(out["result_file"])
            self.assertTrue(result.exists())
            self.assertEqual(json.loads(result.read_text())["outcome"], "succeeded")

            status = rt.task_status(out["run_id"])
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["classification"], "done")

    def test_doctor_and_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rt = AgentRuntime(conf_dir=_conf(root, _script(root)), runs_dir=root / "runs")
            doc = rt.doctor()
            self.assertTrue(doc["ok"])
            self.assertGreaterEqual(doc["profiles"], 1)
            self.assertTrue(doc["providers"]["test-cli"]["implemented"])
            ids = {p["id"] for p in rt.profiles()}
            self.assertIn("test-cli", ids)

    def test_default_profile_used(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            rt = AgentRuntime(conf_dir=_conf(root, _script(root)), runs_dir=root / "runs")
            out = rt.run_task(prompt_file=prompt)
            self.assertEqual(out["state"], "done")

    def test_explicit_run_id_is_idempotent_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            rt = AgentRuntime(conf_dir=_conf(root, _script(root)), runs_dir=root / "runs")
            first = rt.run_task(prompt_file=prompt, run_id="task-fixed")
            second = rt.run_task(prompt_file=prompt, run_id="task-fixed")
            self.assertEqual(first["state"], "done")
            self.assertEqual(second["state"], "done")
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["run_id"], "task-fixed")

    def test_task_watch_reaches_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            conf = _conf(root, _script(root))
            runs_dir = root / "runs"
            rt = AgentRuntime(conf_dir=conf, runs_dir=runs_dir)
            out = rt.run_task(prompt_file=prompt, run_id="task-watch")
            self.assertEqual(out["state"], "done")

            buf = StringIO()
            with redirect_stdout(buf):
                code = cli_main(
                    [
                        "--conf-dir",
                        str(conf),
                        "--runs-dir",
                        str(runs_dir),
                        "task",
                        "watch",
                        "task-watch",
                        "--poll-seconds",
                        "0.1",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("AgentRun 监控结束：done", buf.getvalue())

    def test_task_watch_renders_stream_sources_as_cli_log(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            script = _script_with_body(
                root,
                "cat >/dev/null\n"
                "printf 'stdout-line\\n'\n"
                "printf 'stderr-line\\n' >&2\n"
                "printf '{\"schema_version\":1,\"run_id\":\"%s\",\"outcome\":\"succeeded\",\"summary\":\"stub cli\",\"artifacts\":[],\"errors\":[],\"validation\":{\"commands\":[],\"passed\":true}}' \"$AGENTRUN_RUN_ID\" > \"$AGENTRUN_RESULT_FILE\"\n",
            )
            conf = _conf(root, script)
            runs_dir = root / "runs"
            rt = AgentRuntime(conf_dir=conf, runs_dir=runs_dir)
            out = rt.run_task(prompt_file=prompt, run_id="task-watch-stream")
            self.assertEqual(out["state"], "done")

            buf = StringIO()
            with redirect_stdout(buf):
                code = cli_main(
                    [
                        "--conf-dir",
                        str(conf),
                        "--runs-dir",
                        str(runs_dir),
                        "task",
                        "watch",
                        "task-watch-stream",
                    ]
                )
            self.assertEqual(code, 0)
            rendered = buf.getvalue()
            self.assertIn("[cli-log] stdout-line", rendered)
            self.assertIn("[cli-log] stderr-line", rendered)
            self.assertNotIn("[stdout]", rendered)
            self.assertNotIn("[stderr]", rendered)


if __name__ == "__main__":
    unittest.main()
