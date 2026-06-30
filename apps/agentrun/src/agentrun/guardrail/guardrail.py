"""Guardrail:capability / path 越界拦截(机制,见 design/07 §1-§4)。

只提供机制;领域规则靠 policy 注入,不内置。M1 实现 capability 门禁 + 路径归一化校验。
"""
from __future__ import annotations

from pathlib import Path

# 高危 capability(见 design/07 §2)
CAPABILITIES = (
    "input_artifacts",
    "auto_trust_cwd",
    "write_paths",
    "remote_write",
    "delete",
    "git_push",
    "read_secret",
    "shell",
    "daemon",
)


class GuardrailError(PermissionError):
    """护栏拒绝(分层错误,见 design/06 D)。"""


class Guardrail:
    def __init__(
        self,
        capabilities: set[str] | None = None,
        forbidden_actions: set[str] | None = None,
        allowed_roots: list[Path] | None = None,
    ) -> None:
        self.capabilities = set(capabilities or ())
        self.forbidden_actions = set(forbidden_actions or ())
        self.allowed_roots = [r.resolve() for r in (allowed_roots or [])]

    def require(self, capability: str) -> None:
        """要求某 capability,缺失或被 forbidden 则拒绝。"""
        if capability in self.forbidden_actions:
            raise GuardrailError(f"动作被 forbidden_actions 禁止: {capability}")
        if capability not in self.capabilities:
            raise GuardrailError(f"缺少 capability: {capability}")

    def check_path_within(self, path: str | Path) -> Path:
        """路径必须归一化后落在 allowed_roots 内;拒绝 .. / 软链逃逸。"""
        resolved = Path(path).expanduser().resolve()
        if ".." in Path(path).parts:
            raise GuardrailError(f"路径含 ..,拒绝: {path}")
        if not self.allowed_roots:
            raise GuardrailError("未声明 allowed_roots,默认拒绝 runs/ 外写入")
        for root in self.allowed_roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        raise GuardrailError(f"路径越界(不在 allowed_roots 内): {resolved}")
