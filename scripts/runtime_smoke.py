#!/usr/bin/env python3
"""AgentRun runtime smoke test.

这个脚本只用临时配置和本地 stub CLI 验证当前 runtime 主链路:
doctor/profiles/config validate/choices/task run/status/logs/Workbench adapter。
它不会写入项目级 config/agentrun 或 runs/agentrun。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENTRUN_APP = ROOT / "apps" / "agentrun"
MIN_PYTHON = (3, 11)


class SmokeError(RuntimeError):
    """runtime smoke 失败。"""


def main(argv: Any = None) -> int:
    args = _parse_args(argv)
    python = _select_python()
    if Path(python).resolve() != Path(sys.executable).resolve() and not os.environ.get("AGENTRUN_SMOKE_REEXEC"):
        env = {**os.environ, "AGENTRUN_SMOKE_REEXEC": "1"}
        proc = subprocess.run([python, str(Path(__file__).resolve()), *sys.argv[1:]], env=env, check=False)
        return proc.returncode

    work_root = Path(args.work_dir).expanduser().resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="agentrun-smoke-")
    )
    created_temp = args.work_dir is None
    report: dict[str, Any] = {
        "ok": False,
        "repo": str(ROOT),
        "work_dir": str(work_root),
        "checks": [],
    }
    try:
        _require_runtime_layout()
        paths = _prepare_smoke_workspace(work_root)
        runner = CliRunner(python=sys.executable, conf_dir=paths["conf_dir"], runs_dir=paths["runs_dir"])
        checks = [
            ("layout", check_layout),
            ("doctor", lambda: check_doctor(runner)),
            ("profiles", lambda: check_profiles(runner)),
            ("validate_config", lambda: check_validate_config(runner)),
            ("choices", lambda: check_choices(runner)),
            ("task_run", lambda: check_task_run(runner, paths)),
            ("task_status", lambda: check_task_status(runner)),
            ("task_logs", lambda: check_task_logs(runner)),
            ("adapter", lambda: check_adapter(paths)),
        ]
        for name, check in checks:
            try:
                detail = check()
                report["checks"].append({"name": name, "ok": True, **(detail or {})})
                if not args.json:
                    print(f"  ✓ {name}")
            except Exception as exc:  # noqa: BLE001 CLI 边界统一输出
                report["checks"].append({"name": name, "ok": False, "error": str(exc)})
                raise
        report["ok"] = True
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("")
            print("runtime smoke 通过")
            print(f"work_dir: {work_root}")
        return 0
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = str(exc)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"runtime smoke 失败: {exc}", file=sys.stderr)
            if report["checks"]:
                print("已完成检查:", file=sys.stderr)
                for item in report["checks"]:
                    marker = "✓" if item.get("ok") else "✗"
                    print(f"  {marker} {item.get('name')}: {item.get('error', '')}", file=sys.stderr)
            print(f"work_dir: {work_root}", file=sys.stderr)
        return 1
    finally:
        if created_temp and not args.keep_temp:
            shutil.rmtree(work_root, ignore_errors=True)


def _parse_args(argv: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试当前 AgentRun runtime 主链路")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时目录用于排查")
    parser.add_argument("--work-dir", default="", help="指定隔离测试目录；默认使用系统临时目录")
    return parser.parse_args(argv)


def _select_python() -> str:
    if sys.version_info >= MIN_PYTHON:
        return sys.executable
    candidates = [
        ROOT / ".venv" / "bin" / "python3",
        ROOT.parent / ".venv" / "bin" / "python3",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        proc = subprocess.run(
            [str(candidate), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            check=False,
        )
        if proc.returncode == 0:
            return str(candidate)
    raise SmokeError("需要 Python 3.11+ 才能运行 AgentRun")


def _require_runtime_layout() -> None:
    required = [
        AGENTRUN_APP / "agentrun",
        AGENTRUN_APP / "adapter.py",
        ROOT / "config" / "agentrun" / "runtime.yaml",
        ROOT / "config" / "agentrun" / "providers" / "cli.yaml",
        ROOT / "config" / "agentrun" / "providers" / "tmux.yaml",
        ROOT / "config" / "agentrun" / "providers" / "api.yaml",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise SmokeError("runtime 必要文件缺失: " + ", ".join(missing))


def _prepare_smoke_workspace(work_root: Path) -> dict[str, Path]:
    if work_root.exists() and any(work_root.iterdir()):
        raise SmokeError(f"work_dir 必须为空目录: {work_root}")
    work_root.mkdir(parents=True, exist_ok=True)
    conf_dir = work_root / "conf"
    providers_dir = conf_dir / "providers"
    runs_dir = work_root / "runs"
    local_runtime_dir = work_root / "workbench-runtime"
    providers_dir.mkdir(parents=True)
    runs_dir.mkdir()
    local_runtime_dir.mkdir()

    stub = work_root / "stub_provider.py"
    stub.write_text(_stub_provider_source(), encoding="utf-8")
    (conf_dir / "runtime.yaml").write_text(
        "default_project: runtime-smoke\n"
        "default_profile: runtime-smoke-cli\n"
        "max_concurrency: 1\n",
        encoding="utf-8",
    )
    (providers_dir / "cli.yaml").write_text(
        "runtime_smoke:\n"
        "  profile: runtime-smoke-cli\n"
        f"  command: \"{sys.executable}\"\n"
        "  args:\n"
        f"    - \"{stub}\"\n"
        "  timeout_seconds: 30\n"
        "  result_contract: required\n",
        encoding="utf-8",
    )
    prompt = work_root / "prompt.md"
    prompt.write_text("RUNTIME_SMOKE: 请执行一次本地 stub runtime 测试。\n", encoding="utf-8")
    return {
        "work_root": work_root,
        "conf_dir": conf_dir,
        "runs_dir": runs_dir,
        "prompt": prompt,
        "local_runtime_dir": local_runtime_dir,
    }


def _stub_provider_source() -> str:
    return textwrap.dedent(
        """
        import json
        import os
        import sys
        from pathlib import Path

        prompt = sys.stdin.read()
        if "RUNTIME_SMOKE" not in prompt:
            print("missing RUNTIME_SMOKE marker", file=sys.stderr)
            raise SystemExit(23)

        result_file = Path(os.environ["AGENTRUN_RESULT_FILE"])
        result = {
            "schema_version": 1,
            "run_id": os.environ["AGENTRUN_RUN_ID"],
            "outcome": "succeeded",
            "summary": f"runtime smoke ok: {len(prompt)} bytes",
            "artifacts": [],
            "errors": [],
            "validation": {
                "commands": ["stub_provider.py"],
                "passed": True,
            },
        }
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
        print("runtime-smoke-provider-ok")
        """
    ).lstrip()


class CliRunner:
    def __init__(self, *, python: str, conf_dir: Path, runs_dir: Path) -> None:
        self.python = python
        self.conf_dir = conf_dir
        self.runs_dir = runs_dir

    def run(self, *args: str, timeout: int = 60) -> dict[str, Any]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(AGENTRUN_APP) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run(
            [
                self.python,
                "-m",
                "agentrun.cli.main",
                "--conf-dir",
                str(self.conf_dir),
                "--runs-dir",
                str(self.runs_dir),
                "--json",
                *args,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SmokeError(f"CLI 输出不是 JSON: {exc}; stdout={proc.stdout!r}; stderr={proc.stderr!r}") from exc
        if proc.returncode != 0:
            raise SmokeError(
                "CLI 命令失败: "
                + " ".join(args)
                + f"; payload={payload!r}; stderr={proc.stderr.strip()!r}"
            )
        return payload


def check_layout() -> dict[str, Any]:
    _require_runtime_layout()
    return {"owner": "apps/agentrun"}


def check_doctor(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run("doctor")
    providers = payload.get("providers", {})
    item = providers.get("runtime-smoke-cli")
    if not payload.get("ok") or not item:
        raise SmokeError(f"doctor 没有加载 runtime-smoke-cli: {payload}")
    if item.get("transport") != "cli" or not item.get("implemented"):
        raise SmokeError(f"doctor provider 状态异常: {item}")
    return {"profiles": payload.get("profiles")}


def check_profiles(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run("profiles")
    profiles = payload.get("profiles", [])
    match = [item for item in profiles if item.get("id") == "runtime-smoke-cli"]
    if not match:
        raise SmokeError("profiles 未返回 runtime-smoke-cli")
    if match[0].get("result_contract") != "required":
        raise SmokeError(f"runtime-smoke-cli result_contract 异常: {match[0]}")
    return {"profile_count": len(profiles)}


def check_validate_config(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run(
        "config",
        "validate",
        "--project",
        "runtime-smoke",
        "--provider",
        "cli",
        "--profile",
        "runtime-smoke-cli",
        timeout=60,
    )
    if not payload.get("ok") or payload.get("validated") != 1:
        raise SmokeError(f"config validate 失败: {payload}")
    return {"status_file": payload.get("status_file")}


def check_choices(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run("config", "choices", "--project", "runtime-smoke")
    choices = payload.get("choices", [])
    if not any(item.get("id") == "runtime-smoke-cli" and item.get("validated") for item in choices):
        raise SmokeError(f"config choices 未包含已验证 smoke profile: {payload}")
    return {"choice_count": len(choices)}


def check_task_run(runner: CliRunner, paths: dict[str, Path]) -> dict[str, Any]:
    payload = runner.run(
        "task",
        "run",
        "--project",
        "runtime-smoke",
        "--profile",
        "runtime-smoke-cli",
        "--prompt-file",
        str(paths["prompt"]),
        "--run-id",
        "task-runtime-smoke",
        "--cwd",
        str(ROOT),
        "--deadline-seconds",
        "30",
        "--force",
        timeout=60,
    )
    if payload.get("state") != "done":
        raise SmokeError(f"task run 未完成: {payload}")
    run_dir = paths["runs_dir"] / "tasks" / "runtime-smoke" / "task-runtime-smoke"
    request = _read_json(run_dir / "request.json")
    status = _read_json(run_dir / "status.json")
    result = _read_json(run_dir / "result.json")
    output_log = (run_dir / "output.log").read_text(encoding="utf-8")
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    if request.get("provider_profile") != "runtime-smoke-cli" or request.get("provider") != "cli":
        raise SmokeError(f"request.json provider 异常: {request}")
    if status.get("state") != "done":
        raise SmokeError(f"status.json state 异常: {status}")
    if result.get("outcome") != "succeeded" or not result.get("validation", {}).get("passed"):
        raise SmokeError(f"result.json 异常: {result}")
    if "runtime-smoke-provider-ok" not in output_log:
        raise SmokeError("output.log 未记录 stub provider stdout")
    if not any('"result.written"' in line for line in events):
        raise SmokeError("events.jsonl 未记录 result.written")
    return {"run_dir": str(run_dir), "result_file": payload.get("result_file")}


def check_task_status(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run("task", "status", "task-runtime-smoke", "--project", "runtime-smoke")
    if payload.get("state") != "done" or payload.get("classification") != "done":
        raise SmokeError(f"task status 异常: {payload}")
    return {"classification": payload.get("classification")}


def check_task_logs(runner: CliRunner) -> dict[str, Any]:
    payload = runner.run("task", "logs", "task-runtime-smoke", "--project", "runtime-smoke", "--tail", "40")
    content = str(payload.get("content") or "")
    if "runtime-smoke-provider-ok" not in content:
        raise SmokeError(f"task logs 未返回 provider stdout: {payload}")
    return {"event_count": len(payload.get("events") or [])}


def check_adapter(paths: dict[str, Path]) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from apps.agentrun.adapter import AgentRunAdapter, AgentRunSpec  # noqa: PLC0415

    adapter = AgentRunAdapter(
        paths["local_runtime_dir"],
        runs_dir=paths["runs_dir"],
        conf_dir=paths["conf_dir"],
    )
    result = adapter.run(
        AgentRunSpec(
            runtime="cli",
            prompt_text="RUNTIME_SMOKE: adapter bridge",
            cwd=ROOT,
            runtime_dir=paths["local_runtime_dir"],
            run_id="task-adapter-smoke",
            timeout_seconds=30,
            provider_profile="runtime-smoke-cli",
        )
    )
    if result.get("state") != "done" or not result.get("result_valid"):
        raise SmokeError(f"adapter status 异常: {result}")
    wb_result = result.get("result") or {}
    if wb_result.get("status") != "success":
        raise SmokeError(f"adapter workbench result 异常: {wb_result}")
    agentrun_status = result.get("agentrun_status") or {}
    if agentrun_status.get("state") != "done":
        raise SmokeError(f"adapter AgentRun status 异常: {agentrun_status}")
    return {"run_id": result.get("run_id"), "agentrun_run_dir": result.get("agentrun_run_dir")}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SmokeError(f"文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SmokeError(f"JSON 不是 object: {path}")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
