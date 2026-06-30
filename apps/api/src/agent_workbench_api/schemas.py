"""Pydantic request schemas for the workbench API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str = ""
    runtime: str | None = None
    profile: str | None = None


class DeleteSessionsRequest(BaseModel):
    session_ids: list[str] = Field(default_factory=list)


class AddMessageRequest(BaseModel):
    content: str
    wait_seconds: float = 0


class RuntimeConfigRequest(BaseModel):
    chat_provider: str | None = None
    runtime_provider: str | None = None
    chat_profile: str | None = None
    runtime_profile: str | None = None
    codex_command: str | None = None
    claude_command: str | None = None
    codex_no_alt_screen: bool | None = None
    codex_sandbox: str | None = None
    codex_approval: str | None = None
    codex_bypass: bool | None = None
    codex_extra_args: str | None = None
    claude_permission_mode: str | None = None
    claude_skip_permissions: bool | None = None
    claude_extra_args: str | None = None


class RuntimeValidateRequest(BaseModel):
    provider_type: str | None = None
    name: str | None = None
    profile: str | None = None
    profile_id: str | None = None


class OpenFileRequest(BaseModel):
    path: str


class DraftPreviewRequest(BaseModel):
    brief: str
    platform: str = "xiaohongshu"
    style: str = "知识科普"


class StartContentDeliveryWorkflowRequest(BaseModel):
    brief: str
    platform: str = "xiaohongshu"
    style: str = "知识科普"
    sources: list[dict] = Field(default_factory=list)


class ContinueWorkflowRequest(BaseModel):
    action: str
    payload: dict = Field(default_factory=dict)


class CancelWorkflowRequest(BaseModel):
    reason: str = ""


class StartRuntimeRequest(BaseModel):
    runtime: str | None = None
    profile: str | None = None
    prompt: str = "请输出一个简短状态。"
    command: str | None = None
    timeout_seconds: int = 1800


class SendRuntimeRequest(BaseModel):
    text: str
