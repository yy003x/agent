"""run 目录定位(唯一来源,见 design/02 §8 / design/09)。

runs/{sessions,turns,tasks}/<project_id>/<run_id>/{request,status,result}.json
                                                  + events.jsonl + output.log
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentrun.core.run import SESSION, TASK, TURN

_BUCKET = {SESSION: "sessions", TURN: "turns", TASK: "tasks"}


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    request_file: Path
    status_file: Path
    events_file: Path
    output_log: Path
    result_file: Path

    def ensure(self) -> "RunPaths":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return self


def run_paths(runs_dir: Path, project_id: str, run_type: str, run_id: str) -> RunPaths:
    bucket = _BUCKET.get(run_type)
    if bucket is None:
        raise ValueError(f"未知 run_type: {run_type}")
    run_dir = runs_dir / bucket / project_id / run_id
    return RunPaths(
        run_dir=run_dir,
        request_file=run_dir / "request.json",
        status_file=run_dir / "status.json",
        events_file=run_dir / "events.jsonl",
        output_log=run_dir / "output.log",
        result_file=run_dir / "result.json",
    )
