#!/usr/bin/env python3
"""每轮任务收尾记录脚本（P0）。

把「本轮发生了什么」沉淀为 session 记录，供自我进化（agent_learning_review.py）使用。

用法：
  python scripts/finalize.py record [--skill <name>] [--status success|partial|failed]
                                    [--summary "<一句话摘要>"] [--handoff]
  python scripts/finalize.py snapshot   # 读 git status/diff，判定状态，输出 JSON

输出：
  workspace/daily/YYYY-MM-DD/session-<8位>.md
  （--handoff 时额外写 workspace/resume/<8位>.md 未完成任务恢复点）

设计依据：01-framework.md §5 / content-agent-architecture.md L3。
本脚本不依赖第三方库，可被 Claude Code Stop hook 直接调用。
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
DAILY_DIR = ROOT / "workspace" / "daily"
RESUME_DIR = ROOT / "workspace" / "resume"


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
    rel = path.relative_to(ROOT)
    print(f"[finalize] session 已写入：{rel}")
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

    snap = sub.add_parser("snapshot", help="输出 git 状态 JSON")
    snap.set_defaults(func=cmd_snapshot)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
