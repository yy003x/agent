"""Main runtime facade for the local workbench."""
from __future__ import annotations

from pathlib import Path

from . import external_cli
from .executor import ExternalCliExecutor
from .observer import FileResultObserver
from .planner import Planner
from .skill_registry import SkillRegistry
from .task_store import TaskStore


class MainRuntime:
    """Coordinate planner, executor, observer, state, skills and task storage."""

    def __init__(self, task_store_root: Path | None = None) -> None:
        self.planner = Planner()
        self.executor = ExternalCliExecutor()
        self.observer = FileResultObserver()
        self.skill_registry = SkillRegistry(external_cli.ROOT / "skills", external_cli.ROOT)
        self.task_store = TaskStore(task_store_root or external_cli.RUNS_DIR)

    def default_runtime(self) -> str:
        return external_cli.default_runtime()

    def effective_runtime_config(self, options: dict | None = None) -> dict:
        return external_cli.effective_runtime_config(options)

    def is_worker_alive(self, pane_id: str) -> bool:
        return self.executor.is_alive(pane_id)

    def start_chat_worker(
        self,
        *,
        session_id: str,
        runtime: str,
        prompt_path: Path,
        result_path: Path,
        work_dir: Path,
        command: str | None = None,
        runtime_options: dict | None = None,
    ) -> dict:
        task = self.planner.plan_external_cli_task(
            task_id=session_id,
            runtime=runtime,
            prompt_path=prompt_path,
            result_path=result_path,
            work_dir=work_dir,
            command=command,
            runtime_options=runtime_options,
        )
        return self.executor.start_chat_worker(task)

    def send_to_worker(self, runtime_meta: dict, text: str, submit: bool = True) -> None:
        self.executor.send(runtime_meta, text, submit=submit)

    def worker_status(self, runtime_meta: dict) -> dict:
        return self.executor.status(runtime_meta)

    def worker_logs(self, runtime_meta: dict, max_bytes: int = 40_000) -> dict:
        return self.executor.logs(runtime_meta, max_bytes=max_bytes)

    def stop_worker(self, runtime_meta: dict) -> None:
        self.executor.stop(runtime_meta)

    def list_skills(self) -> list[dict]:
        return self.skill_registry.list()

    def list_runs(self) -> list[dict]:
        return external_cli.list_runs()

    def start_run(
        self,
        runtime: str,
        prompt: str,
        command: str | None = None,
        timeout_seconds: int = 1800,
        runtime_options: dict | None = None,
    ) -> dict:
        return external_cli.start_run(runtime, prompt, command, timeout_seconds, runtime_options)

    def run_status(self, run_id: str) -> dict:
        return external_cli.status_run(run_id)

    def run_logs(self, run_id: str, max_bytes: int = 120_000) -> dict:
        return external_cli.logs(run_id, max_bytes=max_bytes)

    def send_to_run(self, run_id: str, text: str) -> dict:
        return external_cli.send(run_id, text)

    def stop_run(self, run_id: str) -> dict:
        return external_cli.stop(run_id)
