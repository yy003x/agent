#!/usr/bin/env python3
"""Execute one input through AgentRun runtime: tmux / cli / api.

这个脚本不做 stub smoke,不生成临时 provider 配置。它使用当前项目的
config/agentrun 和 runs/agentrun,把同一段输入交给真实 AgentRun runtime 执行。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("tmux", "cli", "api")
DEFAULT_PROFILES = {
    "tmux": "tmux-codex",
    "cli": "codex-cli",
    "api": "api-openai-gpt-4o-mini",
}


class RuntimeRunError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
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
    cli = AgentRunCLI(conf_dir=conf_dir, runs_dir=runs_dir)

    try:
        if runtime == "tmux":
            report = run_tmux(cli, args, prompt=prompt, profile=profile, run_id=run_id, cwd=cwd)
        else:
            report = run_task(cli, args, runtime=runtime, prompt=prompt, profile=profile, run_id=run_id, cwd=cwd)
    except RuntimeRunError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"runtime 执行失败: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and not report.get("ok") else 0


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
    parser.add_argument("--tail", type=int, default=120, help="读取/监控日志行数")
    parser.add_argument("--watch-seconds", type=int, default=0, help="tmux watch 秒数;0 表示持续监控")
    parser.add_argument("--no-watch", action="store_true", help="tmux 只投递输入,不进入 watch")
    parser.add_argument("--no-submit", action="store_true", help="tmux 只粘贴输入,不发送回车")
    parser.add_argument("--no-force", action="store_true", help="不覆盖同 run_id 的已有记录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    parser.add_argument("--strict", action="store_true", help="task 非 done 或 session 非 running 时返回非 0")
    return parser.parse_args(argv)


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
    runtime: str,
    prompt: str,
    profile: str,
    run_id: str,
    cwd: Path,
) -> dict[str, Any]:
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
    if not args.no_force:
        command.append("--force")
    payload = cli.run_json(command, timeout=max(args.deadline_seconds + 30, 60))
    status = cli.run_json(["task", "status", run_id, "--project", args.project])
    logs = cli.run_json(["task", "logs", run_id, "--project", args.project, "--tail", str(args.tail)])
    result = _read_json_if_exists(str(payload.get("result_file") or ""))
    report = {
        "ok": payload.get("state") == "done",
        "runtime": runtime,
        "profile": profile,
        "project": args.project,
        "run_id": run_id,
        "state": payload.get("state"),
        "failure_reason": payload.get("failure_reason"),
        "result_file": payload.get("result_file"),
        "status": status,
        "result": result,
        "logs": logs.get("content", ""),
    }
    if not args.json:
        print_task_report(report)
    return report


def run_tmux(
    cli: "AgentRunCLI",
    args: argparse.Namespace,
    *,
    prompt: str,
    profile: str,
    run_id: str,
    cwd: Path,
) -> dict[str, Any]:
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
    if not args.no_force:
        start_cmd.append("--force")
    start = cli.run_json(start_cmd, timeout=120)
    send_cmd = ["session", "send", run_id, "--project", args.project, "--text", prompt]
    if args.no_submit:
        send_cmd.append("--no-submit")
    sent = cli.run_json(send_cmd, timeout=30)
    status = cli.run_json(["session", "status", run_id, "--project", args.project])
    report = {
        "ok": (status.get("classification") or status.get("state")) == "running",
        "runtime": "tmux",
        "profile": profile,
        "project": args.project,
        "run_id": run_id,
        "state": status.get("classification") or status.get("state"),
        "start": start,
        "sent": sent,
        "status": status,
    }
    if not args.json:
        print_tmux_report(report)
    if not args.no_watch and not args.json:
        watch_cmd = ["session", "watch", run_id, "--project", args.project, "--tail", str(args.tail)]
        if args.watch_seconds > 0:
            watch_cmd.extend(["--seconds", str(args.watch_seconds)])
        cli.run_passthrough(watch_cmd)
    elif not args.no_watch and args.json and args.watch_seconds > 0:
        report["watch"] = cli.run_json_lines(
            ["session", "watch", run_id, "--project", args.project, "--tail", str(args.tail), "--seconds", str(args.watch_seconds)],
            timeout=max(args.watch_seconds + 10, 15),
        )
    return report


def print_task_report(report: dict[str, Any]) -> None:
    print("")
    print(f"runtime: {report['runtime']}")
    print(f"profile: {report['profile']}")
    print(f"project: {report['project']}")
    print(f"run_id: {report['run_id']}")
    print(f"state: {report.get('state')}")
    if report.get("failure_reason"):
        print(f"failure_reason: {report.get('failure_reason')}")
    print(f"result_file: {report.get('result_file')}")
    result = report.get("result") or {}
    if result.get("summary"):
        print("")
        print("summary:")
        print(str(result["summary"]).rstrip())
    logs = str(report.get("logs") or "").rstrip()
    if logs:
        print("")
        print("logs:")
        print(logs)


def print_tmux_report(report: dict[str, Any]) -> None:
    start = report.get("start") or {}
    print("")
    print("已通过 AgentRun 投递到 tmux session。")
    print(f"project: {report['project']}")
    print(f"profile: {report['profile']}")
    print(f"run_id: {report['run_id']}")
    print(f"state: {report.get('state')}")
    print(f"tmux_session: {start.get('session')}")
    print(f"tmux_window: {start.get('window_name')}")
    print(f"ready: {start.get('ready')}")
    print(f"ready_reason: {start.get('ready_reason')}")
    print("")
    print("后续命令:")
    print(f"  {start.get('attach') or 'tmux attach -t <session>'}")
    print(
        "  PYTHONPATH="
        + str(_agentrun_pythonpath())
        + " python3 -m agentrun.cli.main --conf-dir "
        + str(report["status"].get("conf_dir", "config/agentrun"))
        + " --runs-dir runs/agentrun session watch "
        + str(report["run_id"])
        + " --project "
        + str(report["project"])
    )


class AgentRunCLI:
    def __init__(self, *, conf_dir: Path, runs_dir: Path) -> None:
        self.conf_dir = conf_dir
        self.runs_dir = runs_dir
        self.pythonpath = _agentrun_pythonpath()

    def run_json(self, args: list[str], *, timeout: int = 120) -> dict[str, Any]:
        proc = self._run([*args, "--json"], timeout=timeout, capture=True)
        payload = _decode_json(proc.stdout, args)
        if proc.returncode != 0:
            raise RuntimeRunError(f"agentrun 命令失败: {' '.join(args)}; payload={payload}; stderr={proc.stderr.strip()}")
        return payload

    def run_json_lines(self, args: list[str], *, timeout: int = 120) -> list[dict[str, Any]]:
        proc = self._run([*args, "--json"], timeout=timeout, capture=True)
        if proc.returncode != 0:
            raise RuntimeRunError(f"agentrun 命令失败: {' '.join(args)}; stderr={proc.stderr.strip()}")
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    def run_passthrough(self, args: list[str]) -> None:
        proc = self._run(args, timeout=None, capture=False)
        if proc.returncode != 0:
            raise RuntimeRunError(f"agentrun 命令失败: {' '.join(args)}")

    def _run(self, args: list[str], *, timeout: int | None, capture: bool) -> subprocess.CompletedProcess[str]:
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
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=capture,
            timeout=timeout,
            check=False,
        )


def _decode_json(stdout: str, args: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeRunError(f"agentrun 输出不是 JSON: {' '.join(args)}; stdout={stdout!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeRunError(f"agentrun JSON 输出不是 object: {' '.join(args)}; stdout={stdout!r}")
    return payload


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


def _read_json_if_exists(path_text: str) -> dict[str, Any] | None:
    if not path_text:
        return None
    path = Path(path_text)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
