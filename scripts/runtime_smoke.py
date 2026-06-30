#!/usr/bin/env python3
"""AgentRun runtime smoke test for cli / api / tmux.

脚本只创建临时配置和本地 stub,不写项目级 config/agentrun,不依赖真实 API key。
tmux 分支也只调用 AgentRun 的 session start/send/watch/stop,不直接读取 tmux 运行内容。
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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENTRUN_APP = ROOT / "apps" / "agentrun"
MIN_PYTHON = (3, 11)
RUNTIMES = ("cli", "api", "tmux")


class SmokeError(RuntimeError):
    """runtime smoke 失败。"""


def main(argv: Any = None) -> int:
    args = _parse_args(argv)
    python = _select_python()
    if Path(python).resolve() != Path(sys.executable).resolve() and not os.environ.get("AGENTRUN_SMOKE_REEXEC"):
        env = {**os.environ, "AGENTRUN_SMOKE_REEXEC": "1"}
        proc = subprocess.run([python, str(Path(__file__).resolve()), *sys.argv[1:]], env=env, check=False)
        return proc.returncode

    selected = list(RUNTIMES) if args.runtime == "all" else [args.runtime]
    work_root = Path(args.work_dir).expanduser().resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="agentrun-smoke-")
    )
    created_temp = args.work_dir is None
    api_server: StubApiServer | None = None
    report: dict[str, Any] = {
        "ok": False,
        "repo": str(ROOT),
        "runtime": args.runtime,
        "selected": selected,
        "work_dir": str(work_root),
        "checks": [],
    }

    try:
        _require_runtime_layout()
        if "api" in selected:
            api_server = StubApiServer.start()
        paths = _prepare_smoke_workspace(
            work_root,
            prompt_text=args.prompt,
            api_base_url=api_server.base_url if api_server else "http://127.0.0.1:9/v1",
        )
        runner = CliRunner(
            python=sys.executable,
            conf_dir=paths["conf_dir"],
            runs_dir=paths["runs_dir"],
            extra_env={"AGENTRUN_SMOKE_API_KEY": "smoke-key"},
        )
        _run_check(report, args, "layout", check_layout)
        _run_check(report, args, "doctor", lambda: check_doctor(runner, selected))
        _run_check(report, args, "profiles", lambda: check_profiles(runner, selected))

        for runtime in selected:
            _run_check(report, args, f"{runtime}.validate_config", lambda runtime=runtime: check_validate_config(runner, runtime))
            _run_check(report, args, f"{runtime}.choices", lambda runtime=runtime: check_choices(runner, runtime))
            if runtime == "cli":
                _run_check(report, args, "cli.task", lambda: check_task_runtime(runner, paths, "cli"))
                _run_check(report, args, "cli.adapter", lambda: check_adapter(paths))
            elif runtime == "api":
                _run_check(report, args, "api.task", lambda: check_task_runtime(runner, paths, "api"))
                _run_check(report, args, "api.http_stub", lambda: check_api_stub(api_server))
            elif runtime == "tmux":
                _run_check(
                    report,
                    args,
                    "tmux.session",
                    lambda: check_tmux_session(runner, paths, watch_seconds=args.watch_seconds),
                )

        report["ok"] = True
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("")
            print("runtime smoke 通过")
            print(f"runtime: {args.runtime}")
            print(f"work_dir: {work_root}")
        return 0
    except Exception as exc:  # noqa: BLE001 CLI 边界统一输出
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
        if api_server is not None:
            api_server.stop()
        if created_temp and not args.keep_temp:
            shutil.rmtree(work_root, ignore_errors=True)


def _parse_args(argv: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试当前 AgentRun runtime 主链路")
    parser.add_argument(
        "--runtime",
        choices=(*RUNTIMES, "all"),
        default="all",
        help="选择要测试的 runtime,默认 all",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时目录用于排查")
    parser.add_argument("--work-dir", default="", help="指定隔离测试目录；默认使用系统临时目录")
    parser.add_argument("--prompt", default="看下当前项目实现了什么", help="测试 prompt 文本")
    parser.add_argument("--watch-seconds", type=int, default=2, help="tmux watch 监控秒数")
    return parser.parse_args(argv)


def _run_check(report: dict[str, Any], args: argparse.Namespace, name: str, check) -> None:
    try:
        detail = check()
        report["checks"].append({"name": name, "ok": True, **(detail or {})})
        if not args.json:
            print(f"  ✓ {name}")
    except Exception as exc:  # noqa: BLE001
        report["checks"].append({"name": name, "ok": False, "error": str(exc)})
        raise


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


def _prepare_smoke_workspace(work_root: Path, *, prompt_text: str, api_base_url: str) -> dict[str, Path]:
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

    cli_stub = work_root / "stub_cli_provider.py"
    cli_stub.write_text(_cli_stub_source(), encoding="utf-8")
    tmux_stub = work_root / "stub_tmux_session.py"
    tmux_stub.write_text(_tmux_stub_source(), encoding="utf-8")
    prompt = work_root / "prompt.md"
    prompt.write_text(f"RUNTIME_SMOKE: {prompt_text}\n", encoding="utf-8")

    (conf_dir / "runtime.yaml").write_text(
        "default_project: runtime-smoke\n"
        "default_profile: runtime-smoke-cli\n"
        "max_concurrency: 1\n",
        encoding="utf-8",
    )
    (providers_dir / "cli.yaml").write_text(
        "runtime_smoke:\n"
        "  profile: runtime-smoke-cli\n"
        f"  command: {_yaml_str(sys.executable)}\n"
        "  args:\n"
        f"    - {_yaml_str(str(cli_stub))}\n"
        "  timeout_seconds: 30\n"
        "  result_contract: required\n",
        encoding="utf-8",
    )
    (providers_dir / "api.yaml").write_text(
        "smoke:\n"
        "  protocol: openai\n"
        f"  base_url: {_yaml_str(api_base_url)}\n"
        "  api_key_env: AGENTRUN_SMOKE_API_KEY\n"
        "  models:\n"
        "    runtime-smoke-model:\n"
        "      profile: runtime-smoke-api\n"
        "      model: runtime-smoke-model\n"
        "      label: Runtime Smoke API\n"
        "      timeout_seconds: 30\n"
        "      result_contract: required\n",
        encoding="utf-8",
    )
    (providers_dir / "tmux.yaml").write_text(
        "defaults:\n"
        f"  session_name: agentrun-smoke-{os.getpid()}\n"
        "  session_wait_ready: true\n"
        "  session_ready_timeout_seconds: 3\n"
        "  session_ready_settle_seconds: 0.05\n"
        "  poll_interval_seconds: 0.05\n"
        "codex:\n"
        "  profile: runtime-smoke-tmux\n"
        f"  command: {_yaml_str(sys.executable)}\n"
        "  args:\n"
        f"    - {_yaml_str(str(tmux_stub))}\n"
        "  label: Runtime Smoke Tmux\n"
        "  result_contract: optional\n",
        encoding="utf-8",
    )
    return {
        "work_root": work_root,
        "conf_dir": conf_dir,
        "runs_dir": runs_dir,
        "prompt": prompt,
        "local_runtime_dir": local_runtime_dir,
    }


def _cli_stub_source() -> str:
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
            "summary": f"runtime smoke cli ok: {len(prompt)} bytes",
            "artifacts": [],
            "errors": [],
            "validation": {"commands": ["stub_cli_provider.py"], "passed": True},
        }
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
        print("runtime-smoke-cli-ok")
        """
    ).lstrip()


