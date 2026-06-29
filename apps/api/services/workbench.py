#!/usr/bin/env python3
"""Service layer for the personal Agent workbench API."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from apps.api import file_browser
from apps.api import health
from apps.api.services.workbench_config import (
    _command_for_runtime,
    _runtime_options_from_config,
    runtime_config_payload,
    save_workbench_config,
    validate_runtime_config,
    workbench_config,
)
from apps.api.services.workbench_support import (
    CHAT_WAIT_SECONDS,
    CONTENT_RUNTIME,
    MAIN_RUNTIME,
    NONINTERACTIVE_RUNTIMES,
    ROOT,
    SESSIONS_DIR,
    append_jsonl as _append_jsonl,
    elapsed_seconds as _elapsed_seconds,
    now as _now,
    read_json as _read_json,
    session_dir as _session_dir,
    valid_user_runtime as _valid_user_runtime,
    valid_runtime as _valid_runtime,
    write_json as _write_json,
)


def _load_messages(session_id: str) -> list[dict]:
    path = _session_dir(session_id) / "messages.jsonl"
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            messages.append(json.loads(line))
    return messages


def _write_messages(session_id: str, messages: list[dict]) -> None:
    path = _session_dir(session_id) / "messages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for message in messages:
            fh.write(json.dumps(message, ensure_ascii=False) + "\n")


def _load_events(session_id: str) -> list[dict]:
    path = _session_dir(session_id) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def create_session(title: str = "", runtime: str | None = None) -> dict:
    config = workbench_config()
    selected_runtime = _valid_user_runtime(runtime or config["chat_provider"], config["chat_provider"])
    clean_title = str(title or "").strip()
    session_id = f"chat-{uuid.uuid4().hex[:12]}"
    path = _session_dir(session_id)
    path.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": session_id,
        "title": clean_title or "新会话",
        "runtime": selected_runtime,
        "runtime_command": _command_for_runtime(config, selected_runtime),
        "runtime_options": _runtime_options_from_config(config),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write_json(path / "state.json", state)
    _write_json(path / "linked_outputs.json", [])
    _write_json(path / "pending_turns.json", [])
    _append_jsonl(path / "events.jsonl", {"ts": _now(), "type": "chat.created", "session_id": session_id})
    return state


def _session_title_from_message(content: str) -> str:
    lines = [line.strip() for line in str(content or "").splitlines()]
    first_line = next((line for line in lines if line), "新会话")
    match = re.match(r"^(.+?[。！？!?])", first_line)
    title = match.group(1).strip() if match else first_line
    return title[:36] + "..." if len(title) > 36 else title


def list_sessions() -> list[dict]:
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for path in sorted(SESSIONS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_dir() and (path / "state.json").exists():
            sessions.append(_read_json(path / "state.json", {}))
    return sessions


def get_session(session_id: str) -> dict:
    path = _session_dir(session_id)
    if not path.exists():
        raise FileNotFoundError("session not found")
    _sync_pending_turns(session_id)
    state = _read_json(path / "state.json", {})
    payload = {
        **state,
        "messages": _load_messages(session_id),
        "events": _load_events(session_id),
        "linked_outputs": _read_json(path / "linked_outputs.json", []),
        "pending_turns": _pending_turns(session_id),
        "runtime_status": session_runtime_status(session_id),
        "runtime_log_tail": session_runtime_logs(session_id, max_bytes=16_000),
    }
    payload["operator"] = operator_view(session_id)
    return payload


def delete_session(session_id: str) -> dict:
    path = _session_dir(session_id)
    if not path.exists():
        raise FileNotFoundError("session not found")

    resolved = path.resolve()
    sessions_root = SESSIONS_DIR.resolve()
    if resolved == sessions_root or sessions_root not in resolved.parents:
        raise ValueError("refuse to delete path outside sessions dir")

    warnings: list[str] = []
    try:
        state = _read_json(path / "state.json", {})
    except Exception as exc:  # noqa: BLE001
        state = {}
        warnings.append(f"state.json 读取失败，继续删除目录：{exc}")

    runtime_meta = state.get("runtime_meta") or {}
    if runtime_meta:
        try:
            MAIN_RUNTIME.stop_worker(runtime_meta)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"停止 runtime 失败，已继续删除目录：{exc}")

    shutil.rmtree(resolved)
    return {
        "ok": True,
        "session_id": session_id,
        "deleted_path": str(resolved),
        "warnings": warnings,
    }


def delete_sessions(session_ids: list[str]) -> dict:
    if not session_ids:
        raise ValueError("session_ids is required")
    deleted = []
    errors = []
    for session_id in session_ids:
        try:
            deleted.append(delete_session(str(session_id)))
        except Exception as exc:  # noqa: BLE001
            errors.append({"session_id": str(session_id), "error": str(exc)})
    return {"ok": not errors, "deleted": deleted, "errors": errors}


def session_runtime_status(session_id: str) -> dict:
    state = _read_json(_session_dir(session_id) / "state.json", {})
    runtime_meta = state.get("runtime_meta") or {}
    if not runtime_meta:
        return {"ok": False, "state": "not_started"}
    status = MAIN_RUNTIME.worker_status(runtime_meta)
    pending = _pending_turns(session_id)
    if pending:
        current = pending[0]
        status = {
            **status,
            "provider_state": status.get("state"),
            "state": current.get("state", "running"),
            "current_turn_id": current.get("turn_id"),
            "current_result_path": current.get("result_path"),
            "pending_turns": pending,
        }
    return {
        **status,
        "runtime": runtime_meta.get("runtime"),
        "provider_run_id": runtime_meta.get("provider_run_id"),
        "output_path": runtime_meta.get("output_path"),
    }


def session_runtime_logs(session_id: str, max_bytes: int = 40_000) -> dict:
    state = _read_json(_session_dir(session_id) / "state.json", {})
    runtime_meta = state.get("runtime_meta") or {}
    if not runtime_meta:
        return {"ok": False, "text": ""}
    return MAIN_RUNTIME.worker_logs(runtime_meta, max_bytes=max_bytes)


def stop_session_runtime(session_id: str) -> dict:
    path = _session_dir(session_id)
    state = _read_json(path / "state.json", {})
    runtime_meta = state.get("runtime_meta") or {}
    if not runtime_meta:
        return {"ok": True, "state": "not_started"}
    MAIN_RUNTIME.stop_worker(runtime_meta)
    pending = _pending_turns(session_id)
    for turn in pending:
        result = {
            "status": "failed",
            "assistant_message": "已停止当前 runtime。本轮任务没有完成。",
            "summary": "runtime stopped by user",
            "outputs": [],
            "questions": [],
            "errors": ["runtime stopped by user"],
        }
        _replace_message(
            session_id,
            turn.get("assistant_message_id", ""),
            result["assistant_message"],
            {"pending": False, "turn_id": turn.get("turn_id"), "result": result},
        )
    _save_pending_turns(session_id, [])
    state["updated_at"] = _now()
    state["runtime_meta"] = {**runtime_meta, "stopped_at": _now()}
    _write_json(path / "state.json", state)
    event = {
        "ts": _now(),
        "type": "runtime.stopped",
        "session_id": session_id,
        "title": "runtime 已停止",
        "status": "stopped",
        "data": {
            "runtime": runtime_meta.get("runtime"),
            "pane_id": runtime_meta.get("pane_id"),
            "provider_run_id": runtime_meta.get("provider_run_id"),
        },
    }
    _append_jsonl(path / "events.jsonl", event)
    return {"ok": True, "state": "stopped", "event": event}


def _tmux_turn_payload(user_content: str) -> str:
    return user_content


def _build_runtime_contract(session_id: str, current_turn_path: Path) -> str:
    return f"""# 个人 Agent 工作台 Runtime Contract

