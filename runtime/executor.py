"""Executor boundary for external CLI workers."""
from __future__ import annotations

from pathlib import Path

from . import external_cli
from .state import RuntimeTask


class SharedRuntimeWorker:
    """Run Codex/Claude sessions through the shared runtime."""

    def start_chat_worker(self, task: RuntimeTask) -> dict:
        return external_cli.start_chat_pane(
            task.task_id,
            task.runtime,
            task.prompt_path,
            task.result_path,
            task.work_dir,
            command=task.command,
            runtime_options=task.runtime_options,
        )

    def send(self, runtime_meta: dict, text: str, submit: bool = True) -> None:
        external_cli.send_to_runtime(runtime_meta, text, submit=submit)

    def status(self, runtime_meta: dict) -> dict:
        return external_cli.runtime_meta_status(runtime_meta)

    def logs(self, runtime_meta: dict, max_bytes: int = 40_000) -> dict:
        return external_cli.runtime_meta_logs(runtime_meta, max_bytes=max_bytes)

    def stop(self, runtime_meta: dict) -> None:
        external_cli.stop_runtime_meta(runtime_meta)

    def is_alive(self, pane_id: str) -> bool:
        return external_cli.is_pane_alive(pane_id)


class ExternalCliExecutor:
    """Facade over concrete external CLI workers."""

    def __init__(self, worker: SharedRuntimeWorker | None = None) -> None:
        self.worker = worker or SharedRuntimeWorker()

    def start_chat_worker(self, task: RuntimeTask) -> dict:
        return self.worker.start_chat_worker(task)

    def send(self, runtime_meta: dict, text: str, submit: bool = True) -> None:
        self.worker.send(runtime_meta, text, submit=submit)

    def status(self, runtime_meta: dict) -> dict:
        return self.worker.status(runtime_meta)

    def logs(self, runtime_meta: dict, max_bytes: int = 40_000) -> dict:
        return self.worker.logs(runtime_meta, max_bytes=max_bytes)

    def stop(self, runtime_meta: dict) -> None:
        self.worker.stop(runtime_meta)

    def is_alive(self, pane_id: str) -> bool:
        return self.worker.is_alive(pane_id)


def run_dir(path: str | Path) -> Path:
    return Path(path)
