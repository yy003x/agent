#!/usr/bin/env python3
"""Local service manager for the Agent workbench.

This is intentionally small and repo-local. It gives the workbench a
brew-services-like surface without hiding runtime state inside tmux sessions.
Managed service state lives under runs/workbench/services/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web"
RUN_DIR = ROOT / "runs" / "workbench"
SERVICES_DIR = RUN_DIR / "services"
API_SERVICE_PREFIX = "agent_workbench_api"
WEB_SERVICE_PREFIX = "agent_workbench_web"
DEFAULT_HOST = os.environ.get("AGENT_WORKBENCH_API_HOST", "127.0.0.1")
DEFAULT_API_PORT = int(os.environ.get("AGENT_WORKBENCH_API_PORT", "8765"))
DEFAULT_WEB_PORT = int(os.environ.get("AGENT_WORKBENCH_WEB_PORT", "5173"))
STATE_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)


def service_name(port: int) -> str:
    return f"{API_SERVICE_PREFIX}_{port}"


def web_service_name(port: int) -> str:
    return f"{WEB_SERVICE_PREFIX}_{port}"


def infer_port(name: str | None, port: int | None) -> int:
    if port is not None:
        return port
    if name:
        match = re.fullmatch(rf"{re.escape(API_SERVICE_PREFIX)}_(\d+)", name)
        if match:
            return int(match.group(1))
    return DEFAULT_API_PORT


def infer_web_port(name: str | None, port: int | None) -> int:
    if port is not None:
        return port
    if name:
        match = re.fullmatch(rf"{re.escape(WEB_SERVICE_PREFIX)}_(\d+)", name)
        if match:
            return int(match.group(1))
    return DEFAULT_WEB_PORT


def normalize_name(name: str | None, port: int) -> str:
    return name or service_name(port)


def state_path(name: str) -> Path:
    return SERVICES_DIR / f"{name}.json"


def default_log_path(port: int) -> Path:
    return RUN_DIR / f"server-{port}.log"


def default_web_log_path(port: int) -> Path:
    return RUN_DIR / f"web-{port}.log"


def load_state(name: str) -> dict[str, Any] | None:
    path = state_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"name": name, "status": "invalid-state", "state_path": str(path)}


def save_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    state["version"] = STATE_VERSION
    state_path(str(state["name"])).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def managed_states() -> list[dict[str, Any]]:
    if not SERVICES_DIR.exists():
        return []
    states: list[dict[str, Any]] = []
    for path in sorted(SERVICES_DIR.glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {"name": path.stem, "status": "invalid-state"}
        states.append(state)
    return states


def run_quiet(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command(pid: int | None) -> str:
    if not pid:
        return ""
    result = run_quiet(["ps", "-p", str(pid), "-o", "command="])
    return result.stdout.strip() if result.returncode == 0 else ""


def listener_records(port: int) -> list[dict[str, str]]:
    result = run_quiet(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn"])
    if result.returncode != 0:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            if current:
                records.append(current)
            current = {"pid": value}
        elif tag == "c":
            current["command"] = value
        elif tag == "n":
            current["name"] = value
    if current:
        records.append(current)
    for record in records:
        record["process_command"] = process_command(int(record["pid"]))
    return records


def wait_for_http(host: str, port: int, path: str, timeout_seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://{host}:{port}{path}"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:  # noqa: S310 - local URL only.
                if 200 <= response.status < 500:
                    return True
        except (OSError, URLError):
            time.sleep(0.25)
    return False


def wait_for_health(host: str, port: int, timeout_seconds: float = 8.0) -> bool:
    return wait_for_http(host, port, "/api/health", timeout_seconds)


def wait_for_web(host: str, port: int, timeout_seconds: float = 8.0) -> bool:
    return wait_for_http(host, port, "/", timeout_seconds)


def python_bin() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = (
        str(ROOT)
        if not env.get("PYTHONPATH")
        else f"{ROOT}{os.pathsep}{env['PYTHONPATH']}"
    )
    venv_bin = ROOT / ".venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def start_service(args: argparse.Namespace) -> int:
    port = infer_port(args.name, args.port)
    name = normalize_name(args.name, port)
    host = args.host
    log_path = Path(args.log) if args.log else default_log_path(port)
    ensure_dirs()

    state = load_state(name)
    existing_pid = int(state.get("pid", 0)) if state and str(state.get("pid", "")).isdigit() else None
    if process_alive(existing_pid):
        print(f"{name} already running pid={existing_pid}")
        return 0

    if tmux_session_exists(name):
        if args.replace_legacy_tmux:
            stop_tmux_session(name)
        else:
            print(
                f"{name} is a legacy tmux session. "
                "Use --replace-legacy-tmux to replace it with a managed service.",
                file=sys.stderr,
            )
            return 2

    listeners = listener_records(port)
    if listeners:
        detail = ", ".join(f"pid={item['pid']} command={item.get('process_command', item.get('command', ''))}" for item in listeners)
        print(f"port {port} is already listening: {detail}", file=sys.stderr)
        return 2

    command = [
        python_bin(),
        "-m",
        "uvicorn",
        "apps.api.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log_file:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=build_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    new_state = {
        "name": name,
        "manager": "workbench_service",
        "status": "running",
        "host": host,
        "port": port,
        "pid": process.pid,
        "command": command,
        "cwd": str(ROOT),
        "log_path": str(log_path),
        "started_at": utc_now(),
    }
    save_state(new_state)

    time.sleep(0.4)
    if process.poll() is not None:
        new_state["status"] = "failed"
        new_state["stopped_at"] = utc_now()
        save_state(new_state)
        print(f"{name} failed to start. See {log_path}", file=sys.stderr)
        return 1

    health_ok = wait_for_health(host, port)
    print(f"started {name} pid={process.pid} url=http://{host}:{port} log={log_path}")
    if not health_ok:
        print(f"warning: /api/health did not respond before timeout", file=sys.stderr)
    return 0 if health_ok else 1


def start_web_service(args: argparse.Namespace) -> int:
    port = infer_web_port(args.name, args.port)
    name = args.name or web_service_name(port)
    host = args.host
    log_path = Path(args.log) if args.log else default_web_log_path(port)
    ensure_dirs()

    state = load_state(name)
    existing_pid = int(state.get("pid", 0)) if state and str(state.get("pid", "")).isdigit() else None
    if process_alive(existing_pid):
        print(f"{name} already running pid={existing_pid}")
        return 0

    listeners = listener_records(port)
    if listeners:
        detail = ", ".join(
            f"pid={item['pid']} command={item.get('process_command', item.get('command', ''))}"
            for item in listeners
        )
        print(f"port {port} is already listening: {detail}", file=sys.stderr)
        return 2

    command = [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        host,
        "--port",
        str(port),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log_file:
        process = subprocess.Popen(
            command,
            cwd=WEB_DIR,
            env=build_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    new_state = {
        "name": name,
        "manager": "workbench_service",
        "status": "running",
        "host": host,
        "port": port,
        "pid": process.pid,
        "command": command,
        "cwd": str(WEB_DIR),
        "log_path": str(log_path),
        "started_at": utc_now(),
    }
    save_state(new_state)

    time.sleep(0.4)
    if process.poll() is not None:
        new_state["status"] = "failed"
        new_state["stopped_at"] = utc_now()
        save_state(new_state)
        print(f"{name} failed to start. See {log_path}", file=sys.stderr)
        return 1

    web_ok = wait_for_web(host, port)
    print(f"started {name} pid={process.pid} url=http://{host}:{port} log={log_path}")
    if not web_ok:
        print(f"warning: web service did not respond before timeout", file=sys.stderr)
    return 0 if web_ok else 1


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        os.kill(pid, sig)


def stop_pid(pid: int, timeout_seconds: float) -> bool:
    if not process_alive(pid):
        return True
    _signal_process_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.2)
    if process_alive(pid):
        _signal_process_group(pid, signal.SIGKILL)
    return not process_alive(pid)


def stop_service(args: argparse.Namespace) -> int:
    port = infer_port(args.name, args.port)
    name = normalize_name(args.name, port)
    stopped = False
    state = load_state(name)

    if state:
        pid = int(state.get("pid", 0)) if str(state.get("pid", "")).isdigit() else 0
        if pid and process_alive(pid):
            if not stop_pid(pid, args.timeout):
                print(f"failed to stop {name} pid={pid}", file=sys.stderr)
                return 1
            stopped = True
            print(f"stopped {name} pid={pid}")
        state["status"] = "stopped"
        state["stopped_at"] = utc_now()
        save_state(state)

    if tmux_session_exists(name):
        if args.legacy_tmux:
            stop_tmux_session(name)
            stopped = True
            print(f"stopped legacy tmux session {name}")
        else:
            print(
                f"{name} is still running as legacy tmux. "
                "Pass --legacy-tmux to stop that session.",
                file=sys.stderr,
            )
            return 2

    if not stopped:
        print(f"{name} is not running")
    return 0


def stop_named_service(name: str, timeout: float) -> bool:
    state = load_state(name)
    if not state:
        print(f"{name} is not running")
        return True
    pid = int(state.get("pid", 0)) if str(state.get("pid", "")).isdigit() else 0
    stopped = False
    if pid and process_alive(pid):
        if not stop_pid(pid, timeout):
            print(f"failed to stop {name} pid={pid}", file=sys.stderr)
            return False
        stopped = True
        print(f"stopped {name} pid={pid}")
    state["status"] = "stopped"
    state["stopped_at"] = utc_now()
    save_state(state)
    if not stopped:
        print(f"{name} is not running")
    return True


def stop_web_service(args: argparse.Namespace) -> int:
    port = infer_web_port(args.name, args.port)
    name = args.name or web_service_name(port)
    return 0 if stop_named_service(name, args.timeout) else 1


def restart_service(args: argparse.Namespace) -> int:
    stop_args = argparse.Namespace(
        name=args.name,
        port=args.port,
        timeout=args.timeout,
        legacy_tmux=args.replace_legacy_tmux,
    )
    stop_code = stop_service(stop_args)
    if stop_code not in {0, 2}:
        return stop_code
    start_args = argparse.Namespace(
        name=args.name,
        port=args.port,
        host=args.host,
        log=args.log,
        replace_legacy_tmux=args.replace_legacy_tmux,
    )
    return start_service(start_args)


def restart_web_service(args: argparse.Namespace) -> int:
    stop_args = argparse.Namespace(
        name=args.name,
        port=args.port,
        timeout=args.timeout,
    )
    stop_code = stop_web_service(stop_args)
    if stop_code != 0:
        return stop_code
    start_args = argparse.Namespace(
        name=args.name,
        port=args.port,
        host=args.host,
        log=args.log,
    )
    return start_web_service(start_args)


def stop_all_services(args: argparse.Namespace) -> int:
    ok = True
    for state in managed_states():
        name = str(state.get("name", ""))
        if name.startswith((API_SERVICE_PREFIX, WEB_SERVICE_PREFIX)):
            ok = stop_named_service(name, args.timeout) and ok
    if args.legacy_tmux:
        for session in tmux_sessions():
            name = str(session.get("name", ""))
            stop_tmux_session(name)
            print(f"stopped legacy tmux session {name}")
    return 0 if ok else 1


def tmux_session_exists(name: str) -> bool:
    result = run_quiet(["tmux", "has-session", "-t", name])
    return result.returncode == 0


def stop_tmux_session(name: str) -> None:
    run_quiet(["tmux", "kill-session", "-t", name])


def tmux_sessions() -> list[dict[str, Any]]:
    result = run_quiet(["tmux", "list-sessions", "-F", "#{session_name}|#{session_windows}|#{session_attached}"])
    if result.returncode != 0:
        return []
    sessions: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, windows, attached = parts
        if not name.startswith("agent_workbench_"):
            continue
        pane = run_quiet(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}|#{pane_current_command}|#{pane_current_path}"])
        pid = ""
        command = ""
        cwd = ""
        if pane.returncode == 0 and pane.stdout.splitlines():
            pane_parts = pane.stdout.splitlines()[0].split("|")
            if len(pane_parts) >= 3:
                pid, command, cwd = pane_parts[:3]
        port = infer_port(name, None) if re.fullmatch(rf"{re.escape(API_SERVICE_PREFIX)}_\d+", name) else None
        sessions.append(
            {
                "name": name,
                "status": "running",
                "pid": pid,
                "port": port or "",
                "host": "127.0.0.1" if port else "",
                "url": f"http://127.0.0.1:{port}" if port else "",
                "source": "legacy-tmux",
                "log_path": str(default_log_path(port)) if port else "",
                "command": process_command(int(pid)) if str(pid).isdigit() else command,
                "cwd": cwd,
                "windows": windows,
                "attached": attached,
            }
        )
    return sessions


def managed_record(state: dict[str, Any]) -> dict[str, Any]:
    name = str(state.get("name", ""))
    pid = int(state.get("pid", 0)) if str(state.get("pid", "")).isdigit() else 0
    running = process_alive(pid)
    status = state.get("status", "unknown")
    if running:
        status = "running"
    elif status == "running":
        status = "stale"
    port = state.get("port", "")
    host = state.get("host", "")
    return {
        "name": name,
        "status": status,
        "pid": str(pid) if pid else "",
        "port": str(port) if port else "",
        "host": host,
        "url": f"http://{host}:{port}" if host and port else "",
        "source": "managed",
        "log_path": state.get("log_path", ""),
        "command": " ".join(state.get("command", [])) if isinstance(state.get("command"), list) else state.get("command", ""),
        "started_at": state.get("started_at", ""),
        "stopped_at": state.get("stopped_at", ""),
    }


def collect_services() -> list[dict[str, Any]]:
    records = [managed_record(state) for state in managed_states()]
    managed_names = {record["name"] for record in records}
    for session in tmux_sessions():
        if session["name"] not in managed_names:
            records.append(session)
    return sorted(records, key=lambda item: (str(item.get("name", "")), str(item.get("source", ""))))


def print_table(records: list[dict[str, Any]]) -> None:
    columns = [
        ("Name", "name"),
        ("Status", "status"),
        ("PID", "pid"),
        ("Port", "port"),
        ("Source", "source"),
        ("URL", "url"),
        ("Log", "log_path"),
    ]
    widths = [
        max(len(title), *(len(str(record.get(key, ""))) for record in records))
        for title, key in columns
    ]
    header = "  ".join(title.ljust(width) for (title, _), width in zip(columns, widths))
    print(header)
    print("  ".join("-" * width for width in widths))
    for record in records:
        print("  ".join(str(record.get(key, "")).ljust(width) for (_, key), width in zip(columns, widths)))


def list_services(args: argparse.Namespace) -> int:
    records = collect_services()
    if args.json:
        print(json.dumps({"services": records}, ensure_ascii=False, indent=2))
    else:
        print_table(records)
    return 0


def status_service(args: argparse.Namespace) -> int:
    port = infer_port(args.name, args.port)
    name = normalize_name(args.name, port)
    matches = [record for record in collect_services() if record["name"] == name]
    if not matches:
        record = {
            "name": name,
            "status": "stopped",
            "pid": "",
            "port": str(port),
            "source": "managed",
            "url": f"http://{args.host}:{port}",
            "log_path": str(default_log_path(port)),
        }
        matches = [record]
    if args.json:
        print(json.dumps(matches[0], ensure_ascii=False, indent=2))
    else:
        print_table(matches)
    return 0


def status_web_service(args: argparse.Namespace) -> int:
    port = infer_web_port(args.name, args.port)
    name = args.name or web_service_name(port)
    matches = [record for record in collect_services() if record["name"] == name]
    if not matches:
        record = {
            "name": name,
            "status": "stopped",
            "pid": "",
            "port": str(port),
            "source": "managed",
            "url": f"http://{args.host}:{port}",
            "log_path": str(default_web_log_path(port)),
        }
        matches = [record]
    if args.json:
        print(json.dumps(matches[0], ensure_ascii=False, indent=2))
    else:
        print_table(matches)
    return 0


def tail_lines(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        end = file.tell()
        block_size = 4096
        data = b""
        while end > 0 and data.count(b"\n") <= lines:
            step = min(block_size, end)
            end -= step
            file.seek(end)
            data = file.read(step) + data
    return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")


def logs_service(args: argparse.Namespace) -> int:
    port = infer_port(args.name, args.port)
    name = normalize_name(args.name, port)
    state = load_state(name)
    log_path = Path(state.get("log_path")) if state and state.get("log_path") else default_log_path(port)
    output = tail_lines(log_path, args.lines)
    if not output:
        print(f"no log found at {log_path}", file=sys.stderr)
        return 1
    print(output)
    return 0


def logs_web_service(args: argparse.Namespace) -> int:
    port = infer_web_port(args.name, args.port)
    name = args.name or web_service_name(port)
    state = load_state(name)
    log_path = Path(state.get("log_path")) if state and state.get("log_path") else default_web_log_path(port)
    output = tail_lines(log_path, args.lines)
    if not output:
        print(f"no log found at {log_path}", file=sys.stderr)
        return 1
    print(output)
    return 0


def add_common_service_args(parser: argparse.ArgumentParser, *, include_host: bool = False, include_log: bool = False) -> None:
    parser.add_argument("name", nargs="?", help="service name, for example agent_workbench_api_8766")
    parser.add_argument("--port", type=int, help=f"API port, default {DEFAULT_API_PORT}")
    if include_host:
        parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host, default {DEFAULT_HOST}")
    if include_log:
        parser.add_argument("--log", help="log path, default runs/workbench/server-<port>.log")


def add_web_service_args(parser: argparse.ArgumentParser, *, include_host: bool = False, include_log: bool = False) -> None:
    parser.add_argument("name", nargs="?", help="service name, for example agent_workbench_web_5173")
    parser.add_argument("--port", type=int, help=f"Web port, default {DEFAULT_WEB_PORT}")
    if include_host:
        parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host, default {DEFAULT_HOST}")
    if include_log:
        parser.add_argument("--log", help="log path, default runs/workbench/web-<port>.log")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local Agent workbench services.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list managed and legacy workbench services")
    list_parser.add_argument("--json", action="store_true", help="print JSON")
    list_parser.set_defaults(func=list_services)

    start_parser = subparsers.add_parser("start", help="start the API as a background service")
    add_common_service_args(start_parser, include_host=True, include_log=True)
    start_parser.add_argument("--replace-legacy-tmux", action="store_true", help="kill same-named legacy tmux session before starting")
    start_parser.set_defaults(func=start_service)

    stop_parser = subparsers.add_parser("stop", help="stop a managed service")
    add_common_service_args(stop_parser)
    stop_parser.add_argument("--timeout", type=float, default=8.0, help="seconds before SIGKILL")
    stop_parser.add_argument("--legacy-tmux", action="store_true", help="also stop same-named legacy tmux session")
    stop_parser.set_defaults(func=stop_service)

    stop_all_parser = subparsers.add_parser("stop-all", help="stop all managed workbench services")
    stop_all_parser.add_argument("--timeout", type=float, default=8.0, help="seconds before SIGKILL")
    stop_all_parser.add_argument("--legacy-tmux", action="store_true", help="also stop legacy tmux workbench sessions")
    stop_all_parser.set_defaults(func=stop_all_services)

    restart_parser = subparsers.add_parser("restart", help="restart a managed service")
    add_common_service_args(restart_parser, include_host=True, include_log=True)
    restart_parser.add_argument("--timeout", type=float, default=8.0, help="seconds before SIGKILL")
    restart_parser.add_argument("--replace-legacy-tmux", action="store_true", help="replace same-named legacy tmux session")
    restart_parser.set_defaults(func=restart_service)

    status_parser = subparsers.add_parser("status", help="show one service")
    add_common_service_args(status_parser, include_host=True)
    status_parser.add_argument("--json", action="store_true", help="print JSON")
    status_parser.set_defaults(func=status_service)

    logs_parser = subparsers.add_parser("logs", help="tail service logs")
    add_common_service_args(logs_parser)
    logs_parser.add_argument("--lines", type=int, default=80, help="lines to print")
    logs_parser.set_defaults(func=logs_service)

    web_start_parser = subparsers.add_parser("web-start", help="start the Web dev server as a background service")
    add_web_service_args(web_start_parser, include_host=True, include_log=True)
    web_start_parser.set_defaults(func=start_web_service)

    web_stop_parser = subparsers.add_parser("web-stop", help="stop a managed Web service")
    add_web_service_args(web_stop_parser)
    web_stop_parser.add_argument("--timeout", type=float, default=8.0, help="seconds before SIGKILL")
    web_stop_parser.set_defaults(func=stop_web_service)

    web_restart_parser = subparsers.add_parser("web-restart", help="restart a managed Web service")
    add_web_service_args(web_restart_parser, include_host=True, include_log=True)
    web_restart_parser.add_argument("--timeout", type=float, default=8.0, help="seconds before SIGKILL")
    web_restart_parser.set_defaults(func=restart_web_service)

    web_status_parser = subparsers.add_parser("web-status", help="show one Web service")
    add_web_service_args(web_status_parser, include_host=True)
    web_status_parser.add_argument("--json", action="store_true", help="print JSON")
    web_status_parser.set_defaults(func=status_web_service)

    web_logs_parser = subparsers.add_parser("web-logs", help="tail Web service logs")
    add_web_service_args(web_logs_parser)
    web_logs_parser.add_argument("--lines", type=int, default=80, help="lines to print")
    web_logs_parser.set_defaults(func=logs_web_service)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