你正在 `/Users/yang/agents/agent` 项目中，作为个人 Agent 工作台的 tmux CLI runtime。

这份 contract 只在当前 tmux 会话启动时发送一次。后续每一轮，终端里只会收到用户在 UI 输入框里的原文。

## 每轮处理协议

- 每次收到用户消息后，先读取当前 turn 描述文件：
  `{current_turn_path}`
- `current_turn.json` 会给出本轮的 `turn_id`、`raw_user_message_file`、`prompt_file`、`result_file`。
- 用户原文以终端收到的内容为准；如果需要完整上下文、最近对话或输出格式，读取 `prompt_file`。
- 完成本轮后，必须把结构化结果写入 `result_file`。

## result JSON 格式

```json
{{
  "status": "success|partial|failed",
  "assistant_message": "给用户看的中文回复",
  "summary": "本 turn 摘要",
  "outputs": [],
  "questions": [],
  "errors": []
}}
```

`assistant_message` 会直接显示在 UI 聊天里。不要把 token、secret、cookie、private key 或完整 JWT 写入结果。
"""


def _build_turn_prompt(session_id: str, turn_id: str, user_content: str,
                       messages: list[dict], raw_path: Path, result_path: Path) -> str:
    recent = messages[-10:]
    history = "\n".join(
        f"{item.get('role', 'unknown')}: {item.get('content', '')}"
        for item in recent
        if item.get("content")
    )
    return f"""# 图书运营工作台会话 turn

你正在 `/Users/yang/agents/agent` 项目中，通过 AgentRun provider 处理 GUI 聊天请求。

## 固定约束

- 遵守项目 `AGENTS.md`：默认中文输出，面向学而思图书运营场景。
- 不自动发布，不群发，不调用外部发布 API。
- 不泄露 token、secret、cookie、private key 或完整 JWT。
- 需要写文件时只写项目允许目录，优先写 `outputs/`、`workspace/`、`runs/`。
- 如果任务需要大量处理，直接在当前 CLI 会话中使用可用工具完成；不要只给抽象建议。
- 完成本 turn 后必须写入 runtime 要求的 result JSON，UI 会把 runtime result 回填到本 turn。

## 会话信息

- session_id: `{session_id}`
- turn_id: `{turn_id}`
- project_root: `{ROOT}`
- raw_user_message_file: `{raw_path}`
- result_file: `{result_path}`

## 最近对话

{history or "（无）"}

## 用户最新消息

