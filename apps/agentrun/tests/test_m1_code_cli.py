"""M1 cli:脚本 binary 替身,测 done / result 缺失 / 超时。"""
from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentrun.core.config import Profile
from agentrun.core.rundir import run_paths
from agentrun.core.run import TASK, RunRequest
from agentrun.providers.code_cli import CodeCliProvider

_RESULT_JSON = (
    '{"schema_version":1,"run_id":"x","outcome":"succeeded","summary":"stub cli",'
    '"artifacts":[],"errors":[],"validation":{"commands":[],"passed":true}}'
)


def _script(body: str, d: Path) -> Path:
    p = d / "stub-cli.sh"
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _profile(binary: str, timeout: int = 30, contract: str = "required", raw: dict | None = None) -> Profile:
    return Profile(
        id="codex-cli",
        transport="cli",
        label="x",
        binary=binary,
        default_args=[],
        timeout_seconds=timeout,
        result_contract=contract,
        raw=raw or {},
    )


def _request(d: Path, paths) -> RunRequest:
    prompt = d / "p.md"
    prompt.write_text("hi", encoding="utf-8")
    return RunRequest(
        run_type=TASK,
        run_id="task-1",
        provider_profile="codex-cli",
        provider="cli",
        cwd=d,
        prompt_file=prompt,
        result_file=paths.result_file,
    )


class CodeCliTest(unittest.TestCase):
    def test_writes_result_reaches_done(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script(f'cat >/dev/null\nprintf \'{_RESULT_JSON}\' > "$AGENTRUN_RESULT_FILE"\n', d)
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            out = CodeCliProvider(_profile(str(script))).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "done")
            self.assertEqual(out["result"]["outcome"], "succeeded")

    def test_missing_required_result_fails(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("cat >/dev/null\nexit 0\n", d)  # 不写 result
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            out = CodeCliProvider(_profile(str(script))).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "failed")
            self.assertEqual(out["status"]["failure_reason"], "result_missing")

    def test_nonzero_exit_keeps_cli_error_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("cat >/dev/null\nprintf 'Run /usage to use one.\\n' >&2\nexit 1\n", d)
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            out = CodeCliProvider(_profile(str(script))).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "failed")
            self.assertEqual(out["status"]["failure_reason"], "exited")
            self.assertIn("Run /usage to use one.", out["status"]["message"])
            self.assertIn("Run /usage to use one.", out["status"]["provider_status"]["error_excerpt"])

    def test_optional_contract_synthesizes_result(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("cat >/dev/null\nexit 0\n", d)
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            out = CodeCliProvider(_profile(str(script), contract="optional")).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "done")

    def test_env_config_is_injected(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script(
                "cat >/dev/null\n"
                "summary=\"$AGENTRUN_TEST_STATIC/$AGENTRUN_TEST_PASS\"\n"
                "printf '{\"schema_version\":1,\"run_id\":\"x\",\"outcome\":\"succeeded\",\"summary\":\"%s\",\"artifacts\":[],\"errors\":[],\"validation\":{\"commands\":[],\"passed\":true}}' \"$summary\" > \"$AGENTRUN_RESULT_FILE\"\n",
                d,
            )
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            profile = _profile(
                str(script),
                raw={
                    "env": {"AGENTRUN_TEST_STATIC": "${AGENTRUN_TEST_SOURCE}"},
                    "env_passthrough": ["AGENTRUN_TEST_PASS"],
                },
            )
            with patch.dict(os.environ, {"AGENTRUN_TEST_SOURCE": "static", "AGENTRUN_TEST_PASS": "pass"}, clear=False):
                out = CodeCliProvider(profile).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "done")
            self.assertEqual(out["result"]["summary"], "static/pass")

    def test_timeout_fails_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script("sleep 5\n", d)
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            out = CodeCliProvider(_profile(str(script), timeout=1)).run(_request(d, paths), paths)
            self.assertEqual(out["status"]["state"], "failed")
            self.assertEqual(out["status"]["failure_reason"], "timeout")

    def test_output_log_streams_while_cli_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = _script(
                "cat >/dev/null\n"
                "printf 'stream-line\\n'\n"
                "sleep 1\n"
                f"printf '{_RESULT_JSON}' > \"$AGENTRUN_RESULT_FILE\"\n",
                d,
            )
            paths = run_paths(d / "runs", "_default", TASK, "task-1").ensure()
            result: dict = {}

            thread = threading.Thread(
                target=lambda: result.update(CodeCliProvider(_profile(str(script))).run(_request(d, paths), paths))
            )
            thread.start()
            deadline = time.time() + 2
            while time.time() < deadline:
                if paths.output_log.exists() and "stream-line" in paths.output_log.read_text(encoding="utf-8"):
                    break
                time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            self.assertIn("stream-line", paths.output_log.read_text(encoding="utf-8"))
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["status"]["state"], "done")


if __name__ == "__main__":
    unittest.main()
