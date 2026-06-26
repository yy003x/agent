#!/usr/bin/env python3
"""FastAPI entrypoint for the personal Agent workbench."""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.api import file_browser
from apps.api import health
from apps.api.schemas import (
    AddMessageRequest,
    CreateSessionRequest,
    DeleteSessionsRequest,
    DraftPreviewRequest,
    OpenFileRequest,
    RuntimeConfigRequest,
    SendRuntimeRequest,
    StartRuntimeRequest,
)
from apps.api.services import workbench
from runtime import model_backends

ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT / "apps" / "web" / "dist"


def _api_error(exc: Exception, status_code: int = HTTPStatus.BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


def _body(model: RuntimeConfigRequest) -> dict[str, Any]:
    return {key: value for key, value in model.model_dump().items() if value is not None}


app = FastAPI(title="个人 Agent 工作台 API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def get_health() -> dict:
    return health.collect_health()


@app.get("/api/state")
def get_state() -> dict:
    return workbench.state_payload()


@app.get("/api/config/runtime")
def get_runtime_config() -> dict:
    return workbench.runtime_config_payload()


@app.post("/api/config/runtime")
def save_runtime_config(payload: RuntimeConfigRequest) -> dict:
    try:
        return workbench.save_workbench_config(_body(payload))
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/model-backends")
def get_model_backends() -> dict:
    return {"model_backends": model_backends.collect_model_backends()}


@app.get("/api/skills")
def get_skills() -> dict:
    return {"skills": workbench.MAIN_RUNTIME.list_skills()}


@app.get("/api/outputs")
def get_outputs() -> dict:
    return file_browser.list_outputs()


@app.get("/api/files")
def get_file(path: str = Query("outputs"), list: bool = Query(False)) -> dict:  # noqa: A002
    try:
        return file_browser.list_dir(path) if list else file_browser.read_file(path)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/files/open")
def open_file(payload: OpenFileRequest) -> dict:
    try:
        return workbench.open_allowed_path(payload.path)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/kb/search")
def search_kb(query: str = "", modality: str = "all", topk: int = 10) -> dict:
    return workbench.kb_search(query, modality, topk)


@app.get("/api/chat/sessions")
def list_chat_sessions() -> dict:
    return {"sessions": workbench.list_sessions()}


@app.post("/api/chat/sessions")
def create_chat_session(payload: CreateSessionRequest) -> dict:
    try:
        return workbench.create_session(payload.title, payload.runtime)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/chat/sessions/delete")
def delete_chat_sessions(payload: DeleteSessionsRequest) -> dict:
    try:
        return workbench.delete_sessions(payload.session_ids)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/chat/sessions/{session_id}")
def get_chat_session(session_id: str) -> dict:
    try:
        return workbench.get_session(session_id)
    except FileNotFoundError as exc:
        raise _api_error(exc, HTTPStatus.NOT_FOUND) from exc
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: str) -> dict:
    try:
        return workbench.delete_session(session_id)
    except FileNotFoundError as exc:
        raise _api_error(exc, HTTPStatus.NOT_FOUND) from exc
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/chat/sessions/{session_id}/messages")
def add_chat_message(session_id: str, payload: AddMessageRequest) -> dict:
    try:
        return workbench.add_message(session_id, payload.content, payload.wait_seconds)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/chat/sessions/{session_id}/message")
def add_chat_message_compat(session_id: str, payload: AddMessageRequest) -> dict:
    return add_chat_message(session_id, payload)


@app.get("/api/chat/sessions/{session_id}/operator")
def get_session_operator(session_id: str) -> dict:
    try:
        return workbench.operator_view(session_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/chat/sessions/{session_id}/runtime")
def get_session_runtime(session_id: str) -> dict:
    try:
        return workbench.session_runtime_status(session_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/chat/sessions/{session_id}/runtime/logs")
def get_session_runtime_logs(session_id: str, max_bytes: int = 40_000) -> dict:
    try:
        return workbench.session_runtime_logs(session_id, max_bytes=max_bytes)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/chat/sessions/{session_id}/runtime/stop")
def stop_session_runtime(session_id: str) -> dict:
    try:
        return workbench.stop_session_runtime(session_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/content/draft")
def draft_preview(payload: DraftPreviewRequest) -> dict:
    return workbench._draft_preview(payload.brief, payload.platform, payload.style)


@app.get("/api/runtime/tmux/runs")
def list_runtime_runs() -> dict:
    return {"runs": workbench.MAIN_RUNTIME.list_runs()}


@app.post("/api/runtime/tmux/runs")
def start_runtime_run(payload: StartRuntimeRequest) -> dict:
    try:
        config = workbench.workbench_config()
        runtime = workbench._valid_runtime(payload.runtime or config["runtime_provider"], config["runtime_provider"])
        return workbench.MAIN_RUNTIME.start_run(
            runtime,
            payload.prompt,
            payload.command or workbench._command_for_runtime(config, runtime),
            int(payload.timeout_seconds),
            workbench._runtime_options_from_config(config),
        )
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/runtime/tmux/runs/{run_id}")
def get_runtime_run(run_id: str) -> dict:
    try:
        return workbench.MAIN_RUNTIME.run_status(run_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.get("/api/runtime/tmux/runs/{run_id}/logs")
def get_runtime_run_logs(run_id: str) -> dict:
    try:
        return workbench.MAIN_RUNTIME.run_logs(run_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/runtime/tmux/runs/{run_id}/send")
def send_runtime_run(run_id: str, payload: SendRuntimeRequest) -> dict:
    try:
        return workbench.MAIN_RUNTIME.send_to_run(run_id, payload.text)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@app.post("/api/runtime/tmux/runs/{run_id}/stop")
def stop_runtime_run(run_id: str) -> dict:
    try:
        return workbench.MAIN_RUNTIME.stop_run(run_id)
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/", response_model=None)
def web_index():
    index = WEB_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"ok": True, "message": "个人 Agent 工作台 API 已启动，前端开发服务默认在 http://127.0.0.1:5173"}
