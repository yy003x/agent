#!/usr/bin/env python3
"""定时任务主程序（P4），基于 APScheduler。

启动：python apps/scheduler/scheduler.py
配置：apps/scheduler/jobs.json（cron 格式：分 时 日 月 周）

设计依据：01-framework.md §6。
- BackgroundScheduler 注册 job，command 用 subprocess 执行
- 失败记录到 runs/scheduler/YYYY-MM-DD.log
- 启动时打印所有 job 及下次执行时间
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS_FILE = Path(__file__).resolve().parent / "jobs.json"
LOG_DIR = ROOT / "runs" / "scheduler"


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat()} {msg}\n"
    (LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log").open("a", encoding="utf-8").write(line)
    print(line, end="")


def run_command(job_id: str, command: str) -> None:
    _log(f"[{job_id}] start: {command}")
    try:
        res = subprocess.run(shlex.split(command), cwd=ROOT, capture_output=True, text=True, timeout=3600)
        if res.returncode == 0:
            _log(f"[{job_id}] ok")
        else:
            _log(f"[{job_id}] FAIL rc={res.returncode}: {res.stderr.strip()[:500]}")
    except Exception as e:  # noqa: BLE001
        _log(f"[{job_id}] ERROR: {e}")


def main() -> int:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8")).get("jobs", [])
    sched = BackgroundScheduler(timezone="Asia/Shanghai")

    for job in jobs:
        minute, hour, day, month, dow = job["cron"].split()
        trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
        sched.add_job(run_command, trigger, args=[job["id"], job["command"]], id=job["id"])

    sched.start()
    print("=== Scheduler 已启动 ===")
    for j in sched.get_jobs():
        print(f"  · {j.id}: 下次执行 {j.next_run_time}")
    print("Ctrl+C 退出。")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
        print("\n=== Scheduler 已停止 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
