"""横切:结构化日志 + 脱敏(见 design/07 §3)。

凭证红线:key/token/cookie/private key/完整 JWT 不入日志。这里提供 prompt 摘要
与 secret 脱敏的最小工具;完整 Tracer 在 M2 接入。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger("agentrun")

_SECRET_HINTS = ("key", "token", "secret", "password", "cookie", "authorization")


def prompt_digest(text: str) -> dict[str, Any]:
    """prompt 默认只记来源摘要:字符数 + sha256,不记正文。"""
    return {"chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """对疑似 secret 的键脱敏为 set|missing。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if any(hint in key.lower() for hint in _SECRET_HINTS):
            out[key] = "set" if value else "missing"
        else:
            out[key] = value
    return out