def _tmux_stub_source() -> str:
    return textwrap.dedent(
        """
        import os
        import sys

        print("runtime-smoke-tmux-session-ready", flush=True)
        os.execv("/bin/sh", ["/bin/sh"])
        sys.exit(127)
        """
    ).lstrip()


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class StubApiServer:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self.server = server
        self.thread = thread

    @classmethod
    def start(cls) -> "StubApiServer":
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw or "{}")
                self.server.requests.append(  # type: ignore[attr-defined]
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization", ""),
                        "payload": payload,
                    }
                )
                if self.path != "/v1/chat/completions":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "runtime-smoke-api-ok: "
                                    + str(payload.get("model") or "missing-model")
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return cls(server, thread)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(getattr(self.server, "requests", []))

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class CliRunner:
    def __init__(self, *, python: str, conf_dir: Path, runs_dir: Path, extra_env: dict[str, str] | None = None) -> None:
        self.python = python
        self.conf_dir = conf_dir
        self.runs_dir = runs_dir
        self.extra_env = extra_env or {}

    def run(self, *args: str, timeout: int = 60) -> dict[str, Any]:
        proc = self._run(*args, json_output=True, timeout=timeout)
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

    def run_watch_json_lines(self, *args: str, timeout: int = 15) -> list[dict[str, Any]]:
        proc = self._run(*args, json_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise SmokeError(f"CLI watch 失败: {' '.join(args)}; stderr={proc.stderr.strip()!r}")
        out: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def _run(self, *args: str, json_output: bool, timeout: int) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(self.extra_env)
        env["PYTHONPATH"] = str(AGENTRUN_APP) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = [
            self.python,
            "-m",
            "agentrun.cli.main",
            "--conf-dir",
            str(self.conf_dir),
            "--runs-dir",
            str(self.runs_dir),
        ]
        if json_output:
            cmd.append("--json")
        cmd.extend(args)
        return subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )


