#!/usr/bin/env python3
"""每轮任务收尾记录脚本（P0）。

把「本轮发生了什么」沉淀为 session 记录，供自我进化（agent_learning_review.py）使用。

用法：
  python scripts/finalize.py record [--skill <name>] [--status success|partial|failed]
                                    [--summary "<一句话摘要>"] [--handoff]
  python scripts/finalize.py hook     # Stop hook 兜底：stdout 输出 hook JSON
  python scripts/finalize.py mark     # 运行时写操作标记，供 hook 兜底消费
  python scripts/finalize.py snapshot   # 读 git status/diff，判定状态，输出 JSON

输出：
  workspace/daily/YYYY-MM-DD/session-<8位>.md
  （--handoff 时额外写 workspace/resume/YYYY-MM-DD-<8位>.md 未完成任务恢复点）

设计依据：01-framework.md §5 / content-agent-architecture.md L3。
本脚本不依赖第三方库，可被 Python runtime、Codex/tmux runtime 或兼容 Stop hook 直接调用。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 项目根 = 本文件的上两级（scripts/ 的父目录）
ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = ROOT / "workspace"
DAILY_DIR = ROOT / "workspace" / "daily"
RESUME_DIR = ROOT / "workspace" / "resume"
ACTIVITY_FILE = WORKSPACE_DIR / ".finalize-activity.json"
LAST_RECORD_FILE = WORKSPACE_DIR / ".finalize-last-record.json"


def _log(message: str, *, file=sys.stdout) -> None:
    print(message, file=file)


def _emit_stop_hook_output() -> None:
    """Stop hook stdout must be valid JSON; human logs belong on stderr."""
    print(json.dumps({}, ensure_ascii=False))


def _run_git(args: list[str]) -> str | None:
    """运行 git 子命令，失败返回 None。"""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_snapshot() -> dict:
    """读取 git 状态，返回结构化快照。"""
    porcelain = _run_git(["status", "--porcelain"])
    diffstat = _run_git(["diff", "--stat"])
    staged_diffstat = _run_git(["diff", "--cached", "--stat"])

    if porcelain is None:
        return {"git": "not available", "files_changed": [], "status_guess": "unknown"}

    files = [line[3:] for line in porcelain.splitlines() if line.strip()]
    # 启发式状态判定：存在冲突标记 → failed；有变更 → success；无变更 → success（纯只读）
    has_conflict = any(line[:2] in ("UU", "AA", "DD") for line in porcelain.splitlines())
    status_guess = "failed" if has_conflict else "success"

    stat = "\n".join(filter(None, [staged_diffstat, diffstat])) or "(无文件变更)"
    return {
        "files_changed": files,
        "diffstat": stat,
        "status_guess": status_guess,
    }


def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_activity(skill: str, status: str, summary: str, source: str) -> None:
    _write_json_file(ACTIVITY_FILE, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill": skill,
        "status": status,
        "summary": summary.strip(),
        "source": source,
    })


def clear_activity() -> None:
    try:
        ACTIVITY_FILE.unlink()
    except FileNotFoundError:
        pass


def mark_recorded(path: Path) -> None:
    _write_json_file(LAST_RECORD_FILE, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session": str(path.relative_to(ROOT)),
    })


def recently_recorded(seconds: int = 300) -> bool:
    data = _read_json_file(LAST_RECORD_FILE)
    ts = data.get("timestamp")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - when
    return 0 <= age.total_seconds() <= seconds


def write_session(skill: str, status: str, summary: str, handoff: bool) -> Path:
    now = datetime.now(timezone.utc)
    date_dir = DAILY_DIR / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    session_id = uuid.uuid4()
    short = str(session_id)[:8]
    snap = git_snapshot()

    if status == "auto":
        status = snap.get("status_guess", "success")

    files_block = snap.get("diffstat", "(git not available)")
    summary_text = summary.strip() if summary else "（未提供摘要）"

    content = f"""---
session_id: {session_id}
timestamp: {now.isoformat()}
skill_triggered: {skill}
status: {status}
---

## 摘要
{summary_text}