用户最新消息已原封不动写入 `{raw_path}`。下面同步展示一份内容，处理时以 raw 文件为准。

<<<WORKBENCH_RAW_USER_MESSAGE
{user_content}
WORKBENCH_RAW_USER_MESSAGE

## 输出要求

如果当前执行环境没有提供 `AGENTRUN_RESULT_FILE`，请把本 turn 的最终结果写入：

`{result_path}`

JSON 格式：

```json
{{
  "status": "success|partial|failed",
  "assistant_message": "给用户看的中文回复",
  "summary": "本 turn 摘要",
  "outputs": [],
  "questions": [],
  "errors": []
}}
```

`assistant_message` 应该是可以直接显示在 GUI 聊天里的回复。不要把敏感值写入结果。
如果当前执行环境提供了 `AGENTRUN_RESULT_FILE`，请优先写入 AgentRun result 契约；API 会把 `summary` 映射回聊天框。
"""


def _pending_turns(session_id: str) -> list[dict]:
    return _read_json(_session_dir(session_id) / "pending_turns.json", [])


def _save_pending_turns(session_id: str, turns: list[dict]) -> None:
    _write_json(_session_dir(session_id) / "pending_turns.json", turns)


def _patch_pending_turn(session_id: str, turn_id: str, patch: dict) -> None:
    turns = _pending_turns(session_id)
    changed = False
    for turn in turns:
        if turn.get("turn_id") == turn_id:
            turn.update(patch)
            changed = True
            break
    if changed:
        _save_pending_turns(session_id, turns)


def _remove_pending_turn(session_id: str, turn_id: str) -> None:
    _save_pending_turns(
        session_id,
        [turn for turn in _pending_turns(session_id) if turn.get("turn_id") != turn_id],
    )


def _read_turn_result(turn: dict) -> dict | None:
    result_path = Path(turn["result_path"])
    if not result_path.exists():
        return None
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "assistant_message": f"runtime result.json 解析失败：{exc}",
            "summary": "result parse failed",
            "outputs": [],
            "questions": [],
            "errors": [str(exc)],
        }
    return data


def _assistant_text_from_result(data: dict) -> str:
    if data.get("assistant_message"):
        return str(data["assistant_message"]).strip()
    parts = []
    if data.get("summary"):
        parts.append(str(data["summary"]).strip())
    questions = data.get("questions") or []
    if questions:
        parts.append("待确认：" + "；".join(str(item) for item in questions))
    errors = data.get("errors") or []
    if not parts and errors:
        parts.append("runtime 执行失败：" + "；".join(str(item) for item in errors))
    return "\n\n".join(part for part in parts if part) or "runtime 已完成，但没有返回可展示内容。"


def _replace_message(session_id: str, message_id: str, content: str, patch: dict | None = None) -> None:
    messages = _load_messages(session_id)
    for message in messages:
        if message.get("id") == message_id:
            message["content"] = content
            message["ts"] = _now()
            if patch:
                message.update(patch)
            break
    _write_messages(session_id, messages)


def _turn_result_path(session_id: str, turn_id: str) -> Path:
    return _session_dir(session_id) / "turns" / turn_id / "result.json"


def _message_needs_result_repair(message: dict) -> bool:
    if message.get("role") != "assistant" or not message.get("turn_id"):
        return False
    if message.get("pending"):
        return True
    result = message.get("result") or {}
    errors = " ".join(str(item) for item in result.get("errors", []))
    return (
        result.get("summary") == "runtime delivery failed"
        or "did not become idle before prompt submission" in errors
    )


def _sync_completed_turn_messages(session_id: str) -> list[dict]:
    path = _session_dir(session_id)
    messages = _load_messages(session_id)
    changed = False
    repaired_events = []
    for message in messages:
        if not _message_needs_result_repair(message):
            continue
        turn_id = message["turn_id"]
        result_path = _turn_result_path(session_id, turn_id)
        if not result_path.exists():
            continue
        result = _read_turn_result({"result_path": str(result_path)})
        if not result:
            continue
        message["content"] = _assistant_text_from_result(result)
        message["ts"] = _now()
        message["pending"] = False
        message["result"] = result
        changed = True
        event = {
            "ts": _now(),
            "type": "runtime.result_repaired",
            "session_id": session_id,
            "title": "runtime turn 结果已修复回填",
            "status": result.get("status", "success"),
            "data": {
                "turn_id": turn_id,
                "result_path": str(result_path),
                "outputs": result.get("outputs", []),
                "errors": result.get("errors", []),
            },
        }
        _append_jsonl(path / "events.jsonl", event)
        repaired_events.append(event)
    if changed:
        _write_messages(session_id, messages)
        state = _read_json(path / "state.json", {})
        state["updated_at"] = _now()
        _write_json(path / "state.json", state)
    return repaired_events


def _sync_pending_turns(session_id: str) -> list[dict]:
    path = _session_dir(session_id)
    turns = _pending_turns(session_id)
    remaining = []
    completed_events = []
    for turn in turns:
        if turn.get("state") == "done":
            continue
        result = _read_turn_result(turn)
        if result is None:
            remaining.append(turn)
            continue
        text = _assistant_text_from_result(result)
        _replace_message(
            session_id,
            turn["assistant_message_id"],
            text,
            {"pending": False, "turn_id": turn["turn_id"], "result": result},
        )
        event = {
            "ts": _now(),
            "type": "runtime.result_ready",
            "session_id": session_id,
            "title": "runtime turn 完成",
            "status": result.get("status", "success"),
            "data": {
                "turn_id": turn["turn_id"],
                "runtime": turn.get("runtime"),
                "result_path": turn["result_path"],
                "outputs": result.get("outputs", []),
                "errors": result.get("errors", []),
            },
        }
        _append_jsonl(path / "events.jsonl", event)
        completed_events.append(event)
    _save_pending_turns(session_id, remaining)
    if completed_events:
        state = _read_json(path / "state.json", {})
        state["updated_at"] = _now()
        _write_json(path / "state.json", state)
    return completed_events + _sync_completed_turn_messages(session_id)


def _wait_for_turn(session_id: str, turn: dict, timeout_seconds: float) -> list[dict]:
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        events = _sync_pending_turns(session_id)
        if not any(item.get("turn_id") == turn["turn_id"] for item in _pending_turns(session_id)):
            return events
        time.sleep(0.5)
    return []


def _has_running_turn(session_id: str) -> bool:
    _sync_pending_turns(session_id)
    return bool(_pending_turns(session_id))


def _append_runtime_event(session_id: str, event: dict) -> None:
    path = _session_dir(session_id)
    _append_jsonl(path / "events.jsonl", event)
    state = _read_json(path / "state.json", {})
    state["updated_at"] = _now()
    _write_json(path / "state.json", state)


def _ensure_chat_runtime(session_id: str, startup_contract_path: Path,
                         result_path: Path) -> tuple[dict, str]:
    path = _session_dir(session_id)
    state = _read_json(path / "state.json", {})
    runtime_meta = state.get("runtime_meta") or {}
    runtime_status = MAIN_RUNTIME.worker_status(runtime_meta) if runtime_meta else {}
    pane_id = runtime_meta.get("pane_id", "")
    runtime_alive = bool(runtime_status.get("ok") and runtime_status.get("state") in {"running", "idle"})
    if not runtime_alive and pane_id:
        runtime_alive = MAIN_RUNTIME.is_worker_alive(pane_id)
    if runtime_alive:
        if not runtime_meta.get("startup_contract_version"):
            MAIN_RUNTIME.send_to_worker(runtime_meta, startup_contract_path.read_text(encoding="utf-8"))
            runtime_meta["startup_contract_path"] = str(startup_contract_path)
            runtime_meta["startup_contract_version"] = 1
            runtime_meta["startup_contract_migrated_at"] = _now()
            state["runtime_meta"] = runtime_meta
            state["updated_at"] = _now()
            _write_json(path / "state.json", state)
            return runtime_meta, "contract"
        return runtime_meta, "send"

    config = workbench_config()
    runtime = _valid_runtime(state.get("runtime") or config["chat_provider"], config["chat_provider"])
    command = state.get("runtime_command") or _command_for_runtime(config, runtime)
    runtime_options = state.get("runtime_options") or _runtime_options_from_config(config)
    runtime_meta = MAIN_RUNTIME.start_chat_worker(
        session_id=session_id,
        runtime=runtime,
        prompt_path=startup_contract_path,
        result_path=result_path,
        work_dir=path / "runtime",
        command=command,
        runtime_options=runtime_options,
    )
    state["runtime"] = runtime
    state["runtime_command"] = command
    state["runtime_options"] = runtime_options
    state["runtime_meta"] = runtime_meta
    state["updated_at"] = _now()
    _write_json(path / "state.json", state)
    return runtime_meta, "contract"


def _run_direct_chat_turn(session_id: str, turn: dict) -> None:
    path = _session_dir(session_id)
    state = _read_json(path / "state.json", {})
    config = workbench_config()
    runtime = _valid_runtime(state.get("runtime") or config["chat_provider"], config["chat_provider"])
    command = state.get("runtime_command") or _command_for_runtime(config, runtime)
    runtime_options = state.get("runtime_options") or _runtime_options_from_config(config)
    runtime_meta = MAIN_RUNTIME.run_chat_turn(
        session_id=turn["turn_id"],
        runtime=runtime,
        prompt_path=Path(turn["prompt_path"]),
        result_path=Path(turn["result_path"]),
        work_dir=path / "runtime" / "direct",
        command=command,
        timeout_seconds=int(CHAT_WAIT_SECONDS),
        runtime_options=runtime_options,
    )
    result = _read_turn_result(turn) or runtime_meta.get("result") or {
        "status": "failed",
        "assistant_message": "runtime 已结束，但没有写入 result.json。",
        "summary": "missing result",
        "outputs": [],
        "questions": [],
        "errors": ["missing result.json"],
    }
    _replace_message(
        session_id,
        turn["assistant_message_id"],
        _assistant_text_from_result(result),
        {"pending": False, "turn_id": turn["turn_id"], "result": result},
    )
    _remove_pending_turn(session_id, turn["turn_id"])
    state["runtime"] = runtime
    state["runtime_command"] = command
    state["runtime_options"] = runtime_options
    state["runtime_meta"] = runtime_meta
    state["updated_at"] = _now()
    _write_json(path / "state.json", state)
    _append_runtime_event(
        session_id,
        {
            "ts": _now(),
            "type": "runtime.result_ready",
            "session_id": session_id,
            "title": "一次性 runtime turn 完成",
            "status": result.get("status", "success"),
            "data": {
                "turn_id": turn["turn_id"],
                "runtime": runtime,
                "result_path": turn["result_path"],
                "provider_run_id": runtime_meta.get("run_id"),
                "outputs": result.get("outputs", []),
                "errors": result.get("errors", []),
            },
        },
    )


def _deliver_chat_turn(session_id: str, turn: dict, startup_contract_path: Path, result_path: Path, payload: str) -> None:
    try:
        state = _read_json(_session_dir(session_id) / "state.json", {})
        config = workbench_config()
        runtime = _valid_runtime(state.get("runtime") or config["chat_provider"], config["chat_provider"])
        if runtime in NONINTERACTIVE_RUNTIMES:
            _run_direct_chat_turn(session_id, turn)
            return
        runtime_meta, delivery_mode = _ensure_chat_runtime(session_id, startup_contract_path, result_path)
        if delivery_mode == "send":
            MAIN_RUNTIME.send_to_worker(runtime_meta, payload)
        _patch_pending_turn(
            session_id,
            turn["turn_id"],
            {
                "state": "running",
                "runtime": runtime_meta.get("runtime"),
                "pane_id": runtime_meta.get("pane_id"),
                "delivered_at": _now(),
                "delivery_mode": delivery_mode,
            },
        )
        _append_runtime_event(
            session_id,
            {
                "ts": _now(),
                "type": "runtime.contract" if delivery_mode == "contract" else "runtime.send",
                "session_id": session_id,
                "title": "runtime provider 已接管",
                "status": "running",
                "data": {
                    "turn_id": turn["turn_id"],
                    "runtime": runtime_meta.get("runtime"),
                    "pane_id": runtime_meta.get("pane_id"),
                    "prompt_path": turn["prompt_path"],
                    "raw_user_message_path": turn["raw_user_message_path"],
                    "current_turn_path": turn["current_turn_path"],
                    "sent_to_tmux_path": turn["sent_to_tmux_path"],
                    "result_path": turn["result_path"],
                    "startup_contract_path": runtime_meta.get("startup_contract_path"),
                    "delivery_mode": delivery_mode,
                    "command": runtime_meta.get("command"),
                    "argv": runtime_meta.get("argv"),
                    "cwd": runtime_meta.get("cwd"),
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "failed",
            "assistant_message": f"runtime 投递失败：{exc}",
            "summary": "runtime delivery failed",
            "outputs": [],
            "questions": [],
            "errors": [str(exc)],
        }
        try:
            _replace_message(
                session_id,
                turn["assistant_message_id"],
                result["assistant_message"],
                {"pending": False, "turn_id": turn["turn_id"], "result": result},
            )
            _remove_pending_turn(session_id, turn["turn_id"])
            _append_runtime_event(
                session_id,
                {
                    "ts": _now(),
                    "type": "runtime.delivery_failed",
                    "session_id": session_id,
                    "title": "runtime 投递失败",
                    "status": "failed",
                    "data": {"turn_id": turn["turn_id"], "error": str(exc)},
                },
            )
        except Exception:
            pass


def _classify(text: str) -> str:
    lowered = text.lower()
    content_words = ["出一篇", "生成内容", "写小红书", "朋友圈文案", "家长群", "书单", "读书笔记", "文案", "图文", "短视频"]
    search_words = ["搜索", "查一下", "找一下", "有哪些", "调研", "素材"]
    design_words = ["设计", "方案", "规划", "架构"]
    if any(word in lowered for word in content_words):
        return "content"
    if any(word in lowered for word in search_words):
        return "search"
    if any(word in lowered for word in design_words):
        return "design"
    return "qa"


def _draft_preview(brief: str, platform: str = "xiaohongshu", style: str = "知识科普") -> dict:
    cmd = [
        sys.executable,
        str(CONTENT_RUNTIME),
        "text",
        "draft",
        "--brief",
        brief,
        "--platform",
        platform,
        "--style",
        style,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()}
    try:
        return {"ok": True, "draft": json.loads(proc.stdout)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"draft JSON parse failed: {exc}", "raw": proc.stdout}


def add_message(session_id: str, content: str, wait_seconds: float = 0) -> dict:
    path = _session_dir(session_id)
    if not path.exists():
        raise FileNotFoundError("session not found")
    if _has_running_turn(session_id):
        raise RuntimeError("上一轮 runtime 仍在执行，请稍后刷新或在诊断面板查看状态后再继续。")

    user_msg = {"id": f"msg-{uuid.uuid4().hex[:10]}", "role": "user", "content": content, "ts": _now()}
    _append_jsonl(path / "messages.jsonl", user_msg)

    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    turn_dir = path / "turns" / turn_id
    turn_dir.mkdir(parents=True, exist_ok=True)
    result_path = turn_dir / "result.json"
    prompt_path = turn_dir / "prompt.md"
    raw_path = turn_dir / "raw_user_message.txt"
    sent_path = turn_dir / "sent_to_tmux.txt"
    current_turn_path = path / "current_turn.json"
    startup_contract_path = path / "runtime_contract.md"
    messages = _load_messages(session_id)
    raw_path.write_text(content, encoding="utf-8")
    prompt_path.write_text(
        _build_turn_prompt(session_id, turn_id, content, messages, raw_path, result_path),
        encoding="utf-8",
    )
    _write_json(
        current_turn_path,
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "created_at": _now(),
            "raw_user_message_file": str(raw_path),
            "prompt_file": str(prompt_path),
            "result_file": str(result_path),
            "sent_to_tmux_file": str(sent_path),
        },
    )
    startup_contract_path.write_text(_build_runtime_contract(session_id, current_turn_path), encoding="utf-8")
    payload = _tmux_turn_payload(content)
    sent_path.write_text(payload, encoding="utf-8")

    assistant_msg = {
        "id": f"msg-{uuid.uuid4().hex[:10]}",
        "role": "assistant",
        "content": "已提交给 runtime provider，正在后台执行。",
        "ts": _now(),
        "pending": True,
        "turn_id": turn_id,
    }
    _append_jsonl(path / "messages.jsonl", assistant_msg)
    turn = {
        "turn_id": turn_id,
        "runtime": _read_json(path / "state.json", {}).get("runtime"),
        "pane_id": "",
        "prompt_path": str(prompt_path),
        "raw_user_message_path": str(raw_path),
        "current_turn_path": str(current_turn_path),
        "sent_to_tmux_path": str(sent_path),
        "result_path": str(result_path),
        "assistant_message_id": assistant_msg["id"],
        "state": "queued",
        "created_at": _now(),
    }
    pending = _pending_turns(session_id)
    pending.append(turn)
    _save_pending_turns(session_id, pending)
    events = [{
        "ts": _now(),
        "type": "runtime.queued",
        "session_id": session_id,
        "title": "runtime turn 已入队",
        "status": "queued",
        "data": {
            "turn_id": turn_id,
            "prompt_path": str(prompt_path),
            "raw_user_message_path": str(raw_path),
            "current_turn_path": str(current_turn_path),
            "sent_to_tmux_path": str(sent_path),
            "result_path": str(result_path),
        },
    }]
    for event in events:
        _append_jsonl(path / "events.jsonl", event)
    state = _read_json(path / "state.json", {})
    state["updated_at"] = _now()
    if state.get("title") in {"", "新会话", "图书运营会话"}:
        state["title"] = _session_title_from_message(content)
    _write_json(path / "state.json", state)

    threading.Thread(
        target=_deliver_chat_turn,
        args=(session_id, turn, startup_contract_path, result_path, payload),
        daemon=True,
    ).start()

    completed_events = _wait_for_turn(session_id, turn, wait_seconds) if wait_seconds > 0 else []
    return {
        "message": assistant_msg,
        "messages": _load_messages(session_id),
        "events": events + completed_events,
        "runtime": {"state": "queued"},
        "session": state,
        "operator": operator_view(session_id),
    }


def kb_search(query: str, modality: str = "all", topk: int = 10) -> dict:
    if not query.strip():
        return {"ok": False, "error": "query is required"}
    cmd = [
        sys.executable,
        str(CONTENT_RUNTIME),
        "kb",
        "search",
        "--query",
        query,
        "--modality",
        modality,
        "--topk",
        str(topk),
        "--json",
        "--no-log",
        "--no-touch",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60, check=False)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip(), "rows": []}
    try:
        return {"ok": True, "rows": json.loads(proc.stdout or "[]")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"search JSON parse failed: {exc}", "raw": proc.stdout, "rows": []}


def _provider_label(runtime: str | None) -> str:
    if runtime == "cli":
        return "CLI"
    if runtime == "api":
        return "API"
    if runtime == "tmux":
        return "Tmux"
    return "Runtime"


def _operator_status_label(status: str | None) -> str:
    return {
        "queued": "排队中",
        "starting": "正在启动助手",
        "idle": "助手已就绪",
        "running": "正在处理",
        "waiting_result": "正在整理结果",
        "done": "已完成",
        "failed": "失败",
        "stopped": "已停止",
        "not_started": "未启动",
    }.get(status or "", status or "未启动")


def _friendly_error(error: str | None) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    if "did not become idle before prompt submission" in text:
        return "助手还没有准备好接收这次输入。可以重试，或打开诊断查看 CLI 是否卡在启动页。"
    if "not found" in text and ("codex" in text.lower() or "claude" in text.lower()):
        return "当前助手命令不可用，请到设置里检查 Codex / Claude 是否已安装。"
    if "pane is not running" in text:
        return "底层终端会话已经退出，可以重新发送或新建会话。"
    return text


def _latest_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and str(message.get("content") or "").strip():
            return str(message["content"])
    return ""


def _intent_label(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if any(word in lowered for word in ["同步知识库", "知识库", "ingest", "index"]):
        return "knowledge_sync", "同步知识库"
    if any(word in lowered for word in ["素材", "图片", "视频", "产品资料"]):
        return "asset_prepare", "准备素材"
    if any(word in lowered for word in ["合规", "审核", "检查"]):
        return "compliance_review", "审核合规"
    kind = _classify(text)
    if kind == "content":
        return "content_generate", "生成草稿"
    if kind == "search":
        return "knowledge_search", "检索素材"
    if kind == "design":
        return "design", "整理方案"
    return "chat", "理解需求"


def _latest_event(events: list[dict], *, status: str | None = None, event_type: str | None = None) -> dict | None:
    for event in reversed(events):
        if status and event.get("status") != status:
            continue
        if event_type and event.get("type") != event_type:
            continue
        return event
    return None


def _relative_project_path(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        path = Path(text)
        if path.is_absolute():
            return str(path.resolve().relative_to(ROOT))
    except Exception:  # noqa: BLE001
        return text
    return text


def _output_type(path: str, label: str = "") -> str:
    text = f"{path} {label}".lower()
    if "xiaohongshu" in text or "小红书" in text:
        return "小红书图文"
    if "moments" in text or "朋友圈" in text:
        return "朋友圈文案"
    if "wechat" in text or "群话术" in text or "家长群" in text:
        return "家长群话术"
    if "compliance" in text or "审核" in text:
        return "合规审核报告"
    if "campaign" in text or "活动" in text:
        return "活动计划"
    if "profile" in text or "档案" in text:
        return "图书档案"
    if "knowledge" in text or "sync" in text or "ingest" in text:
        return "知识库同步报告"
    if "script" in text or "video" in text or "短视频" in text:
        return "短视频脚本"
    if "checklist" in text:
        return "发布检查清单"
    if text.endswith(".json"):
        return "数据文件"
    return "运营产出"


def _output_status(path: str, source: dict | None = None) -> str:
    source = source or {}
    explicit = source.get("status")
    if explicit in {"草稿", "待审核", "可手动发布", "需修改", "已归档"}:
        return explicit
    text = path.lower()
    if "compliance" in text or "审核" in text:
        return "待审核"
    if "checklist" in text or "publish" in text:
        return "可手动发布"
    return "草稿"


def _normalize_output_ref(item) -> dict | None:
    if not item:
        return None
    if isinstance(item, str):
        path = item
        label = Path(item).name or item
        source = {}
    elif isinstance(item, dict):
        path = item.get("path") or item.get("file") or item.get("output_path") or item.get("href")
        label = item.get("label") or item.get("title") or item.get("name") or (Path(str(path)).name if path else "")
        source = item
    else:
        return None
    if not path:
        return None
    rel_path = _relative_project_path(str(path))
    label = str(label or Path(rel_path).name or rel_path)
    return {
        "label": label,
        "path": rel_path,
        "type": _output_type(rel_path, label),
        "status": _output_status(rel_path, source),
    }


def _session_output_refs(messages: list[dict], events: list[dict], linked_outputs: list) -> list[dict]:
    refs: list[dict] = []
    for item in linked_outputs or []:
        normalized = _normalize_output_ref(item)
        if normalized:
            refs.append(normalized)
    for message in messages:
        result = message.get("result") or {}
        for item in result.get("outputs") or []:
            normalized = _normalize_output_ref(item)
            if normalized:
                refs.append(normalized)
    for event in events:
        data = event.get("data") or {}
        for item in data.get("outputs") or []:
            normalized = _normalize_output_ref(item)
            if normalized:
                refs.append(normalized)
    deduped = []
    seen = set()
    for ref in refs:
        key = ref["path"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _operator_activity(runtime_status: dict, pending: list[dict], status: str, provider: str) -> str:
    if status == "failed":
        return "任务失败，原始错误已放到诊断。"
    if status == "done":
        return "任务已完成，可以查看产出。"
    if status == "queued":
        return "任务已入队，正在等待助手接收。"
    if not pending:
        return "当前没有运行中的任务。"
    bytes_per_sec = float(runtime_status.get("bytes_per_sec") or 0)
    idle_seconds = runtime_status.get("idle_seconds")
    if bytes_per_sec > 1:
        return f"{provider} 正在输出，最近仍有活动。"
    if idle_seconds is not None:
        idle = int(float(idle_seconds))
        if idle < 120:
            return f"{provider} 已 {idle} 秒无新输出，可能正在思考或整理结果。"
        return f"{provider} 已超过 {idle // 60} 分钟无新输出，可能等待输入或卡住。"
    return f"{provider} 正在处理。"


def _progress_for_session(state: dict, messages: list[dict], events: list[dict],
                          pending: list[dict], runtime_status: dict,
                          outputs: list[dict]) -> dict:
    last_user_text = _latest_user_text(messages)
    intent, default_step = _intent_label(last_user_text)
    runtime = state.get("runtime") or runtime_status.get("runtime") or "tmux"
    provider = _provider_label(runtime)
    last_failed = _latest_event(events, status="failed")
    last_done = _latest_event(events, event_type="runtime.result_ready")
    if pending:
        turn = pending[0]
        raw_status = turn.get("state") or runtime_status.get("state") or "running"
        status = "queued" if raw_status == "queued" else "running"
        current_step = "正在启动助手" if status == "queued" else default_step
        started_at = turn.get("created_at") or state.get("updated_at")
        diagnostic = {
            "session_id": state.get("session_id"),
            "turn_id": turn.get("turn_id"),
            "provider_run_id": runtime_status.get("provider_run_id"),
        }
    elif last_failed:
        status = "failed"
        current_step = "处理失败"
        started_at = state.get("updated_at")
        data = last_failed.get("data") or {}
        diagnostic = {
            "session_id": state.get("session_id"),
            "turn_id": data.get("turn_id"),
            "provider_run_id": runtime_status.get("provider_run_id"),
        }
    elif last_done:
        status = "done"
        current_step = "整理产出"
        started_at = last_done.get("ts") or state.get("updated_at")
        data = last_done.get("data") or {}
        diagnostic = {
            "session_id": state.get("session_id"),
            "turn_id": data.get("turn_id"),
            "provider_run_id": runtime_status.get("provider_run_id"),
        }
    else:
        status = runtime_status.get("state") if runtime_status.get("state") in {"idle", "stopped"} else "not_started"
        current_step = "等待输入"
        started_at = state.get("updated_at")
        diagnostic = {
            "session_id": state.get("session_id"),
            "turn_id": runtime_status.get("current_turn_id"),
            "provider_run_id": runtime_status.get("provider_run_id"),
        }
    raw_error = ""
    if last_failed:
        data = last_failed.get("data") or {}
        raw_error = data.get("error") or "; ".join(str(item) for item in data.get("errors", []) or [])
    if not raw_error:
        raw_error = runtime_status.get("error") or ""
    actions = []
    if status in {"failed", "running", "queued"}:
        actions.append({"label": "打开诊断", "action": "diagnostics"})
    if status in {"running", "queued"}:
        actions.append({"label": "停止任务", "action": "stop_session_runtime", "style": "danger"})
    return {
        "task_id": f"{state.get('session_id', '')}:{diagnostic.get('turn_id') or 'latest'}",
        "title": state.get("title") or _session_title_from_message(last_user_text),
        "intent": intent,
        "status": status,
        "status_label": _operator_status_label(status),
        "current_step": current_step,
        "provider": runtime,
        "provider_label": provider,
        "started_at": started_at,
        "elapsed_seconds": _elapsed_seconds(started_at),
        "activity": _operator_activity(runtime_status, pending, status, provider),
        "action_required": None,
        "friendly_error": _friendly_error(raw_error),
        "raw_error": raw_error,
        "outputs": outputs,
        "actions": actions,
        "diagnostic": diagnostic,
    }


def _settings_summary(config: dict, health_payload: dict) -> dict:
    checks = {item.get("id"): item for item in health_payload.get("checks", [])}
    codex = checks.get("codex", {})
    claude = checks.get("claude", {})
    agentrun = checks.get("agentrun-runtime", {})
    return {
        "chat_provider": config.get("chat_provider"),
        "runtime_provider": config.get("runtime_provider"),
        "chat_provider_label": _provider_label(config.get("chat_provider")),
        "runtime_provider_label": _provider_label(config.get("runtime_provider")),
        "mode": "AgentRun provider task",
        "project_root": str(ROOT),
        "checks": {
            "codex": codex.get("status", "missing"),
            "claude": claude.get("status", "missing"),
            "agentrun_runtime": agentrun.get("status", "missing"),
        },
    }


def operator_view(session_id: str) -> dict:
    path = _session_dir(session_id)
    if not path.exists():
        raise FileNotFoundError("session not found")
    _sync_pending_turns(session_id)
    state = _read_json(path / "state.json", {})
    messages = _load_messages(session_id)
    events = _load_events(session_id)
    pending = _pending_turns(session_id)
    runtime_status = session_runtime_status(session_id)
    linked_outputs = _read_json(path / "linked_outputs.json", [])
    outputs = _session_output_refs(messages, events, linked_outputs)
    health_payload = health.collect_health()
    config = workbench_config()
    return {
        "progress": _progress_for_session(state, messages, events, pending, runtime_status, outputs),
        "materials": [],
        "outputs": outputs,
        "settings_summary": _settings_summary(config, health_payload),
        "diagnostics_ref": {
            "session_id": session_id,
            "runtime_status": runtime_status,
            "event_count": len(events),
            "pending_turn_count": len(pending),
        },
    }


def open_allowed_path(rel_path: str) -> dict:
    path = file_browser.safe_path(rel_path)
    target = path if path.is_dir() else path.parent
    subprocess.run(["open", str(target)], cwd=ROOT, check=False)
    return {"ok": True, "opened": file_browser.rel(target)}


def state_payload() -> dict:
    return {
        "project_root": str(ROOT),
        "sessions": list_sessions(),
        "outputs": file_browser.list_outputs(limit=20)["entries"],
        "runtime_runs": MAIN_RUNTIME.list_runs()[:10],
        "runtime_config": runtime_config_payload(),
        "skills": MAIN_RUNTIME.list_skills(),
    }
