"""auto_trust_cwd:启动前把 cwd 写进 codex/claude 用户配置(见 design/03 §3.3 / design/07 §2)。

改用户配置是副作用动作,由 capability `auto_trust_cwd` 守门(调用方授予);默认不启用。
"""
from __future__ import annotations

import json
from pathlib import Path


def ensure_trusted_cwd(cwd: Path, integrations: list[str]) -> None:
    normalized = cwd.expanduser().resolve()
    for integration in integrations:
        if integration == "claude":
            _trust_claude(normalized)
        elif integration == "codex":
            _trust_codex(normalized)
        else:
            raise ValueError(f"不支持的 auto_trust_cwd: {integration}")


def _trust_claude(cwd: Path, config_path: Path | None = None) -> None:
    path = config_path or Path.home() / ".claude.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(payload, dict):
        raise ValueError("~/.claude.json 必须是 JSON 对象")
    projects = payload.setdefault("projects", {})
    proj = projects.setdefault(str(cwd), {})
    if proj.get("hasTrustDialogAccepted") is True:
        return
    proj["hasTrustDialogAccepted"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _trust_codex(cwd: Path, config_path: Path | None = None) -> None:
    path = config_path or Path.home() / ".codex" / "config.toml"
    block = f'[projects.{json.dumps(str(cwd))}]\ntrust_level = "trusted"\n'
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if f"[projects.{json.dumps(str(cwd))}]" in existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if existing.endswith("\n\n") or not existing else ("\n" if existing.endswith("\n") else "\n\n")
    path.write_text(existing + sep + block, encoding="utf-8")