## 文件变更
{files_block}

## KB 命中
（本轮如有 kb search，命中 id 见 workspace/kb/search-log.jsonl）
"""
    out_path = date_dir / f"session-{short}.md"
    out_path.write_text(content, encoding="utf-8")

    if handoff:
        RESUME_DIR.mkdir(parents=True, exist_ok=True)
        resume_path = RESUME_DIR / f"{now.strftime('%Y-%m-%d')}-{short}.md"
        resume_path.write_text(
            f"# 未完成任务恢复点 {now.isoformat()}\n\n"
            f"- session: session-{short}\n"
            f"- skill: {skill}\n"
            f"- 摘要: {summary_text}\n"
            f"- 待续: （在此补充下一步）\n",
            encoding="utf-8",
        )

    return out_path


def cmd_record(args: argparse.Namespace) -> int:
    path = write_session(
        skill=args.skill,
        status=args.status,
        summary=args.summary or "",
        handoff=args.handoff,
    )
    clear_activity()
    mark_recorded(path)
    rel = path.relative_to(ROOT)
    _log(f"[finalize] session 已写入：{rel}")
    return 0


def _cmd_hook(args: argparse.Namespace) -> int:
    """Stop hook 兜底入口：只在有明确实质性信号时写入，避免纯问答产生空 session。"""
    activity = _read_json_file(ACTIVITY_FILE)
    if recently_recorded() and not activity:
        _log("[finalize] hook skip：最近已显式写入 session", file=sys.stderr)
        _emit_stop_hook_output()
        return 0

    snap = git_snapshot()
    changed = snap.get("files_changed") or []
    if not changed and not activity:
        _log("[finalize] hook skip：未检测到实质性任务信号", file=sys.stderr)
        _emit_stop_hook_output()
        return 0

    summary = activity.get("summary") or f"Stop hook 自动记录：检测到 {len(changed)} 个 Git 工作区变更。"
    skill = activity.get("skill") or "none"
    status = activity.get("status") or "auto"
    path = write_session(
        skill=skill,
        status=status,
        summary=summary,
        handoff=False,
    )
    clear_activity()
    mark_recorded(path)
    rel = path.relative_to(ROOT)
    _log(f"[finalize] hook session 已写入：{rel}", file=sys.stderr)
    _emit_stop_hook_output()
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    try:
        return _cmd_hook(args)
    except Exception as exc:  # noqa: BLE001 - Stop hook must not break Codex turns.
        _log(f"[finalize] hook error：{exc}", file=sys.stderr)
        _emit_stop_hook_output()
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    mark_activity(
        skill=args.skill,
        status=args.status,
        summary=args.summary,
        source=args.source,
    )
    _log(f"[finalize] activity 已标记：{ACTIVITY_FILE.relative_to(ROOT)}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    print(json.dumps(git_snapshot(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="每轮任务收尾记录")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="写一条 session 记录")
    rec.add_argument("--skill", default="none", help="本轮触发的 skill 名（默认 none）")
    rec.add_argument(
        "--status",
        default="auto",
        choices=["auto", "success", "partial", "failed"],
        help="任务状态（auto = 据 git 启发式判定）",
    )
    rec.add_argument("--summary", default="", help="1-3 句摘要（不写原始对话文本）")
    rec.add_argument("--handoff", action="store_true", help="额外写恢复点到 workspace/resume/")
    rec.set_defaults(func=cmd_record)

    hook = sub.add_parser("hook", help="Stop hook 兜底记录；无实质性信号时跳过")
    hook.set_defaults(func=cmd_hook)

    mark = sub.add_parser("mark", help="标记运行时写操作，供 Stop hook 兜底消费")
    mark.add_argument("--skill", default="none")
    mark.add_argument(
        "--status",
        default="success",
        choices=["success", "partial", "failed"],
    )
    mark.add_argument("--summary", required=True)
    mark.add_argument("--source", default="manual")
    mark.set_defaults(func=cmd_mark)

    snap = sub.add_parser("snapshot", help="输出 git 状态 JSON")
    snap.set_defaults(func=cmd_snapshot)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