PROFILE = {
    "cli": "runtime-smoke-cli",
    "api": "runtime-smoke-api",
    "tmux": "runtime-smoke-tmux",
}


def check_layout() -> dict[str, Any]:
    _require_runtime_layout()
    return {"owner": "apps/agentrun"}


def check_doctor(runner: CliRunner, selected: list[str]) -> dict[str, Any]:
    payload = runner.run("doctor")
    providers = payload.get("providers", {})
    for runtime in selected:
        item = providers.get(PROFILE[runtime])
        if not payload.get("ok") or not item:
            raise SmokeError(f"doctor 没有加载 {PROFILE[runtime]}: {payload}")
        if item.get("transport") != runtime or not item.get("implemented"):
            raise SmokeError(f"doctor provider 状态异常: {item}")
    return {"profiles": payload.get("profiles")}


def check_profiles(runner: CliRunner, selected: list[str]) -> dict[str, Any]:
    payload = runner.run("profiles")
    profiles = payload.get("profiles", [])
    ids = {item.get("id") for item in profiles}
    missing = [PROFILE[runtime] for runtime in selected if PROFILE[runtime] not in ids]
    if missing:
        raise SmokeError("profiles 未返回: " + ", ".join(missing))
    return {"profile_count": len(profiles)}


def check_validate_config(runner: CliRunner, runtime: str) -> dict[str, Any]:
    payload = runner.run(
        "config",
        "validate",
        "--project",
        "runtime-smoke",
        "--provider",
        runtime,
        "--profile",
        PROFILE[runtime],
        timeout=60,
    )
    if not payload.get("ok") or payload.get("validated") != 1:
        raise SmokeError(f"config validate 失败: {payload}")
    return {"status_file": payload.get("status_file")}


def check_choices(runner: CliRunner, runtime: str) -> dict[str, Any]:
    payload = runner.run("config", "choices", "--project", "runtime-smoke")
    choices = payload.get("choices", [])
    if not any(item.get("id") == PROFILE[runtime] and item.get("validated") for item in choices):
        raise SmokeError(f"config choices 未包含已验证 profile {PROFILE[runtime]}: {payload}")
    return {"choice_count": len(choices)}


