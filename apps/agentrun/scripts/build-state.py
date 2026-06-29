#!/usr/bin/env python3
"""构建工作流状态机助手(确定性状态转移,见 scripts/BUILD-LOOP.md)。

状态文件 .build/state.json:
  {"milestones": [...], "current": "m1", "attempt": 0, "max_attempts": 3, "history": [...]}

命令:
  show     打印完整状态(JSON)
  current  打印当前里程碑(m1..m4)或 DONE
  attempt  打印当前 attempt 次数
  pass     当前里程碑通过:记录结果、推进到下一个、attempt 归零
  fail     当前里程碑失败:attempt += 1,打印新的 attempt;到 max 打印 ESCALATE
  init     初始化(m0 视为已完成,current=m1)
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / ".build" / "state.json"
MILESTONES = ["m0", "m1", "m2", "m3", "m4"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load() -> dict:
    if not STATE.exists():
        return init()
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init() -> dict:
    data = {
        "milestones": MILESTONES,
        "current": "m1",  # m0 已完成
        "attempt": 0,
        "max_attempts": 3,
        "history": [{"milestone": "m0", "result": "pass", "at": _now()}],
    }
    save(data)
    return data


def current(data: dict) -> str:
    return data["current"] if data["current"] else "DONE"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    data = load()

    if cmd == "init":
        data = init()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif cmd == "show":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif cmd == "current":
        print(current(data))
    elif cmd == "attempt":
        print(data["attempt"])
    elif cmd == "pass":
        cur = data["current"]
        if not cur:
            print("DONE")
            return 0
        data["history"].append({"milestone": cur, "result": "pass", "attempt": data["attempt"], "at": _now()})
        idx = data["milestones"].index(cur)
        data["current"] = data["milestones"][idx + 1] if idx + 1 < len(data["milestones"]) else ""
        data["attempt"] = 0
        save(data)
        print(current(data))
    elif cmd == "fail":
        data["attempt"] += 1
        data["history"].append({"milestone": data["current"], "result": "fail", "attempt": data["attempt"], "at": _now()})
        save(data)
        if data["attempt"] >= data["max_attempts"]:
            print(f"ESCALATE attempt={data['attempt']}")
        else:
            print(f"RETRY attempt={data['attempt']}")
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
