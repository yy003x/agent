#!/usr/bin/env python3
"""Execute one input through AgentRun runtime: tmux / cli / api.

这个脚本只负责把一句输入交给 AgentRun,不在脚本侧实现 status/log/result 聚合。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("tmux", "cli", "api")
DEFAULT_PROFILES = {
    "tmux": "tmux-codex",
    "cli": "codex-cli",
    "api": "api-openrouter-openai-glm-5.1",
}
DEFAULT_TMUX_WAIT_SECONDS = 0


def _ensure_supported_python() -> None:
    if sys.version_info >= (3, 11):
        return
    for candidate in (ROOT / ".venv" / "bin" / "python3", ROOT.parent / ".venv" / "bin" / "python3"):
        if candidate.exists() and str(candidate) != sys.executable:
            os.execv(str(candidate), [str(candidate), *sys.argv])
    raise SystemExit("AgentRun 需要 Python 3.11+,当前解释器版本过低")


def main(argv: list[str] | None = None) -> int:
    _ensure_supported_python()
    args = _parse_args(argv)
    runtime = args.runtime_option or args.runtime
    if not runtime:
        raise SystemExit("必须指定 runtime: tmux / cli / api")
    prompt = _load_prompt(args)
    if not prompt.strip():
        raise SystemExit("输入不能为空")

    conf_dir = _resolve_path(args.conf_dir)
    runs_dir = _resolve_path(args.runs_dir)
    cwd = _resolve_path(args.cwd)
    profile = args.profile or DEFAULT_PROFILES[runtime]
    run_id = args.run_id or _new_run_id(runtime)
    cli = AgentRunCLI(conf_dir=conf_dir, runs_dir=runs_dir, json_output=args.json)
    profile_config = require_configured_profile(cli, args, runtime=runtime, profile=profile)
    emit_config_log(args, profile_config)

    if runtime == "tmux":
        return run_tmux(cli, args, prompt=prompt, profile=profile, run_id=run_id, cwd=cwd)
    return run_task(cli, args, prompt=prompt, profile=profile, run_id=run_id, cwd=cwd)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用真实 AgentRun runtime 执行一段输入",
        epilog=(
            "示例:\n"
            "  scripts/runtime_smoke.py tmux \"看下当前项目实现了什么\"\n"
            "  scripts/runtime_smoke.py cli \"看下当前项目实现了什么\"\n"
            "  scripts/runtime_smoke.py api \"看下当前项目实现了什么\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("runtime", nargs="?", choices=RUNTIMES, help="执行方式: tmux / cli / api")
    parser.add_argument("text", nargs="*", help="输入文本")
    parser.add_argument("--runtime", dest="runtime_option", choices=RUNTIMES, help="执行方式,等价于第一个位置参数")
    parser.add_argument("--prompt-file", default="", help="从文件读取输入文本")
    parser.add_argument("--profile", default="", help="AgentRun profile,默认按 runtime 选择")
    parser.add_argument("--project", default=os.environ.get("AGENTRUN_PROJECT", "agent"), help="project id,默认 agent")
    parser.add_argument("--conf-dir", default=str(ROOT / "config" / "agentrun"), help="AgentRun 配置目录")
    parser.add_argument("--runs-dir", default=str(ROOT / "runs" / "agentrun"), help="AgentRun runs 目录")
    parser.add_argument("--cwd", default=str(ROOT), help="runtime 执行工作目录")
    parser.add_argument("--run-id", default="", help="指定 run_id;默认自动生成")
    parser.add_argument("--deadline-seconds", type=int, default=300, help="cli/api task 超时时间")
    parser.add_argument("--tmux-wait-seconds", type=int, default=DEFAULT_TMUX_WAIT_SECONDS, help="tmux watch 秒数;0 表示持续监控")
    parser.add_argument("--tail", type=int, default=120, help="watch 内部读取行数;默认不展示")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="watch 轮询秒数")
    parser.add_argument("--force", action="store_true", help="透传 AgentRun --force")
    parser.add_argument("--json", action="store_true", help="透传 AgentRun --json")
    return parser.parse_intermixed_args(argv)


def _load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    if not sys.stdin.isatty():
        return sys.stdin.read().rstrip("\n")
    return "看下当前项目实现了什么"


def require_configured_profile(
    cli: "AgentRunCLI",
    args: argparse.Namespace,
    *,
    runtime: str,
    profile: str,
) -> dict[str, object]:
    command = ["config", "choices", "--project", args.project, "--all"]
    emit_command_log(args, "配置校验命令", cli.command(command, as_json=True))
    payload = cli.run_json(command, timeout=30)
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise SystemExit("AgentRun config choices 输出格式异常")
    for item in choices:
        if not isinstance(item, dict) or item.get("id") != profile:
            continue
        transport = str(item.get("transport") or "")
        if transport != runtime:
            raise SystemExit(f"runtime profile transport 不匹配: profile={profile} transport={transport} expected={runtime}")
        return item
    raise SystemExit(f"runtime profile 未从 AgentRun 配置解析到: {profile}")


def emit_config_log(args: argparse.Namespace, profile_config: dict[str, object]) -> None:
    if args.json:
        return
    detail = profile_config.get("detail")
    provider_command: list[str] = []
    if isinstance(detail, dict):
        command = str(detail.get("command") or detail.get("model") or "")
        detail_args = detail.get("args")
        if command:
            provider_command = [command, *[str(item) for item in detail_args]] if isinstance(detail_args, list) else [command]
    provider = str(profile_config.get("provider_name") or "")
    suffix = f" provider={provider}" if provider else ""
    suffix += f" command={format_shell_command(provider_command)}" if provider_command else ""
    emit_runtime_log(
        args,
        "runtime 配置校验通过: "
        f"profile={profile_config.get('id')} transport={profile_config.get('transport')}{suffix}",
    )


def run_task(
    cli: "AgentRunCLI",
    args: argparse.Namespace,
    *,
    prompt: str,
    profile: str,
    run_id: str,
    cwd: Path,
) -> int:
    prompt_file = _write_prompt_file(cli.runs_dir, run_id, prompt)
    command = [
        "task",
        "run",
        "--project",
        args.project,
        "--profile",
        profile,
        "--prompt-file",
        str(prompt_file),
        "--run-id",
        run_id,
        "--cwd",
        str(cwd),
        "--deadline-seconds",
        str(args.deadline_seconds),
    ]
    if args.force:
        command.append("--force")
    timeout = max(args.deadline_seconds + 30, 60)
    if args.json:
        return cli.run(command, timeout=timeout)

    proc: subprocess.Popen[str] | None = None
    try:
        emit_runtime_log(args, f"准备启动 runtime run_id={run_id}")
        emit_command_log(args, "task run 命令", cli.command(command))
        proc = cli.popen(command, capture=True)
        emit_runtime_log(args, "runtime 已启动,开始打印 AgentRun task 日志;按 Ctrl+C 取消 task 并终止 CLI runtime")
        watch_cmd = [
            "task",
            "watch",
            run_id,
            "--project",
            args.project,
            "--tail",
            str(args.tail),
            "--poll-seconds",
            str(args.poll_seconds),
        ]
        emit_command_log(args, "task watch 命令", cli.command(watch_cmd))
        code = cli.run(watch_cmd, timeout=timeout, isolate_interrupt=True)
        proc_code, stdout, stderr = cli.wait_process(proc, timeout=10)
        emit_process_output(stdout, stderr)
        emit_task_result(cli, args, run_id)
        return proc_code if proc_code != 0 else code
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C,正在取消 task 并终止 CLI runtime。", file=sys.stderr)
        if proc is not None:
            proc_code, stdout, stderr = cli.stop_process_group(proc, timeout=5)
            emit_process_output(stdout, stderr)
            if proc_code not in (0, 130, -signal.SIGINT):
                emit_runtime_log(args, f"runtime 进程退出码: {proc_code}")
        if task_state(cli, args, run_id) not in {"done", "failed", "blocked", "cancelled"}:
            cancel_cmd = ["task", "cancel", run_id, "--project", args.project]
            emit_command_log(args, "task cancel 命令", cli.command(cancel_cmd))
            cli.run(cancel_cmd, timeout=30, silent=True)
        emit_task_result(cli, args, run_id)
        return 130


def run_tmux(
    cli: "AgentRunCLI",
    args: argparse.Namespace,
    *,
    prompt: str,
    profile: str,
    run_id: str,
    cwd: Path,
) -> int:
    start_cmd = [
        "session",
        "start",
        "--project",
        args.project,
        "--profile",
        profile,
        "--run-id",
        run_id,
        "--cwd",
        str(cwd),
    ]
    if args.force:
        start_cmd.append("--force")
    cleanup = False
    started = False
    try:
        emit_runtime_log(args, "准备启动 runtime")
        cleanup = True
        code = cli.run(start_cmd, timeout=120, quiet_success=True, isolate_interrupt=True)
        if code != 0:
            return code
        started = True
        emit_runtime_log(args, "runtime 已启动,准备投递输入")

        send_cmd = ["session", "send", run_id, "--project", args.project, "--text", prompt]
        code = cli.run(send_cmd, timeout=30, quiet_success=True, isolate_interrupt=True)
        if code != 0:
            return code
        emit_runtime_log(args, "输入已投递,开始等待 result;按 Ctrl+C 返回当前 result 并关闭运行会话")

        wait_seconds = max(int(args.tmux_wait_seconds), 0)
        watch_cmd = [
            "session",
            "watch",
            run_id,
            "--project",
            args.project,
            "--tail",
            str(args.tail),
            "--poll-seconds",
            str(args.poll_seconds),
        ]
        if wait_seconds > 0:
            watch_cmd.extend(["--seconds", str(wait_seconds)])
        code = cli.run(watch_cmd, timeout=wait_seconds + 15 if wait_seconds > 0 else None, silent=True, isolate_interrupt=True)
        if code != 0:
            return code
        emit_tmux_result(cli, args, run_id)
        return 0
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C,正在返回当前 result 并关闭 session。", file=sys.stderr)
        if started:
            emit_tmux_result(cli, args, run_id)
        return 130
    finally:
        if cleanup:
            emit_runtime_log(args, "正在关闭运行会话")
            cli.run(["session", "stop", run_id, "--project", args.project], timeout=30, silent=True)
            emit_runtime_log(args, "运行会话已关闭")


def emit_runtime_log(args: argparse.Namespace, message: str) -> None:
    if args.json:
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[runtime-smoke {stamp}] {message}", flush=True)


def emit_command_log(args: argparse.Namespace, label: str, command: list[str]) -> None:
    emit_runtime_log(args, f"{label}: {format_shell_command(command)}")


def format_shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def emit_tmux_result(cli: "AgentRunCLI", args: argparse.Namespace, run_id: str) -> None:
    result_file = cli.runs_dir / "sessions" / args.project / run_id / "result.json"
    print("")
    print("== AgentRun result ==", flush=True)
    if result_file.is_file():
        print(result_file.read_text(encoding="utf-8").rstrip())
        return
    print("result.json 尚未生成")


def emit_task_result(cli: "AgentRunCLI", args: argparse.Namespace, run_id: str) -> None:
    run_dir = cli.runs_dir / "tasks" / args.project / run_id
    result_file = run_dir / "result.json"
    status_file = run_dir / "status.json"
    print("")
    print("== AgentRun task result ==", flush=True)
    if result_file.is_file():
        print(result_file.read_text(encoding="utf-8").rstrip())
        return
    print("result.json 尚未生成")
    if status_file.is_file():
        print("")
        print("== AgentRun task status ==", flush=True)
        print(status_file.read_text(encoding="utf-8").rstrip())


def emit_process_output(stdout: str, stderr: str) -> None:
    if stdout.strip():
        print("")
        print("== AgentRun task run ==", flush=True)
        print(stdout.rstrip())
    if stderr.strip():
        print("")
        print("== AgentRun task stderr ==", file=sys.stderr, flush=True)
        print(stderr.rstrip(), file=sys.stderr)


def task_state(cli: "AgentRunCLI", args: argparse.Namespace, run_id: str) -> str:
    status_file = cli.runs_dir / "tasks" / args.project / run_id / "status.json"
    if not status_file.is_file():
        return ""
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("state") or "")


class AgentRunCLI:
    def __init__(self, *, conf_dir: Path, runs_dir: Path, json_output: bool) -> None:
        self.conf_dir = conf_dir
        self.runs_dir = runs_dir
        self.json_output = json_output
        self.pythonpath = _agentrun_pythonpath()

    def command(self, args: list[str], *, as_json: bool = False) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "agentrun.cli.main",
            "--conf-dir",
            str(self.conf_dir),
            "--runs-dir",
            str(self.runs_dir),
        ]
        if as_json or self.json_output:
            cmd.append("--json")
        cmd.extend(args)
        return cmd

    def run(
        self,
        args: list[str],
        *,
        timeout: int | None,
        quiet_success: bool = False,
        silent: bool = False,
        isolate_interrupt: bool = False,
    ) -> int:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.pythonpath) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = self.command(args)
        capture = quiet_success or silent
        if isolate_interrupt:
            return self._run_isolated(cmd, env=env, timeout=timeout, capture=capture, silent=silent, args=args)
        run_kwargs = {
            "cwd": ROOT,
            "env": env,
            "text": True,
            "timeout": timeout,
            "check": False,
        }
        if silent:
            run_kwargs["stdout"] = subprocess.DEVNULL
            run_kwargs["stderr"] = subprocess.DEVNULL
        else:
            run_kwargs["capture_output"] = capture
        try:
            proc = subprocess.run(cmd, **run_kwargs)
        except subprocess.TimeoutExpired:
            if not silent:
                print(f"agentrun 命令超时: {' '.join(args)}", file=sys.stderr)
            return 124
        if silent:
            return proc.returncode
        if quiet_success and proc.returncode != 0:
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, end="", file=sys.stderr)
        return proc.returncode

    def popen(self, args: list[str], *, capture: bool) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.pythonpath) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = self.command(args)
        return subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            start_new_session=True,
        )

    def wait_process(self, proc: subprocess.Popen[str], *, timeout: int | None) -> tuple[int, str, str]:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return proc.returncode, stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            code, stdout, stderr = self.stop_process_group(proc, timeout=3)
            return code if code is not None else 124, stdout, stderr

    def stop_process_group(self, proc: subprocess.Popen[str], *, timeout: int = 3) -> tuple[int, str, str]:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return proc.returncode, stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            return proc.returncode, stdout or "", stderr or ""

    def run_json(self, args: list[str], *, timeout: int | None) -> dict[str, object]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.pythonpath) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = self.command(args, as_json=True)
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            raise SystemExit(f"读取 AgentRun 配置失败: {stderr or proc.stdout.strip()}")
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"AgentRun 配置输出不是 JSON: {proc.stdout!r}") from exc
        if not isinstance(payload, dict):
            raise SystemExit("AgentRun 配置输出不是 JSON object")
        return payload

    def _run_isolated(
        self,
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: int | None,
        capture: bool,
        silent: bool,
        args: list[str],
    ) -> int:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.DEVNULL if silent else subprocess.PIPE if capture else None,
            stderr=subprocess.DEVNULL if silent else subprocess.PIPE if capture else None,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            raise
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            if not silent:
                print(f"agentrun 命令超时: {' '.join(args)}", file=sys.stderr)
            return 124
        if silent:
            return proc.returncode
        if capture and proc.returncode != 0:
            if stdout:
                print(stdout, end="")
            if stderr:
                print(stderr, end="", file=sys.stderr)
        return proc.returncode


def _agentrun_pythonpath() -> Path:
    src = ROOT / "apps" / "agentrun" / "src"
    if (src / "agentrun").is_dir():
        return src
    return ROOT / "apps" / "agentrun"


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _new_run_id(runtime: str) -> str:
    prefix = "session" if runtime == "tmux" else "task"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{runtime}-{stamp}-{os.getpid()}"


def _write_prompt_file(runs_dir: Path, run_id: str, prompt: str) -> Path:
    inputs_dir = runs_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = inputs_dir / f"{run_id}.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    return prompt_file


if __name__ == "__main__":
    raise SystemExit(main())
