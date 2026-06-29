"""Run Directory Contract 读写(见 design/02 §3-§4)。

唯一事实:turn/task 认 result.json;session 认 status.json + events.jsonl。
done 只能由 result.json 校验通过进入;超时归 failed + failure_reason: timeout。
"""
from __future__ import annotations

from typing import Any

from agentrun.core.events import append_event
from agentrun.core.jsonio import read_json, write_json_atomic
from agentrun.core.run import (
    DONE,
    OUTCOMES,
    RunRequest,
    utc_now,
)
from agentrun.core.rundir import RunPaths
from agentrun.core.schema import SchemaError, validate_contract

STATUS_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1


def write_request(paths: RunPaths, request: RunRequest) -> dict[str, Any]:
    paths.ensure()
    data = request.to_dict()
    write_json_atomic(paths.request_file, data)
    return data


def write_status(
    paths: RunPaths,
    request: RunRequest,
    state: str,
    *,
    failure_reason: str | None = None,
    provider_status: dict[str, Any] | None = None,
    message: str = "",
) -> dict[str, Any]:
    data = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "run_id": request.run_id,
        "run_type": request.run_type,
        "project_id": request.project_id,
        "state": state,
        "failure_reason": failure_reason,
        "provider": request.provider,
        "provider_status": provider_status or {},
        "message": message,
        "updated_at": utc_now(),
    }
    write_json_atomic(paths.status_file, data)
    return data


def read_status(paths: RunPaths) -> dict[str, Any] | None:
    return read_json(paths.status_file, None)


def write_result(
    paths: RunPaths,
    request: RunRequest,
    outcome: str,
    *,
    summary: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"非法 result.outcome: {outcome}")
    data = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": request.run_id,
        "outcome": outcome,
        "summary": summary,
        "artifacts": artifacts or [],
        "errors": errors or [],
        "validation": validation or {"commands": [], "passed": False},
    }
    write_json_atomic(paths.result_file, data)
    return data


def read_result(paths: RunPaths) -> dict[str, Any] | None:
    return read_json(paths.result_file, None)


def validate_result(paths: RunPaths, schema_ref: str | None = None) -> tuple[bool, str | None]:
    """校验 result.json 存在且满足 result_schema。"""
    data = read_result(paths)
    if data is None:
        return False, "result_missing"
    if not isinstance(data, dict):
        return False, "schema_invalid"
    if data.get("outcome") not in OUTCOMES:
        return False, "schema_invalid"
    try:
        validate_contract(data, schema_ref or "result")
    except (FileNotFoundError, SchemaError):
        return False, "schema_invalid"
    return True, None


def event(paths: RunPaths, request: RunRequest, event_type: str, data: dict[str, Any]) -> None:
    append_event(paths, request.run_id, request.run_type, event_type, data)


def mark_done_if_valid(paths: RunPaths, request: RunRequest) -> dict[str, Any]:
    """校验 result 通过则推进 done,否则 failed(result_missing/schema_invalid)。"""
    ok, reason = validate_result(paths, request.result_schema or "result")
    if ok:
        status = write_status(paths, request, DONE, message="result 校验通过")
        event(paths, request, "result.written", {"outcome": read_result(paths).get("outcome")})
        return status
    return write_status(paths, request, "failed", failure_reason=reason, message=f"result 校验失败:{reason}")
