"""Content delivery workflow orchestration.

This module is a lightweight state machine over existing skills. It owns
ordering, gates, run state, and artifact paths; skill scripts keep owning the
actual deterministic work.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from apps.workflows.state import (
    ROOT,
    RUNS_ROOT,
    WorkflowState,
    WorkflowStep,
    append_event,
    new_run_id,
    now,
    read_json,
    rel,
    safe_run_id,
    write_json,
)

WORKFLOW = "content_delivery"
RUN_PREFIX = "content-delivery"
RUN_ROOT = RUNS_ROOT / "content-delivery"
CONTENT_RUNTIME = ROOT / "skills" / "content-generate" / "scripts" / "content_runtime.py"

STEP_DEFS = (
    {
        "id": "intake",
        "title": "需求解析",
        "owner": "content-generate",
        "gates": ["平台/风格/主题明确"],
    },
    {
        "id": "source_selection",
        "title": "素材选择与事实源回读",
        "owner": "knowledge-search",
        "gates": ["检索结果只是候选", "写作前回读 source_path"],
    },
    {
        "id": "draft",
        "title": "草稿生成与人审",
        "owner": "content-generate",
        "gates": ["不得编造素材事实", "用户确认后进入计划"],
    },
    {
        "id": "plan",
        "title": "组装计划",
        "owner": "content-generate",
        "gates": ["用户确认素材顺序和跳过项"],
    },
    {
        "id": "package",
        "title": "成品包",
        "owner": "content-package",
        "gates": ["发布包只落本地 outputs", "不自动发布"],
    },
    {
        "id": "compliance",
        "title": "发布前合规审核",
        "owner": "content-compliance-review",
        "gates": ["结论必须是 pass/needs-edit/blocked"],
    },
    {
        "id": "finalize",
        "title": "会话收尾",
        "owner": "workbench-finalizer",
        "gates": ["记录摘要", "不保存原始对话"],
    },
)


def _run_dir(run_id: str) -> Path:
    safe_run_id(run_id, RUN_PREFIX)
    return RUN_ROOT / run_id


def _state_path(run_id: str) -> Path:
    return _run_dir(run_id) / "state.json"


def _events_path(run_id: str) -> Path:
    return _run_dir(run_id) / "events.jsonl"


def _load_state(run_id: str) -> WorkflowState:
    path = _state_path(run_id)
    if not path.exists():
        raise FileNotFoundError("workflow run not found")
    return WorkflowState.from_dict(read_json(path, {}))


def _save_state(state: WorkflowState) -> dict:
    state.updated_at = now()
    write_json(_state_path(state.run_id), state.as_dict())
    return state.as_dict()


def _event(state: WorkflowState, event_type: str, message: str, data: dict | None = None) -> None:
    append_event(
        _events_path(state.run_id),
        {
            "ts": now(),
            "type": event_type,
            "workflow": WORKFLOW,
            "run_id": state.run_id,
            "message": message,
            "data": data or {},
        },
    )


def _steps() -> list[WorkflowStep]:
    return [
        WorkflowStep(
            id=item["id"],
            title=item["title"],
            owner=item["owner"],
            gates=list(item["gates"]),
        )
        for item in STEP_DEFS
    ]


def list_runs(limit: int = 50) -> list[dict]:
    if not RUN_ROOT.exists():
        return []
    runs: list[dict] = []
    for path in sorted(RUN_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        state_path = path / "state.json"
        if not state_path.exists():
            continue
        data = read_json(state_path, {})
        runs.append({
            "run_id": data.get("run_id"),
            "status": data.get("status"),
            "current_step": data.get("current_step"),
            "brief": (data.get("inputs") or {}).get("brief", ""),
            "updated_at": data.get("updated_at"),
            "run_dir": data.get("run_dir"),
        })
        if len(runs) >= limit:
            break
    return runs


def start(brief: str, *, platform: str = "xiaohongshu", style: str = "知识科普",
          sources: list[dict] | None = None) -> dict:
    clean_brief = str(brief or "").strip()
    if not clean_brief:
        raise ValueError("brief is required")
    run_id = new_run_id(RUN_PREFIX)
    run_dir = _run_dir(run_id)
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    inputs = {
        "brief": clean_brief,
        "platform": platform or "xiaohongshu",
        "style": style or "知识科普",
    }
    state = WorkflowState(
        workflow=WORKFLOW,
        run_id=run_id,
        status="waiting",
        current_step="source_selection",
        created_at=now(),
        updated_at=now(),
        inputs=inputs,
        run_dir=rel(run_dir),
        steps=_steps(),
        outputs={},
    )
    state.step("intake").patch(
        status="completed",
        message="需求已记录，等待素材选择。",
        artifacts={"inputs": rel(run_dir / "inputs.json")},
    )
    state.step("source_selection").patch(status="waiting", message="请提供选中的素材列表，或先完成 KB 检索。")
    write_json(run_dir / "inputs.json", inputs)
    _save_state(state)
    _event(state, "workflow.started", "content_delivery workflow 已创建")
    if sources:
        return continue_run(run_id, "set_sources", {"sources": sources})
    return get(run_id)


def get(run_id: str) -> dict:
    state = _load_state(run_id)
    payload = state.as_dict()
    payload["events_path"] = rel(_events_path(run_id))
    return payload


def cancel(run_id: str, reason: str = "") -> dict:
    state = _load_state(run_id)
    if state.status in {"completed", "failed", "cancelled"}:
        return get(run_id)
    state.status = "cancelled"
    state.step(state.current_step).patch(status="skipped", message=reason or "用户取消 workflow。")
    _event(state, "workflow.cancelled", reason or "workflow 已取消")
    return _save_state(state)


def continue_run(run_id: str, action: str, payload: dict | None = None) -> dict:
    state = _load_state(run_id)
    if state.status in {"completed", "failed", "cancelled"}:
        raise RuntimeError(f"workflow is already {state.status}")
    payload = payload or {}
    if action == "set_sources":
        _set_sources(state, payload)
    elif action == "regenerate_draft":
        _generate_draft(state, payload)
    elif action == "approve_draft":
        _approve_draft(state)
    elif action == "approve_plan":
        _approve_plan(state)
    elif action == "mark_packaged":
        _mark_packaged(state, payload)
    elif action == "mark_compliance":
        _mark_compliance(state, payload)
    else:
        raise ValueError(f"unsupported workflow action: {action}")
    _event(state, f"workflow.{action}", f"workflow action executed: {action}", payload)
    return _save_state(state)


def _set_sources(state: WorkflowState, payload: dict) -> None:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("payload.sources must be a list")
    run_dir = Path(state.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    sources_path = run_dir / "artifacts" / "sources.json"
    write_json(sources_path, sources)
    state.outputs["sources"] = rel(sources_path)
    state.step("source_selection").patch(
        status="completed",
        message=f"已记录 {len(sources)} 条素材候选。生成草稿前仍需确保 source_path 已回读。",
        artifacts={"sources": rel(sources_path), "count": len(sources)},
    )
    _generate_draft(state, {})


def _generate_draft(state: WorkflowState, payload: dict) -> None:
    run_dir = ROOT / state.run_dir
    inputs = {**state.inputs, **payload}
    draft_path = run_dir / "artifacts" / "draft.json"
    sources_path = state.outputs.get("sources")
    cmd = [
        sys.executable,
        str(CONTENT_RUNTIME),
        "text",
        "draft",
        "--brief",
        str(inputs["brief"]),
        "--platform",
        str(inputs["platform"]),
        "--style",
        str(inputs["style"]),
        "--out",
        str(draft_path),
        "--allow-write",
    ]
    if sources_path:
        cmd.extend(["--sources", str(ROOT / sources_path)])
    _run_command(cmd, run_dir / "logs" / "draft.log")
    state.outputs["draft"] = rel(draft_path)
    state.current_step = "draft"
    state.status = "waiting"
    state.step("draft").patch(
        status="waiting",
        message="草稿已生成，等待用户审核；确认后执行 approve_draft。",
        artifacts={"draft": rel(draft_path)},
    )


def _approve_draft(state: WorkflowState) -> None:
    draft = state.outputs.get("draft")
    if not draft:
        raise RuntimeError("draft artifact is missing")
    run_dir = ROOT / state.run_dir
    plan_path = run_dir / "artifacts" / "plan.json"
    cmd = [
        sys.executable,
        str(CONTENT_RUNTIME),
        "plan",
        "build",
        "--draft",
        str(ROOT / draft),
        "--out",
        str(plan_path),
        "--allow-write",
    ]
    _run_command(cmd, run_dir / "logs" / "plan.log")
    state.outputs["plan"] = rel(plan_path)
    state.current_step = "plan"
    state.status = "waiting"
    state.step("draft").patch(status="completed", message="草稿已确认。")
    state.step("plan").patch(
        status="waiting",
        message="组装计划已生成，等待用户确认；确认后执行 approve_plan。",
        artifacts={"plan": rel(plan_path)},
    )


def _approve_plan(state: WorkflowState) -> None:
    if not state.outputs.get("plan"):
        raise RuntimeError("plan artifact is missing")
    state.current_step = "package"
    state.status = "waiting"
    state.step("plan").patch(status="completed", message="组装计划已确认。")
    state.step("package").patch(
        status="waiting",
        message="请由 content-package 生成成品包后执行 mark_packaged，并传入 package_path。",
        artifacts={"plan": state.outputs["plan"]},
    )


def _mark_packaged(state: WorkflowState, payload: dict) -> None:
    package_path = str(payload.get("package_path") or "").strip()
    if not package_path:
        raise ValueError("payload.package_path is required")
    state.outputs["package"] = package_path
    state.current_step = "compliance"
    state.status = "waiting"
    state.step("package").patch(
        status="completed",
        message="成品包已登记，等待合规审核。",
        artifacts={"package": package_path},
    )
    state.step("compliance").patch(status="waiting", message="请执行合规审核并用 mark_compliance 写入结论。")


def _mark_compliance(state: WorkflowState, payload: dict) -> None:
    result = str(payload.get("result") or "").strip()
    if result not in {"pass", "needs-edit", "blocked"}:
        raise ValueError("payload.result must be pass, needs-edit, or blocked")
    note = str(payload.get("note") or "").strip()
    state.outputs["compliance"] = {"result": result, "note": note}
    if result == "pass":
        state.current_step = "finalize"
        state.status = "completed"
        state.step("compliance").patch(status="completed", message=note or "合规审核通过。")
        state.step("finalize").patch(status="completed", message="workflow 已完成；如有实质会话摘要，可再调用 workbench-finalizer。")
        return
    if result == "needs-edit":
        state.current_step = "draft"
        state.status = "waiting"
        state.step("compliance").patch(status="completed", message=note or "需要修改后复审。")
        state.step("draft").patch(status="waiting", message="合规审核要求修改，请调整草稿后重新审核。")
        return
    state.status = "failed"
    state.current_step = "compliance"
    state.step("compliance").patch(status="failed", message=note or "合规审核 blocked。")
    state.errors.append(note or "compliance blocked")


def _run_command(cmd: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60, check=False)
    log_path.write_text(
        "## command\n"
        + json.dumps(cmd, ensure_ascii=False)
        + "\n\n## stdout\n"
        + proc.stdout
        + "\n\n## stderr\n"
        + proc.stderr,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"command failed: {proc.returncode}").strip())