def check_task_runtime(runner: CliRunner, paths: dict[str, Path], runtime: str) -> dict[str, Any]:
    run_id = f"task-runtime-smoke-{runtime}"
    payload = runner.run(
        "task",
        "run",
        "--project",
        "runtime-smoke",
        "--profile",
        PROFILE[runtime],
        "--prompt-file",
        str(paths["prompt"]),
        "--run-id",
        run_id,
        "--cwd",
        str(ROOT),
        "--deadline-seconds",
        "30",
        "--force",
        timeout=60,
    )
    if payload.get("state") != "done":
        raise SmokeError(f"{runtime} task run 未完成: {payload}")
    run_dir = paths["runs_dir"] / "tasks" / "runtime-smoke" / run_id
    request = _read_json(run_dir / "request.json")
    status = _read_json(run_dir / "status.json")
    result = _read_json(run_dir / "result.json")
    output_log = (run_dir / "output.log").read_text(encoding="utf-8")
    marker = f"runtime-smoke-{runtime}-ok"
    if request.get("provider_profile") != PROFILE[runtime] or request.get("provider") != runtime:
        raise SmokeError(f"request.json provider 异常: {request}")
    if status.get("state") != "done":
        raise SmokeError(f"status.json state 异常: {status}")
    if result.get("outcome") != "succeeded":
        raise SmokeError(f"result.json outcome 异常: {result}")
    if marker not in output_log and marker not in json.dumps(result, ensure_ascii=False):
        raise SmokeError(f"未看到 {marker}: output_log={output_log!r}; result={result}")
    return {"run_dir": str(run_dir), "result_file": payload.get("result_file")}


def check_api_stub(api_server: StubApiServer | None) -> dict[str, Any]:
    if api_server is None:
        raise SmokeError("api stub server 未启动")
    requests = api_server.requests
    if not any(item.get("auth") == "Bearer smoke-key" for item in requests):
        raise SmokeError(f"api stub 未收到带认证请求: {requests}")
    if not any((item.get("payload") or {}).get("model") == "runtime-smoke-model" for item in requests):
        raise SmokeError(f"api stub 未收到 runtime-smoke-model 请求: {requests}")
    return {"requests": len(requests)}


def check_tmux_session(runner: CliRunner, paths: dict[str, Path], *, watch_seconds: int) -> dict[str, Any]:
    if shutil.which("tmux") is None:
        raise SmokeError("tmux 未安装")
    run_id = "session-runtime-smoke-tmux"
    start = runner.run(
        "session",
        "start",
        "--project",
        "runtime-smoke",
        "--profile",
        PROFILE["tmux"],
        "--run-id",
        run_id,
        "--cwd",
        str(ROOT),
        "--force",
        timeout=15,
    )
    try:
        if start.get("state") != "running" or not start.get("ready"):
            raise SmokeError(f"tmux session 未就绪: {start}")
        runner.run(
            "session",
            "send",
            run_id,
            "--project",
            "runtime-smoke",
            "--text",
            "echo runtime-smoke-tmux-ok",
            timeout=10,
        )
        ticks = runner.run_watch_json_lines(
            "session",
            "watch",
            run_id,
            "--project",
            "runtime-smoke",
            "--tail",
            "80",
            "--seconds",
            str(max(watch_seconds, 1)),
            timeout=max(watch_seconds + 5, 8),
        )
        logs = runner.run("session", "logs", run_id, "--project", "runtime-smoke", "--tail", "80")
        content = str(logs.get("content") or "")
        if "runtime-smoke-tmux-ok" not in content:
            raise SmokeError(f"tmux logs 未包含 marker: {content!r}; ticks={ticks}")
        status = runner.run("session", "status", run_id, "--project", "runtime-smoke")
        if status.get("classification") != "running":
            raise SmokeError(f"tmux session 状态异常: {status}")
        return {"run_id": run_id, "watch_ticks": len(ticks), "session": start.get("session")}
    finally:
        runner.run("session", "stop", run_id, "--project", "runtime-smoke", timeout=10)


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
            provider_profile=PROFILE["cli"],
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
