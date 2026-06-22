"""Planning boundary for runtime tasks.

The current GUI already writes concrete prompt/result files before invoking the
runtime. The planner records that contract as a typed RuntimeTask so the UI does
not need to understand worker/provider internals.
"""
from __future__ import annotations

from pathlib import Path

from .state import RuntimeTask


class Planner:
    """Build runtime tasks from workbench-level inputs."""

    def plan_external_cli_task(
        self,
        *,
        task_id: str,
        runtime: str,
        prompt_path: Path,
        result_path: Path,
        work_dir: Path,
        command: str | None = None,
        runtime_options: dict | None = None,
    ) -> RuntimeTask:
        return RuntimeTask(
            task_id=task_id,
            runtime=runtime,
            prompt_path=prompt_path,
            result_path=result_path,
            work_dir=work_dir,
            command=command,
            runtime_options=runtime_options or {},
        )
