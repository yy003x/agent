"""runs/state/registry.json + 文件锁 + on-demand 状态 + prune(见 design/02 §5)。"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agentrun.core.jsonio import read_json, write_json_atomic
from agentrun.core.run import TERMINAL_STATES, utc_now


class Registry:
    def __init__(self, runs_dir: Path) -> None:
        self.state_dir = runs_dir / "state"
        self.registry_file = self.state_dir / "registry.json"
        self.lock_file = self.state_dir / "registry.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_file.open("w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        return read_json(self.registry_file, {"runs": {}})

    def register(self, run_id: str, entry: dict[str, Any]) -> None:
        with self._locked():
            data = self._load()
            data["runs"][run_id] = {**entry, "updated_at": utc_now()}
            write_json_atomic(self.registry_file, data)

    def update(self, run_id: str, **changes: Any) -> None:
        with self._locked():
            data = self._load()
            if run_id in data["runs"]:
                data["runs"][run_id].update(changes)
                data["runs"][run_id]["updated_at"] = utc_now()
                write_json_atomic(self.registry_file, data)

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._load()["runs"].get(run_id)

    def list_runs(self) -> dict[str, Any]:
        return self._load()["runs"]

    def classify(self, run_id: str) -> str:
        """on-demand 区分 running / orphaned / result_pending / terminal。"""
        entry = self.get(run_id)
        if entry is None:
            return "unknown"
        state = entry.get("state")
        if state in TERMINAL_STATES:
            return state
        run_dir = Path(entry["run_dir"])
        if (run_dir / "result.json").exists():
            return "result_pending"
        pid = entry.get("pid")
        if pid and _pid_alive(int(pid)):
            return "running"
        return "orphaned"

    def prune(self, dry_run: bool = True) -> dict[str, Any]:
        """清理终态 run 的 registry 项(不碰 run 目录产物)。"""
        removed: list[str] = []
        with self._locked():
            data = self._load()
            for run_id, entry in list(data["runs"].items()):
                if entry.get("state") in TERMINAL_STATES:
                    removed.append(run_id)
                    if not dry_run:
                        del data["runs"][run_id]
            if not dry_run:
                write_json_atomic(self.registry_file, data)
        return {"dry_run": dry_run, "removed": removed}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import os

        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
