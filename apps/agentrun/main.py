"""Runtime gateway for the local workbench.

This package maps the API/Web contract onto the in-repo AgentRun runtime under
``apps/agentrun/agentrun``.
"""
from __future__ import annotations

from pathlib import Path

from . import external_cli
from .skill_registry import SkillRegistry


class MainRuntime:
    """Small facade used by the API layer."""

    def __init__(self) -> None:
        self.skill_registry = SkillRegistry(external_cli.ROOT / "skills", external_cli.ROOT)

    def default_runtime(self) -> str:
        return external_cli.default_runtime()

    def effective_runtime_config(self, options: dict | None = None) -> dict:
        return external_cli.effective_runtime_config(options)

    def runtime_choices(self, *, only_valid: bool = True) -> dict:
        return external_cli.runtime_choices(only_valid=only_valid)

    def validate_config(self, provider_type: str | None = None, name: str | None = None, profile_id: str | None = None) -> dict:
        return external_cli.validate_config(provider_type=provider_type, name=name, profile_id=profile_id)

    def is_worker_alive(self, pane_id: str) -> bool:
        return external_cli.is_pane_alive(pane_id)

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
        return external_cli.start_chat_pane(
            session_id,
            runtime,
            prompt_path,
            result_path,
            work_dir,
            command=command,
            runtime_options=runtime_options,
        )

    def run_chat_turn(
        self,
        *,
        session_id: str,
        runtime: str,
        prompt_path: Path,
        result_path: Path,
        work_dir: Path,
        command: str | None = None,
        timeout_seconds: int = 300,
        runtime_options: dict | None = None,
    ) -> dict:
        return external_cli.run_chat_turn(
            session_id,
            runtime,
            prompt_path,
            result_path,
            work_dir,
            command=command,
            timeout_seconds=timeout_seconds,
            runtime_options=runtime_options,
        )

    def send_to_worker(self, runtime_meta: dict, text: str, submit: bool = True) -> None:
        external_cli.send_to_runtime(runtime_meta, text, submit=submit)

    def worker_status(self, runtime_meta: dict) -> dict:
        return external_cli.runtime_meta_status(runtime_meta)

    def worker_logs(self, runtime_meta: dict, max_bytes: int = 40_000) -> dict:
        return external_cli.runtime_meta_logs(runtime_meta, max_bytes=max_bytes)

    def stop_worker(self, runtime_meta: dict) -> None:
        external_cli.stop_runtime_meta(runtime_meta)

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
