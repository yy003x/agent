#!/usr/bin/env python3
"""Execute one input through AgentRun runtime: tmux / cli / api.

这个脚本只负责把一句输入交给 AgentRun,不在脚本侧实现 status/log/result 聚合。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("tmux", "cli", "api")
DEFAULT_PROFILES = {
    "tmux": "tmux-codex",
    "cli": "codex-cli",
    "api": "api-openai-gpt-4o-mini",
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
    parser.add_argument("--tail", type=int, default=120, help="tmux watch/logs 行数")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="tmux watch 轮询秒数")
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
    return cli.run(command, timeout=max(args.deadline_seconds + 30, 60))


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
        cleanup = True
        code = cli.run(start_cmd, timeout=120, isolate_interrupt=True)
        if code != 0:
            return code
        started = True

        send_cmd = ["session", "send", run_id, "--project", args.project, "--text", prompt]
        code = cli.run(send_cmd, timeout=30, isolate_interrupt=True)
        if code != 0:
            return code

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
        code = cli.run(watch_cmd, timeout=wait_seconds + 15 if wait_seconds > 0 else None, isolate_interrupt=True)
        if code != 0:
            return code
        emit_tmux_result(cli, args, run_id)
        return 0
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C,正在返回当前结果并关闭 tmux session。", file=sys.stderr)
        if started:
            emit_tmux_result(cli, args, run_id)
        return 130
    finally:
        if cleanup:
            cli.run(["session", "stop", run_id, "--project", args.project], timeout=30, silent=True)


def emit_tmux_result(cli: "AgentRunCLI", args: argparse.Namespace, run_id: str) -> None:
    result_file = cli.runs_dir / "sessions" / args.project / run_id / "result.json"
    print("")
    print("== AgentRun result ==", flush=True)
    if result_file.is_file():
        print(result_file.read_text(encoding="utf-8").rstrip())
        return
    print(f"result.json 尚未生成: {result_file}")
    print("")
    print("== AgentRun logs ==", flush=True)
    cli.run(["session", "logs", run_id, "--project", args.project, "--tail", str(args.tail)], timeout=30)


class AgentRunCLI:
    def __init__(self, *, conf_dir: Path, runs_dir: Path, json_output: bool) -> None:
        self.conf_dir = conf_dir
        self.runs_dir = runs_dir
        self.json_output = json_output
        self.pythonpath = _agentrun_pythonpath()

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
        cmd = [
            sys.executable,
            "-m",
            "agentrun.cli.main",
            "--conf-dir",
            str(self.conf_dir),
            "--runs-dir",
            str(self.runs_dir),
        ]
        if self.json_output:
            cmd.append("--json")
        cmd.extend(args)
        capture = quiet_success or silent
        if isolate_interrupt:
            return self._run_isolated(cmd, env=env, timeout=timeout, capture=capture, silent=silent, args=args)
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env=env,
                text=True,
                timeout=timeout,
                check=False,
                capture_output=capture,
            )
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
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
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
