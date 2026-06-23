#!/usr/bin/env python3
"""Run a minimal isolated content-runtime workflow for e2e validation."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONTENT_RUNTIME = ROOT / "skills" / "content-generate" / "scripts" / "content_runtime.py"
TEST_DATA = ROOT / "test-data" / "minimal"
RUNS_DIR = ROOT / "runs" / "workbench-smoke"


def run(cmd: list[str], *, env: dict[str, str], quiet: bool) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if not quiet:
        print("$ " + " ".join(cmd))
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
    return proc


def must(proc: subprocess.CompletedProcess[str], step: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{step} failed: exit={proc.returncode}\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="工作台最小文本运营闭环 smoke")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--keep", action="store_true", help="保留本次 smoke 目录")
    args = parser.parse_args()

    if not TEST_DATA.exists():
        print(f"测试素材不存在：{TEST_DATA}", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / stamp
    kb_dir = run_dir / "kb"
    output_dir = run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "CONTENT_RUNTIME_KB_DIR": str(kb_dir),
        "CONTENT_RUNTIME_LANCE_DIR": str(kb_dir / "lance"),
        "CONTENT_RUNTIME_MEDIA_STORE": str(run_dir / "media-store"),
        "CONTENT_RUNTIME_RUNS_DIR": str(run_dir / "runtime-logs"),
        "CONTENT_RUNTIME_TEST_EMBEDDING": "hash",
        "PYTHONPATH": str(ROOT),
    }

    try:
        must(run(["python3", str(CONTENT_RUNTIME), "init"], env=env, quiet=args.quiet), "init")
        must(run([
            "python3", str(CONTENT_RUNTIME), "kb", "ingest",
            "--src", str(TEST_DATA),
            "--modality", "doc",
            "--allow-write",
        ], env=env, quiet=args.quiet), "ingest")
        search_path = output_dir / "search.json"
        search = run([
            "python3", str(CONTENT_RUNTIME), "kb", "search",
            "--query", "数学思维 几何",
            "--modality", "doc",
            "--topk", "3",
            "--json",
            "--no-log",
            "--no-touch",
        ], env=env, quiet=args.quiet)
        must(search, "search")
        search_path.write_text(search.stdout, encoding="utf-8")
        draft_path = output_dir / "draft.json"
        must(run([
            "python3", str(CONTENT_RUNTIME), "text", "draft",
            "--brief", "小学数学思维图书推荐",
            "--platform", "xiaohongshu",
            "--sources", str(search_path),
            "--out", str(draft_path),
            "--allow-write",
        ], env=env, quiet=args.quiet), "draft")
        plan_path = output_dir / "plan.json"
        must(run([
            "python3", str(CONTENT_RUNTIME), "plan", "build",
            "--draft", str(draft_path),
            "--sources", str(search_path),
            "--out", str(plan_path),
            "--allow-write",
        ], env=env, quiet=args.quiet), "plan")
        package_dir = output_dir / "package"
        package_dir.mkdir(exist_ok=True)
        (package_dir / "_meta.json").write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")
        must(run([
            "python3", str(CONTENT_RUNTIME), "publish", "package",
            "--platform", "xiaohongshu",
            "--in", str(package_dir),
            "--allow-write",
        ], env=env, quiet=args.quiet), "package")
        report = {
            "ok": True,
            "run_dir": str(run_dir),
            "search": str(search_path),
            "draft": str(draft_path),
            "plan": str(plan_path),
            "package": str(package_dir / "xiaohongshu"),
        }
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.quiet:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        if not args.quiet:
            print(f"smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep is False and os.environ.get("WORKBENCH_SMOKE_KEEP", "").lower() not in {"1", "true", "yes"}:
            # Keep only report-bearing directories during normal e2e troubleshooting.
            pass


if __name__ == "__main__":
    raise SystemExit(main())
