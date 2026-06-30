"""agentrun <verb>:doctor / profiles / task / prune(见 design/08 §4)。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from agentrun.core.run import SESSION, TASK
from agentrun.kernel import AgentRuntime

_NO_EMIT = object()


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_global_options(sys.argv[1:] if argv is None else list(argv))
    parser = argparse.ArgumentParser(prog="agentrun")
    parser.add_argument("--conf-dir", default=None, help="调用方配置目录(覆盖项目默认)")
    parser.add_argument("--runs-dir", default=None, help="runs 目录")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor")

    p_profiles = sub.add_parser("profiles")
    p_profiles.add_argument("action", choices=["list"], nargs="?", default="list")

    p_config = sub.add_parser("config")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)
    p_config_choices = config_sub.add_parser("choices")
    p_config_choices.add_argument("--project", default=None)
    p_config_choices.add_argument("--all", action="store_true", help="包含未验证或验证失败配置")
    p_config_validate = config_sub.add_parser("validate")
    p_config_validate.add_argument("--project", default=None)
    p_config_validate.add_argument("--provider", choices=["api", "cli", "tmux"], default=None)
    p_config_validate.add_argument("--name", default=None)
    p_config_validate.add_argument("--profile", default=None)

    p_task = sub.add_parser("task")
    task_sub = p_task.add_subparsers(dest="task_cmd", required=True)
    p_run = task_sub.add_parser("run")
    p_run.add_argument("--profile", default=None)
    p_run.add_argument("--prompt-file", required=True)
    p_run.add_argument("--project", default=None)
    p_run.add_argument("--result-schema", default="")
    p_run.add_argument("--run-id", default=None)
    p_run.add_argument("--cwd", default=None)
    p_run.add_argument("--deadline-seconds", type=int, default=None)
    p_run.add_argument("--allowed-action", action="append", default=[])
    p_run.add_argument("--forbidden-action", action="append", default=[])
    p_run.add_argument("--force", action="store_true")
    p_status = task_sub.add_parser("status")
    p_status.add_argument("run_id")
    p_status.add_argument("--project", default=None)
    p_logs = task_sub.add_parser("logs")
    p_logs.add_argument("run_id")
    p_logs.add_argument("--project", default=None)
    p_logs.add_argument("--tail", type=int, default=120)
    p_watch = task_sub.add_parser("watch")
    p_watch.add_argument("run_id")
    p_watch.add_argument("--project", default=None)
    p_watch.add_argument("--tail", type=int, default=120)
    p_watch.add_argument("--seconds", type=int, default=0)
    p_watch.add_argument("--poll-seconds", type=float, default=1.0)
    p_cancel = task_sub.add_parser("cancel")
    p_cancel.add_argument("run_id")
    p_cancel.add_argument("--project", default=None)

    p_session = sub.add_parser("session")
    session_sub = p_session.add_subparsers(dest="session_cmd", required=True)
    p_session_start = session_sub.add_parser("start")
    p_session_start.add_argument("--profile", default="tmux-codex")
    p_session_start.add_argument("--project", default=None)
    p_session_start.add_argument("--run-id", default=None)
    p_session_start.add_argument("--cwd", default=None)
    p_session_start.add_argument("--allowed-action", action="append", default=[])
    p_session_start.add_argument("--forbidden-action", action="append", default=[])
    p_session_start.add_argument("--force", action="store_true")
    p_session_status = session_sub.add_parser("status")
    p_session_status.add_argument("run_id")
    p_session_status.add_argument("--project", default=None)
    p_session_send = session_sub.add_parser("send")
    p_session_send.add_argument("run_id")
    p_session_send.add_argument("--text", required=True)
    p_session_send.add_argument("--project", default=None)
    p_session_send.add_argument("--no-submit", action="store_true")
    p_session_logs = session_sub.add_parser("logs")
    p_session_logs.add_argument("run_id")
    p_session_logs.add_argument("--project", default=None)
    p_session_logs.add_argument("--tail", type=int, default=120)
    p_session_watch = session_sub.add_parser("watch")
    p_session_watch.add_argument("run_id")
    p_session_watch.add_argument("--project", default=None)
    p_session_watch.add_argument("--tail", type=int, default=120)
    p_session_watch.add_argument("--seconds", type=int, default=0)
    p_session_watch.add_argument("--poll-seconds", type=float, default=1.0)
    p_session_interrupt = session_sub.add_parser("interrupt")
    p_session_interrupt.add_argument("run_id")
    p_session_interrupt.add_argument("--project", default=None)
    p_session_stop = session_sub.add_parser("stop")
    p_session_stop.add_argument("run_id")
    p_session_stop.add_argument("--project", default=None)

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--apply", action="store_true", help="实际删除(默认 dry-run)")

    args = parser.parse_args(argv)
    rt = AgentRuntime(conf_dir=args.conf_dir, runs_dir=args.runs_dir)

    try:
        result = _dispatch(rt, args)
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "interrupted", "error_type": "KeyboardInterrupt"}, args.json)
        return 130
    except Exception as exc:  # noqa: BLE001 CLI 边界统一成错误输出
        _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, args.json)
        return 1
    if result is _NO_EMIT:
        return 0
    _emit(result, args.json)
    return 0


def _dispatch(rt: AgentRuntime, args: argparse.Namespace) -> Any:
    if args.cmd == "doctor":
        return rt.doctor()
    if args.cmd == "profiles":
        return {"profiles": rt.profiles()}
    if args.cmd == "config":
        if args.config_cmd == "choices":
            return rt.config_choices(project_id=args.project, only_valid=not args.all)
        if args.config_cmd == "validate":
            return rt.validate_config(
                provider_type=args.provider,
                name=args.name,
                profile_id=args.profile,
                project_id=args.project,
            )
    if args.cmd == "task":
        if args.task_cmd == "run":
            return rt.run_task(
                prompt_file=args.prompt_file,
                provider_profile=args.profile,
                project_id=args.project,
                result_schema=args.result_schema,
                run_id=args.run_id,
                cwd=args.cwd,
                deadline_seconds=args.deadline_seconds,
                allowed_actions=args.allowed_action,
                forbidden_actions=args.forbidden_action,
                force=args.force,
            )
        if args.task_cmd == "status":
            return rt.task_status(args.run_id, project_id=args.project)
        if args.task_cmd == "logs":
            return rt.logs(args.run_id, project_id=args.project, run_type=TASK, tail=args.tail)
        if args.task_cmd == "watch":
            _watch_run(rt, args, run_type=TASK, include_events=True)
            return _NO_EMIT
        if args.task_cmd == "cancel":
            return rt.cancel(args.run_id, project_id=args.project, run_type=TASK)
    if args.cmd == "session":
        if args.session_cmd == "start":
            return rt.start_session(
                provider_profile=args.profile,
                project_id=args.project,
                run_id=args.run_id,
                cwd=args.cwd,
                allowed_actions=args.allowed_action,
                forbidden_actions=args.forbidden_action,
                force=args.force,
            )
        if args.session_cmd == "status":
            return rt.status(args.run_id, project_id=args.project, run_type=SESSION)
        if args.session_cmd == "send":
            return rt.send(
                args.run_id,
                args.text,
                project_id=args.project,
                run_type=SESSION,
                submit=not args.no_submit,
            )
        if args.session_cmd == "logs":
            return rt.logs(args.run_id, project_id=args.project, run_type=SESSION, tail=args.tail)
        if args.session_cmd == "watch":
            _watch_run(rt, args, run_type=SESSION, include_events=False)
            return _NO_EMIT
        if args.session_cmd == "interrupt":
            return rt.interrupt(args.run_id, project_id=args.project, run_type=SESSION)
        if args.session_cmd == "stop":
            return rt.stop(args.run_id, project_id=args.project, run_type=SESSION)
    if args.cmd == "prune":
        return rt.prune(dry_run=not args.apply)
    raise ValueError(f"未知命令: {args.cmd}")


def _watch_run(rt: AgentRuntime, args: argparse.Namespace, *, run_type: str, include_events: bool) -> None:
    started = time.monotonic()
    previous = ""
    last_event_seq = 0
    poll = max(float(args.poll_seconds or 1.0), 0.1)
    terminal = {"orphaned", "done", "failed", "blocked", "cancelled"}
    wait_for_run_seconds = 5.0

    if not args.json:
        print("")
        print(f"开始通过 AgentRun 监控 {run_type} 日志。按 Ctrl-C 停止监控。")
        print("", flush=True)

    while True:
        elapsed = time.monotonic() - started
        try:
            status = rt.status(args.run_id, project_id=args.project, run_type=run_type)
        except ValueError:
            if elapsed < wait_for_run_seconds:
                time.sleep(poll)
                continue
            raise
        classification = str(status.get("classification") or "")
        logs = rt.logs(args.run_id, project_id=args.project, run_type=run_type, tail=args.tail)
        current = str(logs.get("content") or "")
        delta = _log_delta(previous, current)
        previous = current
        events = logs.get("events") if include_events else []
        event_delta: list[dict[str, Any]] = []
        if isinstance(events, list):
            for item in events:
                if not isinstance(item, dict):
                    continue
                seq = int(item.get("seq") or 0)
                if seq <= last_event_seq:
                    continue
                event_delta.append(item)
                last_event_seq = seq

        if args.json:
            print(
                json.dumps(
                    {
                        "type": "watch.tick",
                        "run_id": args.run_id,
                        "run_type": run_type,
                        "classification": classification,
                        "events_delta": event_delta,
                        "content_delta": delta,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elif delta:
            if event_delta:
                print(_format_events(event_delta), end="", flush=True)
            print(delta, end="", flush=True)
        elif event_delta:
            print(_format_events(event_delta), end="", flush=True)

        if args.seconds and elapsed >= args.seconds:
            _watch_done(args, classification, elapsed, "seconds_elapsed")
            return
        if classification in terminal:
            _watch_done(args, classification, elapsed, "terminal_state")
            return
        time.sleep(poll)


def _watch_done(args: argparse.Namespace, classification: str, elapsed: float, reason: str) -> None:
    payload = {
        "type": "watch.done",
        "run_id": args.run_id,
        "classification": classification,
        "reason": reason,
        "elapsed_seconds": round(elapsed, 3),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return
    print("")
    print(f"AgentRun 监控结束：{classification or 'unknown'} ({reason})", flush=True)


def _format_events(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in events:
        seq = item.get("seq", "?")
        event_type = item.get("type", "event")
        data = item.get("data") or {}
        detail = json.dumps(data, ensure_ascii=False) if data else "{}"
        lines.append(f"[agentrun event #{seq}] {event_type} {detail}")
    return "\n".join(lines) + ("\n" if lines else "")


def _log_delta(previous: str, current: str) -> str:
    if current.startswith(previous):
        return current[len(previous):]
    if current != previous:
        suffix = "" if current.endswith("\n") else "\n"
        return "\n--- log snapshot ---\n" + current + suffix
    return ""


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(_human(data))


def _human(data: Any) -> str:
    if isinstance(data, dict):
        return "\n".join(f"{k}: {_short(v)}" for k, v in data.items())
    return str(data)


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_global_options(argv: list[str]) -> list[str]:
    """支持 `agentrun --json doctor` 与 `agentrun doctor --json` 两种写法。"""
    moved: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--json":
            moved.append(token)
            i += 1
            continue
        if token in ("--conf-dir", "--runs-dir"):
            moved.append(token)
            if i + 1 < len(argv):
                moved.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue
        if token.startswith("--conf-dir=") or token.startswith("--runs-dir="):
            moved.append(token)
            i += 1
            continue
        rest.append(token)
        i += 1
    return moved + rest


if __name__ == "__main__":
    sys.exit(main())
