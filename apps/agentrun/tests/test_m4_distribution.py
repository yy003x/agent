"""M4 分发:开箱自检 / conf 覆盖 / 项目配置 / 契约版本 fail-fast / SDK client / 打包。"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from importlib import resources
from pathlib import Path

from agentrun.core.jsonio import read_json
from agentrun.core.jsonio import write_json_atomic
from agentrun.core.rundir import run_paths
from agentrun.core.run import TASK
from agentrun.kernel import AgentRuntime
from agentrun.sdk import AgentRunClient, local_transport

_REPO = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _REPO.parents[1]


def _script(root: Path) -> Path:
    p = root / "stub-cli.sh"
    p.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        "printf '{\"schema_version\":1,\"run_id\":\"%s\",\"outcome\":\"succeeded\",\"summary\":\"ok\",\"artifacts\":[],\"errors\":[],\"validation\":{\"commands\":[],\"passed\":true}}' \"$AGENTRUN_RUN_ID\" > \"$AGENTRUN_RESULT_FILE\"\n",
        encoding="utf-8",
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _cli_conf(root: Path, profile: str = "test-cli") -> Path:
    conf = root / "conf"
    providers = conf / "providers"
    providers.mkdir(parents=True)
    script = _script(root)
    (conf / "runtime.yaml").write_text(f"default_profile: {profile}\n", encoding="utf-8")
    (providers / "cli.yaml").write_text(
        "codex:\n"
        f"  profile: {profile}\n"
        f"  command: {script}\n"
        "  args: []\n"
        "  timeout_seconds: 30\n"
        "  result_contract: required\n",
        encoding="utf-8",
    )
    return conf


class OpenBoxTest(unittest.TestCase):
    def test_no_config_works_out_of_box_for_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rt = AgentRuntime(conf_dir=Path(d) / "missing-conf", runs_dir=Path(d) / "runs")
            doc = rt.doctor()
            ids = {p["id"] for p in rt.profiles()}
            self.assertTrue(doc["ok"])
            self.assertEqual(doc["default_profile"], "codex-cli")
            self.assertIn("codex-cli", ids)
            self.assertIn("api-openai-gpt-4o-mini", ids)
            self.assertIn("tmux-codex", ids)

    def test_caller_conf_dir_override_changes_fixed_cli_profile(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            conf = _cli_conf(root, profile="my-cli")
            rt = AgentRuntime(conf_dir=conf, runs_dir=root / "runs")
            ids = {p["id"] for p in rt.profiles()}
            self.assertIn("my-cli", ids)
            self.assertIn("claude-cli", ids)
            self.assertIn("api-openai-gpt-4o-mini", ids)

    def test_project_overlay_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            conf = root / "conf"
            script = _script(root)
            (conf / "projects").mkdir(parents=True)
            (conf / "projects" / "p1.runtime.yaml").write_text(
                "default_profile: project-cli\n"
                "cli:\n"
                "  codex:\n"
                "    profile: project-cli\n"
                f"    command: {script}\n"
                "    args: []\n"
                "    result_contract: required\n",
                encoding="utf-8",
            )
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            rt = AgentRuntime(conf_dir=conf, runs_dir=root / "runs")
            out = rt.run_task(prompt_file=prompt, project_id="p1")
            paths = run_paths(root / "runs", "p1", TASK, out["run_id"])
            self.assertEqual(read_json(paths.request_file)["provider_profile"], "project-cli")

    def test_fixed_provider_files_build_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            conf = root / "conf"
            providers = conf / "providers"
            providers.mkdir(parents=True)
            (conf / "runtime.yaml").write_text("default_profile: cx-local\n", encoding="utf-8")
            (providers / "api.yaml").write_text(
                "openai:\n"
                "  protocol: openai\n"
                "  base_url: https://llm.example.test/v1\n"
                "  api_key_env: AGENTRUN_TEST_API_KEY\n"
                "  headers:\n"
                "    X-Project: test\n"
                "  models:\n"
                "    model-x:\n"
                "      model: model-x\n"
                "      label: Model X\n",
                encoding="utf-8",
            )
            (providers / "cli.yaml").write_text(
                "codex:\n"
                "  profile: cx-local\n"
                "  command: codex-dev\n"
                "  args: [exec, --model, gpt-5]\n"
                "  env:\n"
                "    CODEX_HOME: /tmp/codex-home\n"
                "  env_passthrough: [PATH]\n",
                encoding="utf-8",
            )
            (providers / "tmux.yaml").write_text(
                "defaults:\n"
                "  session_name: runtime-test\n"
                "claude:\n"
                "  command: claude-dev\n"
                "  args: [--model, sonnet]\n"
                "  env:\n"
                "    CLAUDE_CODE_MAX_OUTPUT_TOKENS: 64000\n"
                "  env_passthrough: [SHELL]\n",
                encoding="utf-8",
            )
            rt = AgentRuntime(conf_dir=conf, runs_dir=root / "runs")
            profiles = {p.id: p for p in rt.service.config.profiles().values()}

            self.assertEqual(rt.doctor()["default_profile"], "cx-local")
            self.assertEqual(profiles["cx-local"].transport, "cli")
            self.assertEqual(profiles["cx-local"].binary, "codex-dev")
            self.assertEqual(profiles["cx-local"].default_args, ["exec", "--model", "gpt-5"])
            self.assertEqual(profiles["cx-local"].raw["env"]["CODEX_HOME"], "/tmp/codex-home")
            self.assertEqual(profiles["cx-local"].raw["env_passthrough"], ["PATH"])

            self.assertEqual(profiles["api-openai-model-x"].raw["base_url"], "https://llm.example.test/v1")
            self.assertEqual(profiles["api-openai-model-x"].raw["model"], "model-x")
            self.assertEqual(profiles["api-openai-model-x"].raw["api_key_env"], "AGENTRUN_TEST_API_KEY")
            self.assertEqual(profiles["api-openai-model-x"].raw["headers"]["X-Project"], "test")

            self.assertEqual(profiles["tmux-claude"].binary, "claude-dev")
            self.assertEqual(profiles["tmux-claude"].default_args, ["--model", "sonnet"])
            self.assertEqual(profiles["tmux-claude"].raw["tmux_session_name"], "runtime-test")
            self.assertEqual(profiles["tmux-claude"].raw["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "64000")
            self.assertEqual(profiles["tmux-claude"].raw["env_passthrough"], ["SHELL"])

    def test_cwd_conf_provider_files_are_loaded_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            providers = root / "conf" / "providers"
            providers.mkdir(parents=True)
            (providers / "cli.yaml").write_text(
                "codex:\n"
                "  profile: cx-auto\n"
                "  command: codex-auto\n"
                "  args: [exec, --fast]\n",
                encoding="utf-8",
            )
            (providers / "api.yaml").write_text(
                "auto:\n"
                "  protocol: openai\n"
                "  base_url: https://auto.example.test/v1\n"
                "  api_key_env: AUTO_API_KEY\n"
                "  models:\n"
                "    auto-model:\n"
                "      model: auto-model\n",
                encoding="utf-8",
            )
            (providers / "tmux.yaml").write_text(
                "session_name: auto-session\n",
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                rt = AgentRuntime(runs_dir=root / "runs")
                profiles = rt.service.config.profiles()
            finally:
                os.chdir(old_cwd)

            self.assertEqual(profiles["cx-auto"].transport, "cli")
            self.assertEqual(profiles["cx-auto"].binary, "codex-auto")
            self.assertEqual(profiles["cx-auto"].default_args, ["exec", "--fast"])
            self.assertEqual(profiles["api-auto-auto-model"].raw["base_url"], "https://auto.example.test/v1")
            self.assertEqual(profiles["api-auto-auto-model"].raw["model"], "auto-model")
            self.assertEqual(profiles["tmux-codex"].raw["tmux_session_name"], "auto-session")
            self.assertEqual(profiles["tmux-claude"].raw["tmux_session_name"], "auto-session")

    def test_project_overlay_can_override_tmux_session_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            conf = root / "conf"
            (conf / "projects").mkdir(parents=True)
            (conf / "projects" / "p1.runtime.yaml").write_text(
                "tmux:\n"
                "  session_name: project-session\n",
                encoding="utf-8",
            )
            rt = AgentRuntime(conf_dir=conf, runs_dir=root / "runs")
            profiles = rt.service.config.profiles(project_id="p1")
            self.assertEqual(profiles["tmux-codex"].raw["tmux_session_name"], "project-session")
            self.assertEqual(profiles["tmux-claude"].raw["tmux_session_name"], "project-session")


class ProjectConfigTest(unittest.TestCase):
    def test_project_config_lives_under_config_agentrun(self) -> None:
        conf = _PROJECT_ROOT / "config" / "agentrun"
        self.assertTrue((conf / "runtime.yaml").is_file())
        for name in ("api", "cli", "tmux"):
            self.assertTrue((conf / "providers" / f"{name}.yaml").is_file())

    def test_builtin_schemas_shipped_as_resources(self) -> None:
        schemas = resources.files("agentrun") / "schemas"
        for name in ("request", "status", "result", "event", "profile", "overlay"):
            self.assertTrue((schemas / f"{name}.schema.yaml").is_file())


class CliCompatibilityTest(unittest.TestCase):
    def test_json_flag_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentrun.cli.main",
                    "doctor",
                    "--runs-dir",
                    str(Path(d) / "runs"),
                    "--conf-dir",
                    str(Path(d) / "missing-conf"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(_REPO)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(read_json_from_text(proc.stdout)["ok"])

    def test_session_watch_help_available(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentrun.cli.main", "session", "watch", "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(_REPO)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--seconds", proc.stdout)


class ContractVersionTest(unittest.TestCase):
    def test_mismatch_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rt = AgentRuntime(runs_dir=Path(d) / "runs")
            paths = run_paths(Path(d) / "runs", "_default", TASK, "task-x").ensure()
            write_json_atomic(paths.status_file, {"state": "done", "run_id": "task-x"})
            write_json_atomic(paths.request_file, {"contract_version": 999, "run_id": "task-x"})
            with self.assertRaises(ValueError):
                rt.task_status("task-x")


class SdkClientTest(unittest.TestCase):
    def test_client_drives_via_transport(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "p.md"
            prompt.write_text("x", encoding="utf-8")
            rt = AgentRuntime(conf_dir=_cli_conf(root), runs_dir=root / "runs")
            client = AgentRunClient(local_transport(rt.service))
            self.assertTrue(client.doctor()["ok"])
            out = client.run_task(prompt_file=str(prompt))
            self.assertEqual(out["state"], "done")
            self.assertEqual(client.task_status(out["run_id"])["state"], "done")


class WheelBuildTest(unittest.TestCase):
    def test_wheel_builds_and_includes_conf(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "wheel", str(_REPO), "--no-deps", "-w", d],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                self.skipTest(f"pip wheel 不可用(无 setuptools/build 环境):{proc.stderr[-200:]}")
            wheels = list(Path(d).glob("agentrun-*.whl"))
            self.assertTrue(wheels, "未产出 wheel")
            with zipfile.ZipFile(wheels[0]) as zf:
                names = zf.namelist()
            self.assertTrue(any(n.endswith("schemas/result.schema.yaml") for n in names), "wheel 未包含内置 schemas")


def read_json_from_text(text: str):
    import json

    return json.loads(text)


if __name__ == "__main__":
    unittest.main()
